"""Connecteurs : catalogue, réglages et protocoles (HTTP simulé)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import connectors  # noqa: E402


def fake_http(routes):
    """Fabrique un faux ``_http`` : route (fragment d'URL) -> (status, corps)."""

    def handler(method, url, headers=None, body=None, timeout=10):
        for fragment, response in routes.items():
            if fragment in url:
                value = response(method, url, body) if callable(response) else response
                if isinstance(value, bytes):
                    return 200, value
                if isinstance(value, str):
                    return 200, value.encode("utf-8")
                if isinstance(value, tuple):
                    return value
                return 200, json.dumps(value).encode("utf-8")
        return 404, b"{}"

    return handler


def fake_tls(routes):
    """Faux ``_http_tls`` : route (fragment d'URL) -> (status, corps)."""

    def handler(method, url, headers=None, body=None, timeout=10, context=None):
        for fragment, response in routes.items():
            if fragment in url:
                value = response(method, url, body) if callable(response) else response
                if isinstance(value, bytes):
                    return 200, value
                if isinstance(value, str):
                    return 200, value.encode("utf-8")
                if isinstance(value, tuple):
                    return value
                return 200, json.dumps(value).encode("utf-8")
        return 404, b"{}"

    return handler


DAIKIN_ROUTES = {
    "common/basic_info": "ret=OK,type=aircon,name=Salon,mac=aa:bb:cc",
    "aircon/get_control_info": (
        "ret=OK,pow=1,mode=3,stemp=21.0,shum=0,f_rate=A,f_dir=0"
    ),
    "aircon/get_sensor_info": "ret=OK,htemp=20.5,hhum=45,otemp=8.0,err=0",
    "aircon/set_control_info": "ret=OK",
}


class CatalogTests(unittest.TestCase):
    def test_catalog_lists_the_four_systems(self):
        ids = [item["id"] for item in connectors.catalog()]
        self.assertEqual(ids, ["daikin", "bosch", "netatmo", "mitsubishi"])

    def test_catalog_exposes_fields_without_values(self):
        for item in connectors.catalog():
            self.assertTrue(item["label"])
            self.assertIn(item["status"], ("ready", "pairing"))
            for field in item["fields"]:
                self.assertIn("key", field)
                self.assertIn("label", field)
                self.assertNotIn("value", field)

    def test_every_connector_is_ready(self):
        statuses = {item["id"]: item["status"] for item in connectors.CONNECTORS}
        for cid in ("daikin", "bosch", "netatmo", "mitsubishi"):
            self.assertEqual(statuses[cid], "ready")


class SettingsTests(unittest.TestCase):
    def test_defaults_cover_every_field(self):
        defaults = connectors.default_settings()
        for item in connectors.CONNECTORS:
            self.assertEqual(
                set(defaults[item["id"]]),
                {field["key"] for field in item["fields"]},
            )

    def test_public_settings_masks_secrets(self):
        config = {
            "connectors": {"mitsubishi": {"email": "a@b.c", "password": "s3cret"}}
        }
        public = connectors.public_settings(config)
        self.assertEqual(public["mitsubishi"]["email"], "a@b.c")
        self.assertTrue(public["mitsubishi"]["password_set"])
        self.assertNotIn("password", public["mitsubishi"])
        self.assertFalse(public["bosch"]["password_set"])

    def test_merge_keeps_secret_when_left_empty(self):
        current = {"mitsubishi": {"email": "a@b.c", "password": "s3cret"}}
        merged = connectors.merge_settings(
            {"mitsubishi": {"email": "a@b.c", "password": ""}}, current
        )
        self.assertEqual(merged["mitsubishi"]["password"], "s3cret")

    def test_merge_drops_empty_and_unknown_entries(self):
        self.assertEqual(connectors.merge_settings({"nope": {"x": "1"}}, {}), {})
        self.assertEqual(
            connectors.merge_settings({"bosch": {"host": "", "password": ""}}, {}), {}
        )

    def test_merge_rejects_non_dict(self):
        with self.assertRaises(ValueError):
            connectors.merge_settings("nope", {})


class DaikinTests(unittest.TestCase):
    settings = {"host": "192.168.1.50"}

    def test_snapshot_reads_mode_temp_and_ambient(self):
        with mock.patch.object(connectors, "_http", fake_http(DAIKIN_ROUTES)):
            result = connectors.daikin_snapshot(self.settings)
        self.assertTrue(result["ok"])
        device = result["devices"][0]
        self.assertEqual(device["name"], "Salon")
        self.assertEqual(device["mode"], "heat")
        self.assertEqual(device["temp"], 21.0)
        self.assertEqual(device["ambient"], 20.5)
        self.assertEqual(device["outside"], 8.0)

    def test_apply_sends_the_native_values(self):
        captured = {}

        def setter(method, url, body):
            captured["url"] = url
            return "ret=OK"

        routes = dict(DAIKIN_ROUTES)
        routes["aircon/set_control_info"] = setter
        with mock.patch.object(connectors, "_http", fake_http(routes)):
            connectors.daikin_apply(self.settings, "cool", 24)
        self.assertIn("mode=2", captured["url"])
        self.assertIn("stemp=24.0", captured["url"])
        self.assertIn("pow=1", captured["url"])

    def test_off_mode_cuts_the_power(self):
        captured = {}

        def setter(method, url, body):
            captured["url"] = url
            return "ret=OK"

        routes = dict(DAIKIN_ROUTES)
        routes["aircon/set_control_info"] = setter
        with mock.patch.object(connectors, "_http", fake_http(routes)):
            connectors.daikin_apply(self.settings, "off", 16)
        self.assertIn("pow=0", captured["url"])

    def test_missing_host_is_reported(self):
        with self.assertRaises(ValueError):
            connectors.daikin_snapshot({})


class DisabledDeviceTests(unittest.TestCase):
    """Appareils coupés depuis l'interface : marqués et non pilotables."""

    def test_snapshot_marks_disabled_devices(self):
        settings = {"host": "1.2.3.4", "disabled_devices": "aa:bb:cc"}
        with mock.patch.object(connectors, "_http", fake_http(DAIKIN_ROUTES)):
            result = connectors.snapshot("daikin", settings)
        self.assertFalse(result["devices"][0]["enabled"])

    def test_snapshot_keeps_other_devices_enabled(self):
        with mock.patch.object(connectors, "_http", fake_http(DAIKIN_ROUTES)):
            result = connectors.snapshot("daikin", {"host": "1.2.3.4"})
        self.assertTrue(result["devices"][0]["enabled"])

    def test_apply_refuses_disabled_device(self):
        settings = {"host": "1.2.3.4", "disabled_devices": "aa:bb:cc"}
        with self.assertRaises(ValueError) as ctx:
            connectors.apply_state("daikin", settings, "aa:bb:cc", "heat", 20)
        self.assertIn("désactivé", str(ctx.exception).lower())

    def test_disabled_brand_makes_no_network_call(self):
        settings = {"host": "1.2.3.4", "enabled": "0"}

        def boom(*args, **kwargs):
            raise AssertionError("aucun appel API attendu")

        with mock.patch.object(connectors, "_http", boom):
            result = connectors.snapshot("daikin", settings)
            test = connectors.test_connector("daikin", {"daikin": settings})
        self.assertEqual(result["devices"], [])
        self.assertFalse(test["ok"])
        self.assertIn("désactivé", test["error"])

    def test_apply_refuses_disabled_brand(self):
        settings = {"host": "1.2.3.4", "enabled": "0"}
        with self.assertRaises(ValueError) as ctx:
            connectors.apply_state("daikin", settings, "aa:bb:cc", "heat", 20)
        self.assertIn("désactivé", str(ctx.exception).lower())


class MelCloudTests(unittest.TestCase):
    settings = {"email": "a@b.c", "password": "secret"}

    def routes(self):
        return {
            "Login/ClientLogin": {"LoginData": {"ContextKey": "ctx"}},
            "User/ListDevices": [
                {
                    "DeviceID": 7,
                    "DeviceName": "Clim",
                    "Power": True,
                    "OperationMode": 3,
                    "SetTemperature": 22,
                    "RoomTemperature": 21.5,
                    "DeviceOnline": True,
                }
            ],
            "Device/SetAta": {"ok": True},
        }

    def test_snapshot_lists_devices(self):
        with mock.patch.object(connectors, "_http", fake_http(self.routes())):
            result = connectors.melcloud_snapshot(self.settings)
        device = result["devices"][0]
        self.assertEqual(device["name"], "Clim")
        self.assertEqual(device["mode"], "cool")
        self.assertEqual(device["temp"], 22.0)

    def test_apply_sends_operation_mode(self):
        captured = {}

        def setter(method, url, body):
            captured["body"] = json.loads(body.decode("utf-8"))
            return {"ok": True}

        routes = self.routes()
        routes["Device/SetAta"] = setter
        with mock.patch.object(connectors, "_http", fake_http(routes)):
            connectors.melcloud_apply(self.settings, 7, "heat", 20)
        self.assertEqual(captured["body"]["OperationMode"], 1)
        self.assertEqual(captured["body"]["SetTemperature"], 20.0)
        self.assertTrue(captured["body"]["Power"])

    def test_missing_credentials_is_reported(self):
        with self.assertRaises(ValueError):
            connectors.melcloud_snapshot({})


class NetatmoTests(unittest.TestCase):
    settings = {
        "client_id": "id",
        "client_secret": "secret",
        "refresh_token": "refresh",
    }

    def routes(self):
        return {
            "oauth2/token": {"access_token": "tok"},
            "/api/homesdata": {"body": {"homes": [{"id": "home1"}]}},
            "/api/homestatus": {
                "body": {
                    "home": {
                        "rooms": [
                            {
                                "id": "r1",
                                "name": "Salon",
                                "therm_setpoint_temperature": 19,
                                "therm_measured_temperature": 18.5,
                                "therm_setpoint_mode": "manual",
                                "reachable": True,
                            }
                        ]
                    }
                }
            },
            "setroomthermpoint": {"status": "ok"},
        }

    def test_snapshot_lists_rooms(self):
        with mock.patch.object(connectors, "_http", fake_http(self.routes())):
            result = connectors.netatmo_snapshot(self.settings)
        device = result["devices"][0]
        self.assertEqual(device["id"], "home1:r1")
        self.assertEqual(device["name"], "Salon")
        self.assertEqual(device["temp"], 19.0)

    def test_apply_sends_manual_setpoint(self):
        captured = {}

        def setter(method, url, body):
            captured["body"] = json.loads(body.decode("utf-8"))
            return {"status": "ok"}

        routes = self.routes()
        routes["setroomthermpoint"] = setter
        with mock.patch.object(connectors, "_http", fake_http(routes)):
            connectors.netatmo_apply(self.settings, "home1:r1", "heat", 20.5)
        self.assertEqual(captured["body"]["room_id"], "r1")
        self.assertEqual(captured["body"]["mode"], "manual")
        self.assertEqual(captured["body"]["temp"], 20.5)

    def test_missing_token_is_reported(self):
        with self.assertRaises(ValueError):
            connectors.netatmo_snapshot({})


BOSCH_DEVICES = [
    {
        "@type": "device",
        "id": "roomClimateControl_hz_1",
        "name": "Salon",
        "deviceModel": "ROOM_CLIMATE_CONTROL",
        "status": "AVAILABLE",
        "deviceServiceIds": ["RoomClimateControl", "TemperatureLevel"],
    },
    {
        "@type": "device",
        "id": "hdm:ZigBee:00aa",
        "name": "Détecteur porte",
        "deviceModel": "SWD",
        "status": "AVAILABLE",
        "deviceServiceIds": ["TemperatureLevel"],
    },
]
BOSCH_CLIMATE = {
    "@type": "DeviceServiceData",
    "state": {
        "@type": "climateControlState",
        "setpointTemperature": 21.0,
        "roomControlMode": "HEATING",
        "operationMode": "AUTOMATIC",
    },
}
BOSCH_THERMAL = {"@type": "DeviceServiceData", "state": {"temperature": 20.5}}


class BoschTests(unittest.TestCase):
    settings = {"host": "192.168.1.20", "port": "8444", "password": "secret"}

    def setUp(self):
        patches = [
            mock.patch.object(
                connectors, "_bosch_credentials",
                lambda settings: ("cert.pem", "key.pem"),
            ),
            mock.patch.object(
                connectors, "_bosch_certificate",
                lambda path: "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
            ),
            mock.patch.object(
                connectors, "_bosch_ssl_context", lambda cert, key: None
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def routes(self):
        return {
            "/devices/roomClimateControl_hz_1/services/RoomClimateControl": BOSCH_CLIMATE,
            "/devices/roomClimateControl_hz_1/services/TemperatureLevel": BOSCH_THERMAL,
            "/devices": BOSCH_DEVICES,
        }

    def test_snapshot_reads_setpoint_and_ambient(self):
        with mock.patch.object(connectors, "_http_tls", fake_tls(self.routes())):
            result = connectors.bosch_snapshot(self.settings)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["devices"]), 1)
        device = result["devices"][0]
        self.assertEqual(device["id"], "roomClimateControl_hz_1")
        self.assertEqual(device["name"], "Salon")
        self.assertEqual(device["temp"], 21.0)
        self.assertEqual(device["ambient"], 20.5)
        self.assertEqual(device["mode"], "heat")
        self.assertTrue(device["online"])

    def test_snapshot_pairs_client_when_unknown(self):
        state = {"reads": 0}
        routes = self.routes()

        def devices(method, url, body):
            state["reads"] += 1
            return (403, b'{"message":"unauthorized"}') if state["reads"] == 1 else BOSCH_DEVICES

        routes["/devices"] = devices
        routes["/clients"] = (201, b"")
        with mock.patch.object(connectors, "_http_tls", fake_tls(routes)):
            result = connectors.bosch_snapshot(self.settings)
        self.assertTrue(result["ok"])
        self.assertEqual(state["reads"], 2)

    def test_pairing_sends_credentials_on_neighbour_port(self):
        seen = {}
        routes = self.routes()

        def clients(method, url, body):
            seen["url"] = url
            seen["payload"] = json.loads(body.decode("utf-8"))
            return 201, b""

        calls = {"reads": 0}

        def devices(method, url, body):
            calls["reads"] += 1
            return (403, b"{}") if calls["reads"] == 1 else BOSCH_DEVICES

        routes["/devices"] = devices
        routes["/clients"] = clients
        with mock.patch.object(connectors, "_http_tls", fake_tls(routes)):
            connectors.bosch_snapshot(self.settings)
        self.assertIn(":8443/smarthome/clients", seen["url"])
        self.assertEqual(seen["payload"]["id"], "oss_ecasa")
        self.assertEqual(seen["payload"]["primaryRole"], "ROLE_RESTRICTED_CLIENT")
        self.assertTrue(
            seen["payload"]["certificate"].startswith("-----BEGIN CERTIFICATE-----\r")
        )

    def test_pairing_reports_wrong_password(self):
        routes = self.routes()
        routes["/devices"] = lambda method, url, body: (403, b"{}")
        routes["/clients"] = (401, b'{"message":"unauthorized"}')
        with mock.patch.object(connectors, "_http_tls", fake_tls(routes)):
            with self.assertRaises(ValueError) as ctx:
                connectors.bosch_snapshot(self.settings)
        self.assertIn("mot de passe", str(ctx.exception).lower())

    def test_pairing_asks_for_the_hardware_button(self):
        routes = self.routes()
        routes["/devices"] = lambda method, url, body: (403, b"{}")
        routes["/clients"] = (400, b'{"message":"button not pressed"}')
        with mock.patch.object(connectors, "_http_tls", fake_tls(routes)):
            with self.assertRaises(ValueError) as ctx:
                connectors.bosch_snapshot(self.settings)
        self.assertIn("bouton", str(ctx.exception).lower())

    def test_missing_password_is_reported(self):
        with self.assertRaises(ValueError) as ctx:
            connectors.bosch_snapshot({"host": "192.168.1.20"})
        self.assertIn("mot de passe", str(ctx.exception).lower())

    def test_missing_host_is_reported(self):
        with self.assertRaises(ValueError) as ctx:
            connectors.bosch_snapshot({"password": "secret"})
        self.assertIn("adresse", str(ctx.exception).lower())

    def _apply(self, mode, temp, device_id="roomClimateControl_hz_1"):
        captured = {}
        routes = self.routes()

        def climate(method, url, body):
            if method == "PUT":
                captured["body"] = json.loads(body.decode("utf-8"))
                return {"ok": True}
            return BOSCH_CLIMATE

        routes["/devices/roomClimateControl_hz_1/services/RoomClimateControl"] = climate
        with mock.patch.object(connectors, "_http_tls", fake_tls(routes)):
            connectors.bosch_apply(self.settings, device_id, mode, temp)
        return captured["body"]

    def test_apply_sends_setpoint_and_mode(self):
        body = self._apply("heat", 20.5)
        self.assertEqual(body["setpointTemperature"], 20.5)
        self.assertEqual(body["operationMode"], "MANUAL")
        self.assertEqual(body["roomControlMode"], "HEATING")
        self.assertEqual(body["@type"], "climateControlState")

    def test_apply_cooling_mode(self):
        self.assertEqual(self._apply("cool", 23)["roomControlMode"], "COOLING")

    def test_apply_off_uses_frost_protection(self):
        self.assertEqual(self._apply("off", 20)[ "setpointTemperature"], 5.0)

    def test_apply_clamps_temperature(self):
        self.assertEqual(self._apply("heat", 35)["setpointTemperature"], 30.0)

    def test_apply_rejects_unknown_device(self):
        with self.assertRaises(ValueError) as ctx:
            self._apply("heat", 20, device_id="absent")
        self.assertIn("inconnu", str(ctx.exception).lower())

    def test_apply_rejects_device_without_climate_service(self):
        with self.assertRaises(ValueError) as ctx:
            self._apply("heat", 20, device_id="hdm:ZigBee:00aa")
        self.assertIn("consigne", str(ctx.exception).lower())


class TestConnectorTests(unittest.TestCase):
    def test_unknown_connector(self):
        result = connectors.test_connector("nope", {})
        self.assertFalse(result["ok"])
        self.assertIn("inconnu", result["error"])

    def test_daikin_via_test_connector(self):
        with mock.patch.object(connectors, "_http", fake_http(DAIKIN_ROUTES)):
            result = connectors.test_connector("daikin", {"daikin": {"host": "1.2.3.4"}})
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["devices"]), 1)

    def test_network_error_is_reported(self):
        def boom(*args, **kwargs):
            raise OSError("réseau indisponible")

        with mock.patch.object(connectors, "_http", boom):
            result = connectors.test_connector("daikin", {"daikin": {"host": "1.2.3.4"}})
        self.assertFalse(result["ok"])
        self.assertIn("injoignable", result["error"])

    def test_bosch_requires_a_password(self):
        result = connectors.test_connector("bosch", {"bosch": {"host": "1.2.3.4"}})
        self.assertFalse(result["ok"])
        self.assertIn("mot de passe", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
