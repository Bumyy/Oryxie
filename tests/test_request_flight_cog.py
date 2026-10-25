import unittest

from cogs.request_flight_cog import (
    RouteStartSelect,
    _current_assignments_for_pilot,
)


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

    def test_current_assignments_omit_aircraft_outside_current_fleet(self):
        schedules = [
            {
                "id": "retired-route",
                "aircraft_id": "retired-aircraft",
                "status": 1,
                "sequence": 1,
                "crew": [{"userId": "if-pilot"}],
            },
            {
                "id": "current-route",
                "aircraft_id": "current-aircraft",
                "status": 1,
                "sequence": 1,
                "crew": [{"userId": "if-pilot"}],
            },
        ]

        assignments = _current_assignments_for_pilot(
            schedules,
            "if-pilot",
            {"current-aircraft": "A7QRV"},
        )

        self.assertEqual(
            set(assignments),
            {"current-aircraft"},
        )
        self.assertEqual(
            [route["id"] for route in assignments["current-aircraft"]],
            ["current-route"],
        )


if __name__ == "__main__":
    unittest.main()
