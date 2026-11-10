import asyncio
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import web_app
import web_database


class PostgresConnectionTests(unittest.TestCase):
    def test_execute_translates_sqlite_placeholders(self):
        class FakeConnection:
            def __init__(self):
                self.calls = []

            def execute(self, query, parameters):
                self.calls.append((query, parameters))

            def executemany(self, query, parameters):
                self.calls.append((query, parameters))

        connection = FakeConnection()
        adapter = web_database.PostgresConnection(connection)
        adapter.execute("SELECT * FROM users WHERE id = ?", (1,))
        adapter.executemany("UPDATE users SET name = ? WHERE id = ?", [("Awa", 1)])

        self.assertEqual(
            connection.calls,
            [
                ("SELECT * FROM users WHERE id = %s", (1,)),
                ("UPDATE users SET name = %s WHERE id = %s", [("Awa", 1)]),
            ],
        )


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        web_app.DATABASE_PATH = Path(self.temp_dir.name) / "finmee.db"
        self.previous_cookie_secure = web_app.COOKIE_SECURE
        web_app.COOKIE_SECURE = False
        web_database.initialize(web_app.DATABASE_PATH)
        self.client = TestClient(web_app.app)
        self.client.__enter__()
        self.client.get("/api/csrf")
        self.register_account(self.client, "Test User", "user@example.com")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        web_app.COOKIE_SECURE = self.previous_cookie_secure
        self.temp_dir.cleanup()

    def csrf_headers(self, client=None):
        client = client or self.client
        return {"X-CSRF-Token": client.cookies["depenses_csrf"]}

    def register_account(self, client, name, email):
        return client.post(
            "/api/auth/register",
            json={"name": name, "email": email, "password": "CorrectHorse!42"},
            headers=self.csrf_headers(client),
        )

    def test_vercel_requires_a_persistent_database_url(self):
        async def run_lifespan():
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                async with web_app.lifespan(web_app.app):
                    self.fail("Lifespan should not start without PostgreSQL.")

        with patch.dict("os.environ", {"VERCEL": "1"}), patch.object(
            web_app, "DATABASE_URL", None
        ), patch.object(web_database, "initialize") as initialize:
            asyncio.run(run_lifespan())
            initialize.assert_not_called()

    def test_financial_api_requires_an_authenticated_account(self):
        with TestClient(web_app.app) as anonymous:
            anonymous.get("/api/csrf")
            self.assertEqual(anonymous.get("/api/auth/me").status_code, 401)
            self.assertEqual(anonymous.get("/api/dashboard").status_code, 401)
            self.assertEqual(anonymous.get("/api/transactions").status_code, 401)
            self.assertEqual(anonymous.get("/api/profile").status_code, 401)

    def test_registration_validates_email_and_password(self):
        invalid_email = self.client.post(
            "/api/auth/register",
            json={
                "name": "New User",
                "email": "not-an-email",
                "password": "CorrectHorse!42",
            },
            headers=self.csrf_headers(),
        )
        self.assertEqual(invalid_email.status_code, 422)
        weak_password = self.client.post(
            "/api/auth/register",
            json={"name": "New User", "email": "new@example.com", "password": "short"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(weak_password.status_code, 422)

    def test_accounts_are_private_and_can_log_in_and_out(self):
        account = self.client.get("/api/auth/me").json()
        self.assertEqual(account["email"], "user@example.com")
        self.assertEqual(account["name"], "Test User")
        session_token = self.client.cookies[web_app.SESSION_COOKIE]
        session_cookie = next(
            cookie
            for cookie in self.client.cookies.jar
            if cookie.name == web_app.SESSION_COOKIE
        )
        self.assertTrue(session_cookie.has_nonstandard_attr("HttpOnly"))
        with web_database.connect(web_app.DATABASE_PATH) as connection:
            stored_session = connection.execute(
                "SELECT token_hash FROM sessions WHERE user_id = ?", (account["id"],)
            ).fetchone()
        self.assertEqual(
            stored_session["token_hash"],
            hashlib.sha256(session_token.encode("ascii")).hexdigest(),
        )
        duplicate = self.register_account(self.client, "Another Name", "USER@example.com")
        self.assertEqual(duplicate.status_code, 409)

        with TestClient(web_app.app) as another_client:
            another_client.get("/api/csrf")
            invalid_login = another_client.post(
                "/api/auth/login",
                json={"email": "user@example.com", "password": "wrong-password"},
                headers=self.csrf_headers(another_client),
            )
            self.assertEqual(invalid_login.status_code, 401)
            login = another_client.post(
                "/api/auth/login",
                json={
                    "email": " USER@EXAMPLE.COM ",
                    "password": "CorrectHorse!42",
                },
                headers=self.csrf_headers(another_client),
            )
            self.assertEqual(login.status_code, 200, login.text)
            self.assertEqual(login.json()["id"], account["id"])
            self.assertEqual(
                another_client.post(
                    "/api/auth/logout",
                    json={},
                    headers=self.csrf_headers(another_client),
                ).status_code,
                200,
            )
            self.assertEqual(another_client.get("/api/auth/me").status_code, 401)

    def test_profile_is_saved_for_the_authenticated_user(self):
        self.assertEqual(self.client.get("/api/profile").json(), {"name": "Test User"})
        payload = {"name": "  Awa Traoré  "}
        saved = self.client.put(
            "/api/profile", json=payload, headers=self.csrf_headers()
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json(), {"name": "Awa Traoré"})
        account = self.client.get("/api/auth/me").json()
        self.assertEqual(
            web_database.get_user(web_app.DATABASE_PATH, account["id"])["name"],
            "Awa Traoré",
        )

        reopened_client = TestClient(web_app.app)
        try:
            reopened_client.cookies.update(self.client.cookies)
            self.assertEqual(
                reopened_client.get("/api/profile").json(),
                {"name": "Awa Traoré"},
            )
        finally:
            reopened_client.close()

        invalid = self.client.put(
            "/api/profile", json={"name": "   "}, headers=self.csrf_headers()
        )
        self.assertEqual(invalid.status_code, 422)
        no_csrf = self.client.put("/api/profile", json={"name": "Awa"})
        self.assertEqual(no_csrf.status_code, 403)

    def test_transactions_are_saved_in_the_account_space(self):
        headers = self.csrf_headers()
        income = self.client.post(
            "/api/transactions",
            json={
                "amount": "25000",
                "type": "income",
                "description": "Revenu",
                "operation_date": "2026-03-04",
            },
            headers=headers,
        )
        self.assertEqual(income.status_code, 201, income.text)
        expense = self.client.post(
            "/api/transactions",
            json={
                "amount": "5500",
                "type": "expense",
                "description": "Courses",
                "category": "Alimentation",
                "operation_date": "2026-03-05",
            },
            headers=headers,
        )
        self.assertEqual(expense.status_code, 201, expense.text)
        self.assertEqual(
            self.client.get("/api/dashboard").json()["balance_xof"], 19500
        )

    def test_accounts_cannot_read_each_others_transactions(self):
        created = self.client.post(
            "/api/transactions",
            json={
                "amount": "45000",
                "type": "income",
                "description": "Revenu privé",
                "operation_date": "2026-03-04",
            },
            headers=self.csrf_headers(),
        )
        self.assertEqual(created.status_code, 201)
        with TestClient(web_app.app) as another_client:
            another_client.get("/api/csrf")
            registration = self.register_account(
                another_client, "Another User", "another@example.com"
            )
            self.assertEqual(registration.status_code, 201, registration.text)
            self.assertEqual(
                another_client.get("/api/dashboard").json()["balance_xof"], 0
            )
            self.assertEqual(another_client.get("/api/transactions").json(), [])

        web_database.initialize(web_app.DATABASE_PATH)
        self.assertEqual(self.client.get("/api/dashboard").json()["balance_xof"], 45000)
        self.assertEqual(
            self.client.get("/api/auth/me").json()["email"], "user@example.com"
        )

    def test_import_from_a_multi_account_database_only_reads_legacy_space(self):
        web_database.add_transaction(
            web_app.DATABASE_PATH,
            1,
            6000,
            "income",
            "Ancien revenu local",
            None,
            "2026-03-04",
        )
        account_id = self.client.get("/api/auth/me").json()["id"]
        web_database.add_transaction(
            web_app.DATABASE_PATH,
            account_id,
            99000,
            "income",
            "Autre compte",
            None,
            "2026-03-04",
        )

        rows = web_app._read_legacy_database(Path(web_app.DATABASE_PATH).read_bytes())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["description"], "Ancien revenu local")
        self.assertEqual(rows[0]["amount_xof"], 6000)

    def test_expense_can_make_balance_negative_and_csrf_is_required(self):
        payload = {
            "amount": "32000",
            "type": "expense",
            "description": "Café",
            "category": "Alimentation",
            "operation_date": "2026-03-04",
        }
        income = self.client.post(
            "/api/transactions",
            json={
                "amount": "20000",
                "type": "income",
                "description": "Revenu",
                "operation_date": "2026-03-04",
            },
            headers=self.csrf_headers(),
        )
        self.assertEqual(income.status_code, 201, income.text)
        accepted = self.client.post(
            "/api/transactions", json=payload, headers=self.csrf_headers()
        )
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(
            self.client.get("/api/dashboard").json()["balance_xof"], -12000
        )

        decimal_amount = self.client.post(
            "/api/transactions",
            json={**payload, "amount": "1.5"},
            headers=self.csrf_headers(),
        )
        self.assertEqual(decimal_amount.status_code, 422)

        no_csrf = self.client.post("/api/transactions", json=payload)
        self.assertEqual(no_csrf.status_code, 403)

    def test_import_terminal_history_keeps_nominal_cfa_amounts(self):
        legacy_path = Path(self.temp_dir.name) / "depenses.db"
        connection = sqlite3.connect(legacy_path)
        with connection:
            connection.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY,
                    operation_date TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transactions
                    (operation_date, amount_cents, type, description, category)
                VALUES ('2026-03-04', 12000, 'income', 'Salaire', NULL),
                       ('2026-03-05', 2500, 'expense', 'Courses', 'Alimentation')
                """
            )
        connection.close()
        content = legacy_path.read_bytes()
        first = self.client.post(
            "/api/import",
            files={"file": ("depenses.db", content, "application/octet-stream")},
            headers=self.csrf_headers(),
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["imported"], 2)
        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual(dashboard["balance_xof"], 95)

        second = self.client.post(
            "/api/import",
            files={"file": ("depenses.db", content, "application/octet-stream")},
            headers=self.csrf_headers(),
        )
        self.assertTrue(second.json()["already_imported"])
        self.assertEqual(
            self.client.get("/api/dashboard").json()["balance_xof"], 95
        )

    def test_import_first_web_version_converts_euro_to_xof(self):
        legacy_path = Path(self.temp_dir.name) / "old-web.db"
        connection = sqlite3.connect(legacy_path)
        with connection:
            connection.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    operation_date TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    description TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transactions
                    (user_id, operation_date, amount_cents, type, description)
                VALUES (1, '2026-03-04', 1000, 'income', 'Ancien salaire')
                """
            )
        connection.close()

        response = self.client.post(
            "/api/import",
            files={
                "file": (
                    "old-web.db",
                    legacy_path.read_bytes(),
                    "application/octet-stream",
                )
            },
            headers=self.csrf_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.client.get("/api/dashboard").json()["balance_xof"], 6560
        )

    def test_legacy_accounts_are_merged_into_one_local_user(self):
        legacy_path = Path(self.temp_dir.name) / "old-accounts.db"
        connection = sqlite3.connect(legacy_path)
        with connection:
            connection.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO users (id, email, password_salt, password_hash, created_at)
                VALUES (7, 'old@example.com', X'01', X'02', '2026-01-01')
                """
            )
            connection.execute(
                """
                INSERT INTO users (id, email, password_salt, password_hash, created_at)
                VALUES (8, 'another@example.com', X'03', X'04', '2026-01-02')
                """
            )
            connection.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    operation_date TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transactions
                    (user_id, operation_date, amount_cents, type, description)
                VALUES (7, '2026-01-01', 1000, 'income', 'Ancien revenu'),
                       (8, '2026-01-02', 200, 'income', 'Autre revenu')
                """
            )
            connection.execute(
                "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER, expires_at TEXT)"
            )
            connection.execute(
                "CREATE TABLE otp_challenges (email_hash TEXT, code_hash BLOB)"
            )
            connection.execute(
                "CREATE TABLE otp_requests (ip_hash TEXT, email_hash TEXT)"
            )
        connection.close()

        web_database.initialize(legacy_path)

        connection = sqlite3.connect(legacy_path)
        with connection:
            user_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(users)")
            }
            users = connection.execute("SELECT id, email FROM users").fetchall()
            balance = web_database.get_dashboard(legacy_path, 1)["balance_xof"]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        connection.close()
        self.assertIn("password_hash", user_columns)
        self.assertNotIn("password_salt", user_columns)
        self.assertEqual([tuple(row) for row in users], [(1, "local@finmee.invalid")])
        self.assertEqual(balance, 7872)
        self.assertIn("sessions", tables)
        self.assertNotIn("otp_challenges", tables)
        self.assertNotIn("otp_requests", tables)

    def test_pwa_shell_is_served_with_security_headers_and_xof_manifest(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"auth-screen", response.content)
        self.assertNotIn(b"otp", response.content.lower())
        self.assertIn("Content-Security-Policy", response.headers)
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertIn("XOF", manifest.json()["description"])
        icon = self.client.get("/assets/icon-192.png")
        self.assertEqual(icon.status_code, 200)
        self.assertTrue(icon.content.startswith(b"\x89PNG\r\n\x1a\n"))
        service_worker = self.client.get("/sw.js")
        self.assertEqual(service_worker.headers["Service-Worker-Allowed"], "/")


if __name__ == "__main__":
    unittest.main()
