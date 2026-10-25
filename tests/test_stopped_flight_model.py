import unittest

from database.stopped_flight_model import StoppedFlightModel


class SchemaDatabase:
    def __init__(self, allow_create=True):
        self.tables = set()
        self.columns = set()
        self.allow_create = allow_create
        self.executed = []

    async def fetch_one(self, query, args=None):
        if query.startswith("SHOW TABLES"):
            return {"table": args[0]} if args[0] in self.tables else None
        if query.startswith("SHOW COLUMNS"):
            return {"Field": args[0]} if args[0] in self.columns else None
        raise AssertionError(f"Unexpected query: {query}")

    async def execute(self, query, args=None):
        self.executed.append(query)
        normalized = query.strip()
        if normalized.startswith("CREATE TABLE") and self.allow_create:
            table_name = normalized.split("CREATE TABLE IF NOT EXISTS ", 1)[1].split(
                " ",
                1,
            )[0]
            self.tables.add(table_name)
            if table_name == "if_stopped_flight_incidents":
                self.columns.update(
                    {
                        "eta_warning_attempts",
                        "eta_warning_last_attempt_at",
                    }
                )
            return 1
        if normalized.startswith("ALTER TABLE") and self.allow_create:
            column_name = normalized.split("ADD COLUMN ", 1)[1].split(" ", 1)[0]
            self.columns.add(column_name)
            return 1
        return 0


class StoppedFlightModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_tables_creates_missing_tables_and_eta_columns(self):
        db = SchemaDatabase()
        model = StoppedFlightModel(db)

        await model.ensure_tables()

        self.assertEqual(
            db.tables,
            set(StoppedFlightModel._TABLE_DDL),
        )
        self.assertIn("eta_warning_attempts", db.columns)
        self.assertIn("eta_warning_last_attempt_at", db.columns)

    async def test_ensure_tables_reports_missing_table_when_creation_fails(self):
        db = SchemaDatabase(allow_create=False)
        model = StoppedFlightModel(db)

        with self.assertRaisesRegex(
            RuntimeError,
            "Grant CREATE TABLE permissions",
        ):
            await model.ensure_tables()


if __name__ == "__main__":
    unittest.main()
