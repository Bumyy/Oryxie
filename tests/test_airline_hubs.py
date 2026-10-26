import unittest

from cogs.airline_hubs import hubs_for_registration
from cogs.request_flight_cog import _aircraft_hubs


class AirlineHubTests(unittest.TestCase):
    def test_qantas_hub_is_sydney_only(self):
        self.assertEqual(hubs_for_registration("VH-ZNF"), ("YSSY",))

    def test_american_airlines_hubs_include_requested_airports(self):
        expected = ("KJFK", "KPHL", "KPHX", "KCLT", "KMIA", "KDFW", "KLAX")

        self.assertEqual(hubs_for_registration("N762AN"), expected)
        self.assertEqual(hubs_for_registration("N302NY"), expected)

    def test_latam_hubs_are_gru_and_brasilia_for_shared_and_override_lookups(self):
        expected = ("SBGR", "SBBR")

        self.assertEqual(hubs_for_registration("IF-MPSK"), expected)
        self.assertEqual(_aircraft_hubs({"registration": "PT-LBN"}), expected)
        self.assertEqual(_aircraft_hubs({"registration": "IF-MPSK"}), expected)


if __name__ == "__main__":
    unittest.main()
