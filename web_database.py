import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


MAX_AMOUNT_XOF = 1_000_000_000_000_000
EUR_TO_XOF = Decimal("655.957")


@contextmanager
def connect(db_path):
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                operation_date TEXT NOT NULL,
                amount_xof INTEGER NOT NULL CHECK (amount_xof > 0),
                type TEXT NOT NULL CHECK (type IN ('income', 'expense')),
                description TEXT NOT NULL CHECK (length(trim(description)) > 0),
                category TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_web_transactions_user_date
                ON transactions(user_id, operation_date, id);
            CREATE TABLE IF NOT EXISTS imports (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                file_hash TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (user_id, file_hash)
            );
            CREATE TABLE IF NOT EXISTS local_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            """
        )
        user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        legacy_password_columns = {
            column for column in ("password_salt", "password_hash")
            if column in user_columns
        }
        if legacy_password_columns:
            for column in legacy_password_columns:
                connection.execute(f"ALTER TABLE users DROP COLUMN {column}")
        connection.execute("DROP TABLE IF EXISTS login_attempts")
        connection.execute("DROP TABLE IF EXISTS registration_attempts")
        connection.execute(
            """
            UPDATE users
            SET email = 'legacy-' || id || '@finmee.invalid'
            WHERE id != 1 AND email = 'local@finmee.invalid'
            """
        )
        connection.execute(
            """
            INSERT INTO users (id, email, created_at)
            VALUES (1, 'local@finmee.invalid', ?)
            ON CONFLICT(id) DO UPDATE SET email = excluded.email
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        transaction_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(transactions)")
        }
        if "amount_cents" in transaction_columns:
            connection.execute(
                "ALTER TABLE transactions RENAME COLUMN amount_cents TO amount_xof"
            )
            rows = connection.execute(
                "SELECT id, amount_xof FROM transactions"
            ).fetchall()
            converted = []
            for row in rows:
                amount = (
                    Decimal(row["amount_xof"]) * EUR_TO_XOF / 100
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if not 0 < amount <= MAX_AMOUNT_XOF:
                    raise ValueError(
                        "Une ancienne opération dépasse le montant maximum en FCFA."
                    )
                converted.append((int(amount), row["id"]))
            connection.executemany(
                "UPDATE transactions SET amount_xof = ? WHERE id = ?", converted
            )
        connection.execute("UPDATE transactions SET user_id = 1")
        connection.execute(
            """
            INSERT OR IGNORE INTO imports (user_id, file_hash, imported_at)
            SELECT 1, file_hash, imported_at FROM imports
            """
        )
        connection.execute("DELETE FROM imports WHERE user_id != 1")
        connection.execute("DROP TABLE IF EXISTS otp_challenges")
        connection.execute("DROP TABLE IF EXISTS otp_requests")
        connection.execute("DROP TABLE IF EXISTS sessions")
        connection.execute("DELETE FROM users WHERE id != 1")
        connection.execute(
            """
            INSERT OR IGNORE INTO local_profile (id, name, updated_at)
            VALUES (1, '', ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )


def get_local_profile(db_path):
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT name FROM local_profile WHERE id = 1"
        ).fetchone()
    return {"name": row["name"] if row else ""}


def save_local_profile(db_path, name):
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO local_profile (id, name, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                updated_at = excluded.updated_at
            """,
            (name, datetime.now(timezone.utc).isoformat()),
        )


def get_dashboard(db_path, user_id):
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    next_month = (
        (today.replace(day=28) + timedelta(days=4)).replace(day=1).isoformat()
    )
    with connect(db_path) as connection:
        totals = connection.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN type = 'income' THEN amount_xof
                                  ELSE -amount_xof END), 0) AS balance_xof,
                COALESCE(SUM(CASE WHEN type = 'income'
                                      AND operation_date >= ? AND operation_date < ?
                                  THEN amount_xof ELSE 0 END), 0) AS month_income_xof,
                COALESCE(SUM(CASE WHEN type = 'expense'
                                      AND operation_date >= ? AND operation_date < ?
                                  THEN amount_xof ELSE 0 END), 0) AS month_expense_xof
            FROM transactions WHERE user_id = ?
            """,
            (month_start, next_month, month_start, next_month, user_id),
        ).fetchone()
        transactions = connection.execute(
            """
            SELECT operation_date, amount_xof, type, description, category
            FROM transactions WHERE user_id = ?
            ORDER BY operation_date DESC, id DESC LIMIT 8
            """,
            (user_id,),
        ).fetchall()
    return {
        **dict(totals),
        "transactions": [dict(row) for row in transactions],
    }


def add_transaction(db_path, user_id, amount_xof, transaction_type, description,
                    category, operation_date):
    date.fromisoformat(operation_date)
    if not isinstance(amount_xof, int) or not 0 < amount_xof <= MAX_AMOUNT_XOF:
        raise ValueError("Le montant doit être un nombre entier positif en FCFA.")
    if not description or not description.strip() or len(description) > 200:
        raise ValueError("La description doit contenir de 1 à 200 caractères.")
    if transaction_type not in {"income", "expense"}:
        raise ValueError("Type de transaction invalide.")
    if transaction_type == "expense" and (
        not category or not category.strip() or len(category) > 60
    ):
        raise ValueError("Catégorie de dépense invalide.")
    if transaction_type == "income":
        category = None

    with connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if transaction_type == "expense":
            balance = connection.execute(
                """
                SELECT COALESCE(
                    SUM(CASE WHEN type = 'income' THEN amount_xof
                             ELSE -amount_xof END), 0
                ) AS balance_xof
                FROM transactions WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()["balance_xof"]
            if amount_xof > balance:
                raise ValueError("Solde insuffisant pour cette dépense.")
        connection.execute(
            """
            INSERT INTO transactions
                (user_id, operation_date, amount_xof, type, description, category)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                operation_date,
                amount_xof,
                transaction_type,
                description.strip(),
                category,
            ),
        )


def get_history(db_path, user_id):
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT operation_date, amount_xof, type, description, category
            FROM transactions WHERE user_id = ?
            ORDER BY operation_date DESC, id DESC LIMIT 250
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_statistics(db_path, user_id, period):
    period_formats = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}
    if period not in period_formats:
        raise ValueError("Période statistique invalide.")
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT strftime(?, operation_date) AS period,
                   SUM(CASE WHEN type = 'income' THEN amount_xof ELSE 0 END)
                       AS income_xof,
                   SUM(CASE WHEN type = 'expense' THEN amount_xof ELSE 0 END)
                       AS expense_xof
            FROM transactions WHERE user_id = ?
            GROUP BY period ORDER BY period DESC
            """,
            (period_formats[period], user_id),
        ).fetchall()
        categories = connection.execute(
            """
            SELECT category, SUM(amount_xof) AS expense_xof
            FROM transactions WHERE user_id = ? AND type = 'expense'
            GROUP BY category ORDER BY expense_xof DESC, category LIMIT 8
            """,
            (user_id,),
        ).fetchall()
    return {
        "periods": [dict(row) for row in rows],
        "categories": [dict(row) for row in categories],
    }


def import_transactions(db_path, user_id, rows, file_hash):
    with connect(db_path) as connection:
        if connection.execute(
            "SELECT 1 FROM imports WHERE user_id = ? AND file_hash = ?",
            (user_id, file_hash),
        ).fetchone():
            return 0

        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            connection.execute(
                """
                INSERT INTO transactions
                    (user_id, operation_date, amount_xof, type, description, category)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    row["operation_date"],
                    row["amount_xof"],
                    row["type"],
                    row["description"],
                    row["category"],
                ),
            )
        connection.execute(
            "INSERT INTO imports (user_id, file_hash, imported_at) VALUES (?, ?, ?)",
            (user_id, file_hash, datetime.now(timezone.utc).isoformat()),
        )
    return len(rows)
