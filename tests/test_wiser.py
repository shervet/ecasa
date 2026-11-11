"""Tests du client Wiser : lecture et consigne manuelle."""

import unittest
from unittest import mock

from mecasa.wiser import WiserClient


class WiserClientTests(unittest.TestCase):
    def test_set_room_temperature_uses_request_override(self):
        client = WiserClient("http://127.0.0.1:1", "secret")

        with mock.patch.object(client, "_request", return_value={}) as request:
            ok = client.set_room_temperature(3, 20.0)

        self.assertTrue(ok)
        request.assert_called_once_with(
            "PATCH",
            "/data/domain/Room/3",
            {"RequestOverride": {"Type": "Manual", "SetPoint": 200}},
        )


if __name__ == "__main__":
    unittest.main()
