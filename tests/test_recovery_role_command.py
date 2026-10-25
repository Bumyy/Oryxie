import unittest
from types import SimpleNamespace

from discord.app_commands import MissingRole

from cogs.stopped_flight_monitor import (
    RECOVERY_ROLE_SETUP_AUTHORIZED_ROLE_ID,
    StoppedFlightMonitor,
)


class RecoveryRoleCommandTests(unittest.TestCase):
    def test_only_configured_role_passes_setup_command_check(self):
        command = StoppedFlightMonitor.setup_recovery_role
        role_check = command.checks[0]

        authorized_user = SimpleNamespace(
            get_role=lambda role_id: (
                SimpleNamespace(id=role_id)
                if role_id == RECOVERY_ROLE_SETUP_AUTHORIZED_ROLE_ID
                else None
            )
        )
        self.assertTrue(
            role_check(SimpleNamespace(user=authorized_user))
        )

        unauthorized_user = SimpleNamespace(get_role=lambda role_id: None)
        with self.assertRaises(MissingRole):
            role_check(SimpleNamespace(user=unauthorized_user))


if __name__ == "__main__":
    unittest.main()
