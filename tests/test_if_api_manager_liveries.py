import unittest
from unittest.mock import MagicMock

import aiohttp

from api.manager import InfiniteFlightAPIManager


class ErrorResponseContext:
    def __init__(self, error):
        self.error = error

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, error):
        self.error = error
        self.request_count = 0
        self.closed = False

    def request(self, *args, **kwargs):
        self.request_count += 1
        return ErrorResponseContext(self.error)


class InfiniteFlightLiveryTests(unittest.IsolatedAsyncioTestCase):
    def make_manager(self, error):
        manager = InfiniteFlightAPIManager.__new__(InfiniteFlightAPIManager)
        manager.api_key = "test-key"
        manager.base_url = "https://api.infiniteflight.com/public/v2"
        manager._session = FakeSession(error)
        manager._aircraft_without_livery_endpoint = set()
        return manager

    async def test_livery_404_is_cached_and_not_retried(self):
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=404,
            message="Not Found",
        )
        manager = self.make_manager(error)

        self.assertEqual(
            await manager.get_aircraft_liveries("aircraft-id"),
            {"result": []},
        )
        self.assertEqual(
            await manager.get_aircraft_liveries("aircraft-id"),
            {"result": []},
        )
        self.assertEqual(manager._session.request_count, 1)

    async def test_non_livery_404_does_not_populate_livery_cache(self):
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=404,
            message="Not Found",
        )
        manager = self.make_manager(error)

        self.assertIsNone(await manager._request("GET", "/airport/XXXX"))
        self.assertEqual(manager._aircraft_without_livery_endpoint, set())


if __name__ == "__main__":
    unittest.main()
