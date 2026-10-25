import unittest

from database.stopped_flight_model import StoppedFlightModel


class SchemaDatabase:
    def __init__(self, allow_create=True):
        self.tables = set()
        self.columns = set()
        self.allow_create = allow_create
        self.executed = []
        self.recovery_panel = None
        self.write_error = None

    async def fetch_one(self, query, args=None):
        if query.startswith("SHOW TABLES"):
            return {"table": args[0]} if args[0] in self.tables else None
        if query.startswith("SHOW COLUMNS"):
            return {"Field": args[0]} if args[0] in self.columns else None
        if "FROM if_stopped_flight_recovery_role_config" in query:
            return self.recovery_panel
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
        if normalized.startswith(
            "INSERT INTO if_stopped_flight_recovery_role_config"
        ):
            if self.write_error:
                raise self.write_error
            if not self.allow_create:
                return 0
            guild_id, channel_id, message_id, emoji_id = args
            self.recovery_panel = {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "emoji_id": emoji_id,
            }
            return 1
        return 0

    async def execute_or_raise(self, query, args=None):
        return await self.execute(query, args)


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

    async def test_recovery_panel_setup_uses_only_panel_table(self):
        db = SchemaDatabase()
        model = StoppedFlightModel(db)

        await model.ensure_recovery_role_panel_table()
        await model.set_recovery_role_panel(
            "guild-1",
            "channel-1",
            "message-1",
            "emoji-1",
        )

        self.assertEqual(
            db.tables,
            {"if_stopped_flight_recovery_role_config"},
        )
        self.assertEqual(
            db.recovery_panel,
            {
                "guild_id": "guild-1",
                "channel_id": "channel-1",
                "message_id": "message-1",
                "emoji_id": "emoji-1",
            },
        )

    async def test_recovery_panel_reports_write_permission_failure(self):
        db = SchemaDatabase()
        db.tables.add("if_stopped_flight_recovery_role_config")
        db.allow_create = False
        model = StoppedFlightModel(db)

        with self.assertRaisesRegex(RuntimeError, "INSERT/UPDATE permissions"):
            await model.set_recovery_role_panel(
                "guild-1",
                "channel-1",
                "message-1",
                "emoji-1",
            )

    async def test_recovery_panel_preserves_database_write_error(self):
        db = SchemaDatabase()
        db.tables.add("if_stopped_flight_recovery_role_config")
        db.write_error = RuntimeError("INSERT command denied to bot account")
        model = StoppedFlightModel(db)

        with self.assertRaisesRegex(RuntimeError, "INSERT command denied"):
            await model.set_recovery_role_panel(
                "guild-1",
                "channel-1",
                "message-1",
                "emoji-1",
            )


if __name__ == "__main__":
    unittest.main()
