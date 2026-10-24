import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.available_aircraft_cog import (
    _active_schedules as availability_active_schedules,
    _aircraft_category as availability_category,
)
from cogs.request_flight_cog import (
    _active_schedules as request_active_schedules,
    _aircraft_category as request_category,
    _airline_name,
    _model_label,
)
from cogs.stopped_flight_monitor import _has_active_routes
from services.fleet_service import FleetService


class FleetServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_delayed_routes_remain_active_for_availability_and_exhaustion(self):
        delayed_route = {
            "id": "delayed",
            "status": 8,
            "sequence": 1,
            "crew": [],
        }
        arrived_route = {
            "id": "arrived",
            "status": 11,
            "sequence": 2,
            "crew": [],
        }

        self.assertEqual(
            availability_active_schedules([delayed_route]),
            [delayed_route],
        )
        self.assertEqual(
            request_active_schedules([delayed_route]),
            [delayed_route],
        )
        self.assertTrue(_has_active_routes([delayed_route]))
        self.assertFalse(_has_active_routes([arrived_route]))

    async def test_enriches_fleet_from_aircraft_and_livery_database_ids(self):
        aircraft_model = SimpleNamespace(
            get_all_aircraft=AsyncMock(
                return_value=[
                    {
                        "ifaircraftid": "type-777",
                        "ifliveryid": "livery-qatar",
                        "name": "Boeing 777-300ER",
                        "liveryname": "Qatar Airways",
                    },
                    {
                        "ifaircraftid": "type-a380",
                        "ifliveryid": "livery-cargo",
                        "name": "Airbus A380-800",
                        "liveryname": "Qatar Airways Cargo",
                    },
                ]
            )
        )
        bot = SimpleNamespace(aircraft_model=aircraft_model, aircraft_name_map={})
        service = FleetService(bot)
        fleet = [
            SimpleNamespace(aircraft_id="TYPE-777", registration="A7-BAJ"),
            SimpleNamespace(aircraft_id="livery-cargo", registration="A7BFA"),
        ]

        await service.enrich_fleet(fleet)

        self.assertEqual(fleet[0].model, "Boeing 777-300ER")
        self.assertEqual(fleet[0].livery_name, "Qatar Airways")
        self.assertEqual(availability_category(fleet[0]), "Boeing")
        self.assertEqual(request_category(fleet[1]), "Qatar Cargo")
        self.assertEqual(_airline_name(fleet[1]), "Qatar Airways Cargo")
        self.assertIn("Airbus A380-800", _model_label(fleet[1]))
        aircraft_model.get_all_aircraft.assert_awaited_once()

    async def test_registration_models_classify_the_three_unmatched_aircraft(self):
        aircraft_model = SimpleNamespace(
            get_all_aircraft=AsyncMock(return_value=[])
        )
        bot = SimpleNamespace(aircraft_model=aircraft_model, aircraft_name_map={})
        service = FleetService(bot)
        fleet = [
            SimpleNamespace(aircraft_id="unmapped-1", registration="A7-BAJ"),
            SimpleNamespace(aircraft_id="unmapped-2", registration="A7BAG"),
            SimpleNamespace(aircraft_id="unmapped-3", registration="N762AN"),
        ]

        await service.enrich_fleet(fleet)

        self.assertEqual(
            [aircraft.model for aircraft in fleet],
            ["777-300ER", "777-300ER", "777-200ER"],
        )
        self.assertEqual(
            [availability_category(aircraft) for aircraft in fleet],
            ["Boeing", "Boeing", "Boeing"],
        )
        self.assertEqual(
            [request_category(aircraft) for aircraft in fleet],
            ["Boeing", "Boeing", "Boeing"],
        )

    async def test_unmatched_ids_and_registrations_remain_unknown(self):
        aircraft_model = SimpleNamespace(
            get_all_aircraft=AsyncMock(return_value=[])
        )
        bot = SimpleNamespace(aircraft_model=aircraft_model, aircraft_name_map={})
        service = FleetService(bot)
        aircraft = SimpleNamespace(
            aircraft_id="unmapped-type",
            registration="UNMAPPED",
        )

        await service.enrich_fleet([aircraft])

        self.assertEqual(aircraft.model, "")
        self.assertEqual(availability_category(aircraft), "Other")
        self.assertEqual(request_category(aircraft), "Other")
        self.assertIn("Model unknown", _model_label(aircraft))


if __name__ == "__main__":
    unittest.main()
