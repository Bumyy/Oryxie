import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from api.if_live_manager import IFLiveError
from cogs.stopped_flight_monitor import (
    StoppedFlightMonitor,
    _flight_timestamp,
    _recent_schedule_flight_count,
)


class RouteDemandTests(unittest.IsolatedAsyncioTestCase):
    def test_flight_timestamp_accepts_actual_arrival_field(self):
        self.assertEqual(
            _flight_timestamp(
                {"actualArrivalUtc": "2026-10-23T12:30:00Z"}
            ),
            datetime(2026, 10, 23, 12, 30),
        )

    def test_schedule_fallback_counts_only_recent_completed_flights(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - timedelta(days=7)
        schedules = [
            {
                "status": 11,
                "actual_arrival_utc": now.isoformat(),
            },
            {
                "status": 11,
                "actual_arrival_utc": (now - timedelta(days=8)).isoformat(),
            },
            {
                "status": 9,
                "actual_arrival_utc": now.isoformat(),
            },
        ]

        self.assertEqual(_recent_schedule_flight_count(schedules, cutoff), 1)

    async def test_uses_completed_schedules_when_flight_history_api_fails(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        client = type("Client", (), {})()
        client.list_aircraft_flights = AsyncMock(
            side_effect=IFLiveError(403, 403, "Flight history unavailable")
        )
        cog = StoppedFlightMonitor.__new__(StoppedFlightMonitor)
        schedules = [
            {
                "status": 11,
                "actual_arrival_utc": now.isoformat(),
            },
            {
                "status": 11,
                "actual_arrival_utc": (now - timedelta(days=1)).isoformat(),
            },
        ]

        count = await cog._recent_aircraft_flight_count(
            client,
            "aircraft-1",
            schedules,
        )

        self.assertEqual(count, 2)
        client.list_aircraft_flights.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
