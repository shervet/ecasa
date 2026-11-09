"""Tests du diagnostic Tailscale et de la publication HTTPS (sans binaire réel).

Ces tests simulent la sortie de ``tailscale status --json`` et
``tailscale serve status --json`` : aucun accès réseau ni binaire requis.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mecasa import main  # noqa: E402


STATUS_JSON = {
    "BackendState": "Running",
    "TailscaleIPs": ["100.64.0.10"],
    "Self": {
        "DNSName": "mecasa.tailnet-abcd.ts.net.",
        "HostName": "mecasa",
        "TailscaleIPs": ["100.64.0.10"],
    },
    "CurrentTailnet": {
        "MagicDNSSuffix": "tailnet-abcd.ts.net",
        "MagicDNSEnabled": True,
    },
    "CertDomains": ["mecasa.tailnet-abcd.ts.net"],
    "Health": [],
}

SERVE_JSON = {
    "Web": {
        "mecasa.tailnet-abcd.ts.net:443": {
            "Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}},
        },
    },
    "AllowFunnel": {"mecasa.tailnet-abcd.ts.net:443": False},
}


def fake_cmd(responses: dict):
    def _cmd(args, timeout=10):  # noqa: ARG001 - signature de _tailscale_cmd
        key = " ".join(args)
        return responses.get(key, (1, "", f"commande inattendue : {key}"))
    return _cmd


class ServeArgsTests(unittest.TestCase):
    def test_publication_racine_en_https(self):
        args = main.tailscale_serve_args(8780)
        self.assertEqual(args[:2], ["serve", "--bg"])
        self.assertIn("--https=443", args)
        self.assertIn("--set-path=/", args)
        self.assertEqual(args[-1], "http://127.0.0.1:8780")

    def test_port_personnalise(self):
        args = main.tailscale_serve_args(9000)
        self.assertEqual(args[-1], "http://127.0.0.1:9000")


class SummarizeServeTests(unittest.TestCase):
    def test_proxy_racine_detecte(self):
        result = main.summarize_serve_config(json.dumps(SERVE_JSON), 8780)
        self.assertTrue(result["serve_active"])
        self.assertTrue(result["serve_ok"])
        self.assertEqual(result["serve_target"], "http://127.0.0.1:8780")
        self.assertEqual(result["serve_path"], "/")
        self.assertEqual(result["serve_https_port"], 443)
        self.assertEqual(result["serve_url"], "https://mecasa.tailnet-abcd.ts.net/")

    def test_mauvaise_cible_signalee(self):
        payload = {
            "Web": {
                "mecasa.tailnet-abcd.ts.net:443": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:9000"}},
                },
            },
        }
        result = main.summarize_serve_config(json.dumps(payload), 8780)
        self.assertTrue(result["serve_active"])
        self.assertFalse(result["serve_ok"])

    def test_prefere_racine_https_443(self):
        payload = {
            "Web": {
                "mecasa.tailnet-abcd.ts.net:8443": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}},
                },
                "mecasa.tailnet-abcd.ts.net:443": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}},
                },
            },
        }
        result = main.summarize_serve_config(json.dumps(payload), 8780)
        self.assertEqual(result["serve_https_port"], 443)
        self.assertTrue(result["serve_ok"])

    def test_entree_invalide(self):
        for raw in ("", "   ", "pas du json", "{}", '{"Web": null}'):
            result = main.summarize_serve_config(raw, 8780)
            self.assertFalse(result["serve_active"])
            self.assertFalse(result["serve_ok"])


class StatusTests(unittest.TestCase):
    def status(self, responses: dict, **kwargs):
        with mock.patch.object(
            main, "_tailscale_cmd", side_effect=fake_cmd(responses),
        ):
            return main.get_tailscale_status(**kwargs)

    def test_nominal(self):
        responses = {
            "status --json": (0, json.dumps(STATUS_JSON), ""),
            "serve status --json": (0, json.dumps(SERVE_JSON), ""),
        }
        status = self.status(responses, port=8780, hostname="mecasa",
                             local_host="0.0.0.0")
        self.assertTrue(status["installed"])
        self.assertTrue(status["running"])
        self.assertEqual(status["dns_name"], "mecasa.tailnet-abcd.ts.net")
        self.assertEqual(status["https_url"], "https://mecasa.tailnet-abcd.ts.net/")
        self.assertTrue(status["magic_dns_enabled"])
        self.assertEqual(status["magic_dns_suffix"], "tailnet-abcd.ts.net")
        self.assertTrue(status["https_ready"])
        self.assertTrue(status["serve_ok"])
        self.assertEqual(status["warnings"], [])
        self.assertTrue(all(item["ok"] for item in status["checks"]))

    def test_https_et_nom_manquants(self):
        data = json.loads(json.dumps(STATUS_JSON))
        data["CertDomains"] = []
        data["Self"]["DNSName"] = "autre.tailnet-abcd.ts.net."
        responses = {
            "status --json": (0, json.dumps(data), ""),
            "serve status --json": (0, json.dumps(SERVE_JSON), ""),
        }
        status = self.status(responses, port=8780, hostname="mecasa",
                             local_host="0.0.0.0")
        self.assertFalse(status["https_ready"])
        self.assertFalse(status["serve_ok"])
        warnings = " | ".join(status["warnings"])
        self.assertIn("HTTPS Certificates", warnings)
        self.assertIn("autre", warnings)

    def test_hote_local_injoignable(self):
        responses = {
            "status --json": (0, json.dumps(STATUS_JSON), ""),
            "serve status --json": (0, json.dumps(SERVE_JSON), ""),
        }
        status = self.status(responses, port=8780, hostname="mecasa",
                             local_host="192.168.1.10")
        self.assertIn("0.0.0.0", " | ".join(status["warnings"]))

    def test_non_installe(self):
        responses = {
            "status --json": (127, "", "introuvable"),
            "ip -4": (127, "", ""),
            "status": (127, "", ""),
            "serve status --json": (127, "", ""),
            "serve status": (127, "", ""),
        }
        status = self.status(responses, port=8780, hostname="mecasa",
                             local_host="0.0.0.0")
        self.assertFalse(status["installed"])
        self.assertFalse(status["running"])
        self.assertIsNone(status["https_url"])
        self.assertIn("introuvable", " | ".join(status["warnings"]))


class StartupTests(unittest.TestCase):
    def test_desactive_ne_fait_rien(self):
        result = main.apply_tailscale_startup({})
        self.assertFalse(result["attempted"])
        self.assertEqual(result["errors"], [])

    def test_publication_automatique(self):
        config = {
            "tailscale_enabled": True,
            "tailscale_auto_serve": True,
            "tailscale_hostname": "mecasa",
            "tailscale_authkey": "",
            "port": 8780,
            "host": "0.0.0.0",
        }
        status = {"running": True, "https_url": "https://mecasa.ts.net/"}
        with mock.patch.object(
            main, "get_tailscale_status", return_value=status,
        ), mock.patch.object(
            main, "tailscale_serve_enable", return_value={"ok": True},
        ) as serve:
            result = main.apply_tailscale_startup(config)
        serve.assert_called_once_with(8780)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["url"], "https://mecasa.ts.net/")
        self.assertEqual(result["errors"], [])

    def test_renomme_noeud_connecte(self):
        config = {
            "tailscale_enabled": True,
            "tailscale_auto_serve": False,
            "tailscale_hostname": "mecasa",
            "tailscale_authkey": "",
            "port": 8780,
            "host": "0.0.0.0",
        }
        status = {"running": True, "dns_name": "ancien.tailnet.ts.net"}
        with mock.patch.object(
            main, "get_tailscale_status", return_value=status,
        ), mock.patch.object(
            main, "tailscale_up", return_value={"ok": True},
        ) as up:
            result = main.apply_tailscale_startup(config)
        up.assert_called_once_with("", hostname="mecasa")
        self.assertEqual(result["errors"], [])

    def test_connexion_sans_cle_signalee(self):
        config = {
            "tailscale_enabled": True,
            "tailscale_auto_serve": False,
            "tailscale_hostname": "mecasa",
            "tailscale_authkey": "",
            "port": 8780,
            "host": "0.0.0.0",
        }
        with mock.patch.object(
            main, "get_tailscale_status", return_value={"running": False},
        ):
            result = main.apply_tailscale_startup(config)
        self.assertTrue(result["attempted"])
        self.assertTrue(any("clé" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
