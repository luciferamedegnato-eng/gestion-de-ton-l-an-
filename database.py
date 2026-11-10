from contextlib import contextmanager
import sqlite3
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


MAX_AMOUNT_XOF = 1_000_000_000_000_000


class InsufficientBalanceError(ValueError):
    pass


@contextmanager
def _connect(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _validate_transaction(amount_xof, description, operation_date):
    if not isinstance(amount_xof, int) or not 0 < amount_xof <= MAX_AMOUNT_XOF:
        raise ValueError("Le montant en FCFA doit être un nombre entier positif.")
    if not description or not description.strip():
        raise ValueError("La description ne peut pas être vide.")
    date.fromisoformat(operation_date)


def _convert_legacy_amount_to_xof(amount_cents):
    amount_xof = (
        Decimal(amount_cents) / 100
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if not 0 < amount_xof <= MAX_AMOUNT_XOF:
        raise ValueError("Une ancienne opération dépasse le montant maximum en FCFA.")
    return int(amount_xof)


def init_db(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with _connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_date TEXT NOT NULL,
                amount_xof INTEGER NOT NULL CHECK (amount_xof > 0),
                type TEXT NOT NULL CHECK (type IN ('income', 'expense')),
                description TEXT NOT NULL CHECK (length(trim(description)) > 0),
                category TEXT
            )
            """
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(transactions)")
        }
        if "amount_cents" in columns:
            connection.execute(
                "ALTER TABLE transactions RENAME COLUMN amount_cents TO amount_xof"
            )
            rows = connection.execute(
                "SELECT id, amount_xof FROM transactions"
            ).fetchall()
            connection.executemany(
                "UPDATE transactions SET amount_xof = ? WHERE id = ?",
                [
                    (_convert_legacy_amount_to_xof(row["amount_xof"]), row["id"])
                    for row in rows
                ],
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_date "
            "ON transactions(operation_date)"
        )


def get_balance(db_path):
    with _connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT COALESCE(
                SUM(CASE WHEN type = 'income' THEN amount_xof ELSE -amount_xof END),
                0
            ) AS balance_xof
            FROM transactions
            """
        ).fetchone()
    return row["balance_xof"]


def add_income(db_path, amount_xof, description, operation_date=None):
    operation_date = operation_date or date.today().isoformat()
    _validate_transaction(amount_xof, description, operation_date)
    _add_transaction(
        db_path, amount_xof, "income", description, None, operation_date
    )


def add_expense(db_path, amount_xof, description, category, operation_date=None):
    operation_date = operation_date or date.today().isoformat()
    _validate_transaction(amount_xof, description, operation_date)
    if not category or not category.strip():
        raise ValueError("La catégorie ne peut pas être vide.")
    with _connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        balance = connection.execute(
            """
            SELECT COALESCE(
                SUM(CASE WHEN type = 'income' THEN amount_xof ELSE -amount_xof END),
                0
            ) AS balance_xof
            FROM transactions
            """
        ).fetchone()["balance_xof"]
        if amount_xof > balance:
            raise InsufficientBalanceError("Solde insuffisant.")
        connection.execute(
            """
            INSERT INTO transactions
                (operation_date, amount_xof, type, description, category)
            VALUES (?, ?, 'expense', ?, ?)
            """,
            (operation_date, amount_xof, description, category),
        )


def _add_transaction(
    db_path, amount_xof, transaction_type, description, category, operation_date
):
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO transactions
                (operation_date, amount_xof, type, description, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (operation_date, amount_xof, transaction_type, description, category),
        )


def get_history(db_path):
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT operation_date, amount_xof, type, description, category
            FROM transactions
            ORDER BY operation_date DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_statistics(db_path, period):
    period_formats = {"jour": "%Y-%m-%d", "mois": "%Y-%m", "année": "%Y"}
    if period not in period_formats:
        raise ValueError("Période inconnue.")

    with _connect(db_path) as connection:
        periods = connection.execute(
            """
            SELECT strftime(?, operation_date) AS period,
                   SUM(CASE WHEN type = 'income' THEN amount_xof ELSE 0 END)
                       AS income_xof,
                   SUM(CASE WHEN type = 'expense' THEN amount_xof ELSE 0 END)
                       AS expense_xof
            FROM transactions
            GROUP BY period
            ORDER BY period DESC
            """,
            (period_formats[period],),
        ).fetchall()
        categories = connection.execute(
            """
            SELECT category, SUM(amount_xof) AS expense_xof
            FROM transactions
            WHERE type = 'expense'
            GROUP BY category
            ORDER BY expense_xof DESC, category
            """
        ).fetchall()

    return {
        "periods": [dict(row) for row in periods],
        "categories": [dict(row) for row in categories],
    }
