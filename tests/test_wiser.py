"""Tests du client Wiser : lecture et consigne manuelle."""

import unittest
from unittest import mock

from ecasa.wiser import WiserClient, _humidity_percent


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


class HumidityTests(unittest.TestCase):
    def make_client(self):
        return WiserClient("http://127.0.0.1:1", "secret")

    def test_humidity_from_room_stat(self):
        client = self.make_client()
        domain = {
            "Room": [
                {
                    "id": 1,
                    "Name": "Salon",
                    "CalculatedTemperature": 214,
                    "CurrentSetPoint": 200,
                    "RoomStatId": 100,
                }
            ],
            "Device": [
                {"id": 100, "ProductType": "RoomStat", "MeasuredHumidity": 48}
            ],
        }
        with mock.patch.object(client, "get_domain", return_value=domain):
            rooms = client.get_rooms()
        self.assertEqual(rooms[0]["humidity"], 48.0)

    def test_humidity_falls_back_to_room_field(self):
        client = self.make_client()
        domain = {
            "Room": [
                {
                    "id": 2,
                    "Name": "Bureau",
                    "CalculatedTemperature": 196,
                    "Humidity": 455,
                }
            ],
            "Device": [],
        }
        with mock.patch.object(client, "get_domain", return_value=domain):
            rooms = client.get_rooms()
        self.assertEqual(rooms[0]["humidity"], 45.5)

    def test_missing_or_invalid_humidity_is_none(self):
        client = self.make_client()
        domain = {
            "Room": [
                {"id": 3, "CalculatedTemperature": 196, "Humidity": -32768},
                {"id": 4, "CalculatedTemperature": 196, "Humidity": 15000},
            ],
            "Device": [],
        }
        with mock.patch.object(client, "get_domain", return_value=domain):
            rooms = client.get_rooms()
        self.assertIsNone(rooms[0]["humidity"])
        self.assertIsNone(rooms[1]["humidity"])

    def test_percent_normalization(self):
        self.assertEqual(_humidity_percent(48), 48.0)
        self.assertEqual(_humidity_percent(480), 48.0)
        self.assertEqual(_humidity_percent(4800), 48.0)
        self.assertIsNone(_humidity_percent(None))
        self.assertIsNone(_humidity_percent("n/a"))
        self.assertIsNone(_humidity_percent(-5))


if __name__ == "__main__":
    unittest.main()
