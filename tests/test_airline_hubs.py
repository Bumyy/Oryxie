import unittest

from cogs.airline_hubs import hubs_for_registration


class AirlineHubTests(unittest.TestCase):
    def test_qantas_hubs_include_sydney_and_perth(self):
        self.assertEqual(hubs_for_registration("VH-ZNF"), ("YSSY", "YPPH"))


if __name__ == "__main__":
    unittest.main()
