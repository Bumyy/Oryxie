import unittest

from cogs.request_flight_cog import RouteStartSelect


class RouteStartSelectTests(unittest.TestCase):
    def test_builds_route_option_label_for_aircraft_schedule(self):
        route = {
            "id": "route-1",
            "callsign": "QRV123",
            "origin_icao": "OTHH",
            "destination_icao": "EGLL",
        }

        select = RouteStartSelect(
            None,
            {"discordid": "123"},
            {"registration": "A7-APE"},
            [route],
            [(0, [route])],
        )

        self.assertEqual(
            select.options[0].label,
            "QRV123 - OTHH-EGLL",
        )


if __name__ == "__main__":
    unittest.main()
