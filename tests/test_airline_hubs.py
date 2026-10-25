import unittest

from cogs.airline_hubs import hubs_for_registration
from cogs.request_flight_cog import _aircraft_hubs


class AirlineHubTests(unittest.TestCase):
    def test_qantas_hubs_include_sydney_and_perth(self):
        self.assertEqual(hubs_for_registration("VH-ZNF"), ("YSSY", "YPPH"))

    def test_latam_hubs_are_gru_and_brasilia_for_shared_and_override_lookups(self):
        expected = ("SBGR", "SBBR")

        self.assertEqual(hubs_for_registration("IF-MPSK"), expected)
        self.assertEqual(_aircraft_hubs({"registration": "PT-LBN"}), expected)
        self.assertEqual(_aircraft_hubs({"registration": "IF-MPSK"}), expected)


if __name__ == "__main__":
    unittest.main()
