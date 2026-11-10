import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import web_app
import web_database


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

    def tearDown(self):
        self.client.__exit__(None, None, None)
        web_app.COOKIE_SECURE = self.previous_cookie_secure
        self.temp_dir.cleanup()

    def csrf_headers(self):
        return {"X-CSRF-Token": self.client.cookies["depenses_csrf"]}

    def test_api_is_available_without_authentication(self):
        self.assertEqual(self.client.get("/api/dashboard").status_code, 200)
        self.assertEqual(self.client.get("/api/transactions").status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 404)
        self.assertEqual(
            self.client.post("/api/auth/request-code", json={}).status_code, 404
        )

    def test_profile_is_saved_in_sqlite_and_can_be_changed(self):
        self.assertEqual(self.client.get("/api/profile").json(), {"name": ""})
        payload = {"name": "  Awa Traoré  "}
        saved = self.client.put(
            "/api/profile", json=payload, headers=self.csrf_headers()
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json(), {"name": "Awa Traoré"})
        self.assertEqual(
            web_database.get_local_profile(web_app.DATABASE_PATH),
            {"name": "Awa Traoré"},
        )

        reopened_client = TestClient(web_app.app)
        try:
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

    def test_transactions_can_be_created_without_an_account(self):
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

    def test_expense_requires_balance_integer_amount_and_csrf(self):
        payload = {
            "amount": "1",
            "type": "expense",
            "description": "Café",
            "category": "Alimentation",
            "operation_date": "2026-03-04",
        }
        rejected = self.client.post(
            "/api/transactions", json=payload, headers=self.csrf_headers()
        )
        self.assertEqual(rejected.status_code, 422)

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
        self.assertNotIn("password_hash", user_columns)
        self.assertNotIn("password_salt", user_columns)
        self.assertEqual([tuple(row) for row in users], [(1, "local@finmee.invalid")])
        self.assertEqual(balance, 7872)
        self.assertNotIn("sessions", tables)
        self.assertNotIn("otp_challenges", tables)
        self.assertNotIn("otp_requests", tables)

    def test_pwa_shell_is_served_with_security_headers_and_xof_manifest(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"auth-screen", response.content)
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
