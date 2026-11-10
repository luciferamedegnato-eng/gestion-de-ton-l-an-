import tempfile
import unittest
from pathlib import Path

import database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "depenses.db"
        database.init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_income_expense_and_history_are_persisted(self):
        database.add_income(self.db_path, 200000, "Salaire", "2026-03-04")
        database.add_expense(
            self.db_path, 12550, "Courses", "Alimentation", "2026-03-05"
        )

        database.init_db(self.db_path)

        self.assertEqual(database.get_balance(self.db_path), 187450)
        history = database.get_history(self.db_path)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["description"], "Courses")
        self.assertEqual(history[0]["category"], "Alimentation")
        self.assertEqual(history[0]["amount_xof"], 12550)

    def test_expense_cannot_exceed_available_balance(self):
        database.add_income(self.db_path, 10000, "Revenu", "2026-03-04")

        with self.assertRaises(database.InsufficientBalanceError):
            database.add_expense(self.db_path, 10001, "Trop cher", "Autre")

        self.assertEqual(database.get_balance(self.db_path), 10000)
        self.assertEqual(len(database.get_history(self.db_path)), 1)

    def test_statistics_group_by_day_month_and_year(self):
        database.add_income(self.db_path, 200000, "Salaire", "2025-12-31")
        database.add_expense(
            self.db_path, 10000, "Courses", "Alimentation", "2025-12-31"
        )
        database.add_expense(
            self.db_path, 5000, "Bus", "Transport", "2026-01-02"
        )

        daily = database.get_statistics(self.db_path, "jour")
        monthly = database.get_statistics(self.db_path, "mois")
        yearly = database.get_statistics(self.db_path, "année")

        self.assertEqual(len(daily["periods"]), 2)
        self.assertEqual(daily["periods"][0]["period"], "2026-01-02")
        self.assertEqual(daily["periods"][0]["expense_xof"], 5000)
        self.assertEqual(len(monthly["periods"]), 2)
        self.assertEqual(len(yearly["periods"]), 2)
        self.assertEqual(yearly["categories"][0]["category"], "Alimentation")
        self.assertEqual(yearly["categories"][0]["expense_xof"], 10000)

    def test_invalid_statistics_period_is_rejected(self):
        with self.assertRaises(ValueError):
            database.get_statistics(self.db_path, "semaine")

    def test_existing_terminal_database_keeps_amounts_in_whole_cfa(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        import sqlite3

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
                    (operation_date, amount_cents, type, description)
                VALUES ('2025-01-01', 1000, 'income', 'Ancien revenu')
                """
            )
        connection.close()

        database.init_db(legacy_path)

        self.assertEqual(database.get_balance(legacy_path), 10)
        self.assertEqual(database.get_history(legacy_path)[0]["amount_xof"], 10)


if __name__ == "__main__":
    unittest.main()
