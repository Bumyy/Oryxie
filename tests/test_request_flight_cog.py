import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from cogs.request_flight_cog import (
    RouteStartSelect,
    _current_assignments_for_pilot,
    _simbrief_assignment_link,
    _simbrief_assignment_lines,
)
from services.aircraft_classifier import load_aircraft_models
from services.flight_board_service import FlightBoardService


class SimBriefLinkTests(unittest.TestCase):
    def test_all_current_fleet_models_resolve_to_their_fleet_icao_type(self):
        project_root = Path(__file__).resolve().parents[1]
        fleet_frames = json.loads(
            (project_root / "assets" / "fleet_frames.json").read_text(
                encoding="utf-8"
            )
        )
        aircraft_catalog = json.loads(
            (project_root / "assets" / "aircraft_data.json").read_text(
                encoding="utf-8"
            )
        )
        models = load_aircraft_models()
        converter = SimpleNamespace(
            aircraft_db=aircraft_catalog,
        )

        self.assertTrue(set(fleet_frames).issubset(models))
        for registration, frame in fleet_frames.items():
            with self.subTest(registration=registration):
                aircraft_type = FlightBoardService.convert_aircraft_name_to_icao(
                    converter,
                    models[registration],
                )
                self.assertEqual(aircraft_type, frame["icao"])

    def test_builds_prefilled_link_for_first_assigned_route(self):
        class SimBriefService:
            def generate_dispatch_link(
                self,
                origin,
                destination,
                aircraft_type,
                callsign,
            ):
                return (
                    f"{origin}|{destination}|{aircraft_type}|{callsign}"
                )

        class FlightBoardService:
            def convert_aircraft_name_to_icao(self, model):
                self.model = model
                return "B77W"

        class Bot:
            simbrief_service = SimBriefService()
            flight_board_service = FlightBoardService()
            flight_board_service.aircraft_db = {
                "infinite_flight": {"B77W": "Boeing 777-300ER"}
            }

        bot = Bot()

        link = _simbrief_assignment_link(
            bot,
            {"model": "Boeing 777-300ER"},
            {"callsign": "QRV123"},
            {"origin_icao": "othh", "destination_icao": "egll"},
        )

        self.assertEqual(link, "OTHH|EGLL|B77W|QRV123")
        self.assertEqual(
            bot.flight_board_service.model,
            "Boeing 777-300ER",
        )

    def test_skips_link_when_aircraft_type_is_unknown(self):
        class SimBriefService:
            def generate_dispatch_link(self, **kwargs):
                self.generated = True
                return "unexpected"

        class FlightBoardService:
            def convert_aircraft_name_to_icao(self, model):
                return "NG777"

        class Bot:
            simbrief_service = SimBriefService()
            flight_board_service = FlightBoardService()
            flight_board_service.aircraft_db = {
                "infinite_flight": {"B77W": "Boeing 777-300ER"}
            }

        bot = Bot()
        link = _simbrief_assignment_link(
            bot,
            {"model": "Unknown aircraft"},
            {"callsign": "QRV123"},
            {"origin_icao": "OTHH", "destination_icao": "EGLL"},
        )

        self.assertIsNone(link)
        self.assertFalse(
            getattr(bot.simbrief_service, "generated", False),
        )

    def test_uses_registered_a350_1000_type_for_a7_aoa(self):
        class SimBriefService:
            def generate_dispatch_link(
                self,
                origin,
                destination,
                aircraft_type,
                callsign,
            ):
                self.request = (
                    origin,
                    destination,
                    aircraft_type,
                    callsign,
                )
                return "https://simbrief.test/dispatch"

        class FlightBoardService:
            aircraft_db = {
                "infinite_flight": {
                    "A359": "Airbus A350",
                    "A35K": "Airbus A350-1000",
                }
            }

            def convert_aircraft_name_to_icao(self, model):
                return "A350"

        class Bot:
            simbrief_service = SimBriefService()
            flight_board_service = FlightBoardService()

        bot = Bot()
        link = _simbrief_assignment_link(
            bot,
            {"registration": "A7-AOA", "model": "Airbus A350-1000"},
            {"callsign": "QRV123"},
            {"origin_icao": "OTHH", "destination_icao": "LIMC"},
        )

        self.assertEqual(link, "https://simbrief.test/dispatch")
        self.assertEqual(
            bot.simbrief_service.request,
            ("OTHH", "LIMC", "A35K", "QRV123"),
        )

    def test_builds_departure_and_return_links_without_flight_numbers(self):
        class SimBriefService:
            def __init__(self):
                self.requests = []

            def generate_dispatch_link(
                self,
                origin,
                destination,
                aircraft_type,
                callsign,
            ):
                self.requests.append(
                    (origin, destination, aircraft_type, callsign)
                )
                return f"https://simbrief.test/{origin}/{destination}"

        class FlightBoardService:
            aircraft_db = {
                "infinite_flight": {"B77W": "Boeing 777-300ER"}
            }

            def convert_aircraft_name_to_icao(self, model):
                return "B77W"

        class Bot:
            simbrief_service = SimBriefService()
            flight_board_service = FlightBoardService()

        bot = Bot()
        lines = _simbrief_assignment_lines(
            bot,
            {"model": "Boeing 777-300ER"},
            {"callsign": "QRV123"},
            [
                {"origin_icao": "OTHH", "destination_icao": "EGLL"},
                {"origin_icao": "EGLL", "destination_icao": "OTHH"},
            ],
        )

        self.assertEqual(
            bot.simbrief_service.requests,
            [
                ("OTHH", "EGLL", "B77W", "QRV123"),
                ("EGLL", "OTHH", "B77W", "QRV123"),
            ],
        )
        self.assertEqual(
            lines,
            "[Departure FPL](https://simbrief.test/OTHH/EGLL)\n"
            "[Return FPL](https://simbrief.test/EGLL/OTHH)",
        )

    def test_builds_a_link_for_each_connecting_leg(self):
        class SimBriefService:
            def __init__(self):
                self.aircraft_types = []

            def generate_dispatch_link(
                self,
                origin,
                destination,
                aircraft_type,
                callsign,
            ):
                self.aircraft_types.append(aircraft_type)
                return f"https://simbrief.test/{origin}/{destination}"

        class FlightBoardService:
            aircraft_db = {
                "infinite_flight": {"A21N": "Airbus A321neo"}
            }

            def convert_aircraft_name_to_icao(self, model):
                return "A21N" if model == "Airbus A321neo" else "A321"

        class Bot:
            simbrief_service = SimBriefService()
            flight_board_service = FlightBoardService()

        bot = Bot()
        lines = _simbrief_assignment_lines(
            bot,
            {"registration": "PT-LBN", "model": "Airbus A321"},
            {"callsign": "QRV006"},
            [
                {"origin_icao": "SBGR", "destination_icao": "SBBR"},
                {"origin_icao": "SBBR", "destination_icao": "SCEL"},
                {"origin_icao": "SCEL", "destination_icao": "SBGR"},
            ],
        )

        self.assertEqual(
            lines,
            "[Departure FPL](https://simbrief.test/SBGR/SBBR)\n"
            "[FPL for Connecting route SBBR -> SCEL]"
            "(https://simbrief.test/SBBR/SCEL)\n"
            "[Return FPL](https://simbrief.test/SCEL/SBGR)",
        )
        self.assertEqual(bot.simbrief_service.aircraft_types, ["A21N"] * 3)


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
