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

from ecasa import main  # noqa: E402


STATUS_JSON = {
    "BackendState": "Running",
    "TailscaleIPs": ["100.64.0.10"],
    "Self": {
        "DNSName": "ecasa.tailnet-abcd.ts.net.",
        "HostName": "ecasa",
        "TailscaleIPs": ["100.64.0.10"],
    },
    "CurrentTailnet": {
        "MagicDNSSuffix": "tailnet-abcd.ts.net",
        "MagicDNSEnabled": True,
    },
    "CertDomains": ["ecasa.tailnet-abcd.ts.net"],
    "Health": [],
}

SERVE_JSON = {
    "Web": {
        "ecasa.tailnet-abcd.ts.net:443": {
            "Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}},
        },
    },
    "AllowFunnel": {"ecasa.tailnet-abcd.ts.net:443": False},
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
        self.assertEqual(result["serve_url"], "https://ecasa.tailnet-abcd.ts.net/")

    def test_mauvaise_cible_signalee(self):
        payload = {
            "Web": {
                "ecasa.tailnet-abcd.ts.net:443": {
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
                "ecasa.tailnet-abcd.ts.net:8443": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8780"}},
                },
                "ecasa.tailnet-abcd.ts.net:443": {
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
        status = self.status(responses, port=8780, hostname="ecasa",
                             local_host="0.0.0.0")
        self.assertTrue(status["installed"])
        self.assertTrue(status["running"])
        self.assertEqual(status["dns_name"], "ecasa.tailnet-abcd.ts.net")
        self.assertEqual(status["https_url"], "https://ecasa.tailnet-abcd.ts.net/")
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
        status = self.status(responses, port=8780, hostname="ecasa",
                             local_host="0.0.0.0")
        self.assertFalse(status["https_ready"])
        self.assertFalse(status["serve_ok"])
        warnings = " | ".join(status["warnings"])
        self.assertIn("HTTPS Certificates", warnings)
        self.assertIn("autre", warnings)

    def test_aucune_publication_texte_non_vide(self):
        responses = {
            "status --json": (0, json.dumps(STATUS_JSON), ""),
            "serve status --json": (0, "", ""),
            "serve status": (0, "No serve config", ""),
        }
        status = self.status(responses, port=8780, hostname="ecasa",
                             local_host="0.0.0.0")
        self.assertFalse(status["serve_active"])
        self.assertFalse(status["serve_ok"])
        self.assertIsNone(status["serve_url"])

    def test_publication_texte_secours(self):
        responses = {
            "status --json": (0, json.dumps(STATUS_JSON), ""),
            "serve status --json": (0, "", ""),
            "serve status": (0, "https://ecasa.tailnet-abcd.ts.net (tailnet only)", ""),
        }
        status = self.status(responses, port=8780, hostname="ecasa",
                             local_host="0.0.0.0")
        self.assertTrue(status["serve_active"])
        self.assertEqual(status["serve_url"], "https://ecasa.tailnet-abcd.ts.net/")

    def test_hote_local_injoignable(self):
        responses = {
            "status --json": (0, json.dumps(STATUS_JSON), ""),
            "serve status --json": (0, json.dumps(SERVE_JSON), ""),
        }
        status = self.status(responses, port=8780, hostname="ecasa",
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
        status = self.status(responses, port=8780, hostname="ecasa",
                             local_host="0.0.0.0")
        self.assertFalse(status["installed"])
        self.assertFalse(status["running"])
        self.assertIsNone(status["https_url"])
        self.assertIn("introuvable", " | ".join(status["warnings"]))

    def test_conflit_de_nom_par_un_service(self):
        data = json.loads(json.dumps(STATUS_JSON))
        data["Self"]["DNSName"] = "ecasa-1.tailnet-abcd.ts.net."
        data["Self"]["CapMap"] = {
            "services/ecasa": [{
                "Name": "svc:ecasa",
                "Addrs": ["100.91.75.7"],
                "Ports": ["tcp:8790"],
            }],
        }
        responses = {
            "status --json": (0, json.dumps(data), ""),
            "serve status --json": (0, json.dumps(SERVE_JSON), ""),
        }
        status = self.status(responses, port=8780, hostname="ecasa",
                             local_host="0.0.0.0")
        self.assertEqual(status["services"][0]["name"], "svc:ecasa")
        self.assertEqual(status["services"][0]["addrs"], ["100.91.75.7"])
        detail = " | ".join(status["warnings"])
        self.assertIn("Service Tailscale", detail)
        self.assertIn("100.91.75.7", detail)
        self.assertIn("console", detail)

    def test_auth_url_exposee(self):
        data = {
            "BackendState": "NeedsLogin",
            "AuthURL": "https://login.tailscale.com/a/abc123",
            "Self": {"HostName": "ecasa", "TailscaleIPs": []},
        }
        responses = {
            "status --json": (0, json.dumps(data), ""),
            "serve status --json": (1, "", ""),
            "serve status": (1, "", ""),
        }
        status = self.status(responses, port=8780, hostname="ecasa",
                             local_host="0.0.0.0")
        self.assertFalse(status["running"])
        self.assertEqual(status["auth_url"], "https://login.tailscale.com/a/abc123")


class BinaryTests(unittest.TestCase):
    def test_chemin_explicite(self):
        with mock.patch.dict(
            main.os.environ, {"ECASA_TAILSCALE_BIN": r"C:\Tools\tailscale.exe"},
        ), mock.patch.object(
            main, "load_config", return_value={},
        ), mock.patch.object(
            main.shutil, "which", return_value=None,
        ), mock.patch.object(
            main.os.path, "isfile", return_value=True,
        ):
            self.assertEqual(
                main.tailscale_binary(), r"C:\Tools\tailscale.exe",
            )

    def test_detection_automatique_dans_le_path(self):
        with mock.patch.dict(
            main.os.environ, {"ECASA_TAILSCALE_BIN": ""},
        ), mock.patch.object(
            main, "load_config", return_value={},
        ), mock.patch.object(
            main.shutil, "which", return_value="/usr/bin/tailscale",
        ), mock.patch.object(
            main.os.path, "isfile", return_value=True,
        ):
            self.assertEqual(main.tailscale_binary(), "/usr/bin/tailscale")

    def test_absent(self):
        with mock.patch.dict(
            main.os.environ, {"ECASA_TAILSCALE_BIN": ""},
        ), mock.patch.object(
            main, "load_config", return_value={},
        ), mock.patch.object(
            main.shutil, "which", return_value=None,
        ), mock.patch.object(
            main.os.path, "isfile", return_value=False,
        ):
            self.assertIsNone(main.tailscale_binary())

    def test_message_actionnable_si_absent(self):
        with mock.patch.object(main, "tailscale_binary", return_value=None):
            code, _, err = main._tailscale_cmd(["status"])
        self.assertEqual(code, 127)
        self.assertIn("Réglages", err)


class StartupTests(unittest.TestCase):
    def test_desactive_ne_fait_rien(self):
        result = main.apply_tailscale_startup({})
        self.assertFalse(result["attempted"])
        self.assertEqual(result["errors"], [])

    def test_publication_automatique(self):
        config = {
            "tailscale_enabled": True,
            "tailscale_auto_serve": True,
            "tailscale_hostname": "ecasa",
            "tailscale_authkey": "",
            "port": 8780,
            "host": "0.0.0.0",
        }
        status = {"running": True, "https_url": "https://ecasa.ts.net/"}
        with mock.patch.object(
            main, "get_tailscale_status", return_value=status,
        ), mock.patch.object(
            main, "tailscale_serve_enable", return_value={"ok": True},
        ) as serve:
            result = main.apply_tailscale_startup(config)
        serve.assert_called_once_with(8780)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["url"], "https://ecasa.ts.net/")
        self.assertEqual(result["errors"], [])

    def test_renomme_noeud_connecte(self):
        config = {
            "tailscale_enabled": True,
            "tailscale_auto_serve": False,
            "tailscale_hostname": "ecasa",
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
        up.assert_called_once_with("", hostname="ecasa")
        self.assertEqual(result["errors"], [])

    def test_connexion_sans_cle_signalee(self):
        config = {
            "tailscale_enabled": True,
            "tailscale_auto_serve": False,
            "tailscale_hostname": "ecasa",
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


class UpTests(unittest.TestCase):
    def test_nom_deja_pris_signale(self):
        responses = {"up --hostname=ecasa --accept-routes=false": (0, "", "")}
        with mock.patch.object(
            main, "_tailscale_cmd", side_effect=fake_cmd(responses),
        ), mock.patch.object(
            main, "get_tailscale_status",
            return_value={"dns_name": "ecasa-1.tailnet-abcd.ts.net"},
        ):
            result = main.tailscale_up("", hostname="ecasa")
        self.assertTrue(result["ok"])
        self.assertIn("ecasa-1", result["warning"])
        self.assertIn("déjà pris", result["warning"])

    def test_nom_correct_sans_avertissement(self):
        responses = {"up --hostname=ecasa --accept-routes=false": (0, "", "")}
        with mock.patch.object(
            main, "_tailscale_cmd", side_effect=fake_cmd(responses),
        ), mock.patch.object(
            main, "get_tailscale_status",
            return_value={"dns_name": "ecasa.tailnet-abcd.ts.net"},
        ):
            result = main.tailscale_up("", hostname="ecasa")
        self.assertTrue(result["ok"])
        self.assertNotIn("warning", result)


if __name__ == "__main__":
    unittest.main()
