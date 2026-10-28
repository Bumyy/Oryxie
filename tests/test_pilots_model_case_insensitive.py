import unittest

from database.pilots_model import PilotsModel


class QueryRecordingDatabase:
    def __init__(self):
        self.calls = []

    async def fetch_one(self, query, args=None):
        self.calls.append((query, args))
        return {"discordid": "123"}

    async def execute(self, query, args=None):
        self.calls.append((query, args))
        return 1


class PilotIFCUsernameTests(unittest.IsolatedAsyncioTestCase):
    async def test_ifc_lookup_normalizes_username_case(self):
        db = QueryRecordingDatabase()
        model = PilotsModel(db)

        await model.get_pilot_by_ifc_username("H4MZA")

        query, args = db.calls[0]
        self.assertIn("LOWER(ifc) LIKE %s", query)
        self.assertEqual(args, ("%/h4mza%",))

    async def test_ifc_update_normalizes_username_case(self):
        db = QueryRecordingDatabase()
        model = PilotsModel(db)

        await model.update_ifuserid_by_ifc_username("H4MZA", "if-user-id")

        query, args = db.calls[0]
        self.assertIn("LOWER(ifc) LIKE %s", query)
        self.assertEqual(args, ("if-user-id", "%/h4mza%"))


if __name__ == "__main__":
    unittest.main()
