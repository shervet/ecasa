"""Serveur HTTP local MeCasa + API JSON.

Lancement :
    python -m mecasa.main

L'interface est servie sur http://127.0.0.1:8780 par défaut. Tous les fichiers
web sont chargés en mémoire au démarrage : une requête ne touche jamais la
carte SD. Les réponses texte sont compressées en gzip à la volée et servies
avec un ETag.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import mimetypes
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import urllib.request

from . import __version__
from .atlantic import AtlanticClient, AtlanticError, AuthError, MockAtlanticClient
from .energy import (
    ENERGY_VIEWS,
    MAX_HISTORY_DAYS,
    EnergyService,
    build_energy,
    test_energy,
)
from .engine import Engine, _caps, _mode_value, device_name
from .find_wiser import scan_network
from .pronote import (
    STUDENT_STATUSES,
    PronoteError,
    build_plan as build_pronote_plan,
    parse_week_start,
    search_schools,
    test_connection as test_pronote_connection,
)
from .schedule import MODE_LABELS, MODES, WEEKDAY_NAMES, week_type
from .stats import RANGES, build_stats, parse_anchor
from .storage import MemoryStore, dumps
from .weather import WeatherError, WeatherService
from .wiser import MockWiserClient, WiserClient, WiserError

APP_NAME = "MeCasa"
START_TS = time.time()

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"

DEFAULT_CONFIG = {
    "email": "",
    "password": "",
    "host": "0.0.0.0",
    "port": 8780,
    # Autorise les routes sensibles (réglages, Pronote, arrêt/redémarrage)
    # depuis une autre machine du réseau. À réserver aux réseaux de confiance.
    "allow_remote_admin": False,
    "mock": False,
    "wiser_enabled": False,
    "wiser_host": "",
    "wiser_secret": "",
    "pronote_url": "",
    "pronote_username": "",
    "pronote_password": "",
    "pronote_ent": "",
    "pronote_account": "student",
    "pronote_child": "",
    "pronote_travel_minutes": 20,
    "pronote_morning_prep_minutes": 20,
    "pronote_student_status": "externe",
    "pronote_comfort_mode": "heat",
    "pronote_comfort_temp": 20,
    "pronote_reduced_mode": "heat",
    "pronote_reduced_temp": 16,
    "weather_enabled": True,
    "weather_city": "",
    "weather_latitude": 48.8566,
    "weather_longitude": 2.3522,
    # --- Consommation électrique (MyElectricalData / Enedis) ----------------
    "med_enabled": False,
    "med_url": "https://www.myelectricaldata.fr",
    "med_token": "",
    "med_pdl": "",
    # Vide = 3 ans d'historique (limite Enedis : 1095 jours).
    "med_start": "",
    "tailscale_enabled": False,
    "tailscale_auto_serve": False,
    # Nom du nœud dans le tailnet : l'app Android résout mecasa.<tailnet>.ts.net.
    "tailscale_hostname": "mecasa",
    # Chemin du binaire si `tailscale` n'est pas dans le PATH (ex. Windows).
    "tailscale_bin": "",
    "tailscale_authkey": "",
    # --- Stockage basse consommation (spécifique Raspberry Pi) ---------------
    # Snapshot structurel : un changement = une écriture au plus toutes les
    # `structural_delay` secondes.
    "state_file": "data/state.json",
    "structural_delay": 3,
    # Journal horaire append-only : une écriture au plus toutes les
    # `history_interval` secondes, même s'il y a 4 mesures par minute.
    "history_dir": "data/history",
    "history_interval": 900,
    "history_days": 180,
    # Fichiers web chargés en RAM au démarrage.
    "cache_static": True,
    "gzip_enabled": True,
}

MIME_OVERRIDES = {
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".woff2": "font/woff2",
}

_config_cache: dict | None = None
_config_lock = threading.Lock()


# --------------------------------------------------------------------- config


def load_config(force: bool = False) -> dict:
    """Lit config.json une seule fois puis garde le résultat en mémoire."""
    global _config_cache
    with _config_lock:
        if _config_cache is not None and not force:
            return dict(_config_cache)

        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(
                json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"[config] {CONFIG_PATH.name} créé. Renseigne e-mail et mot de passe.")
            _config_cache = dict(DEFAULT_CONFIG)
            return dict(_config_cache)

        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as err:
            raise SystemExit(f"config.json illisible : {err}")

        merged = dict(DEFAULT_CONFIG)
        merged.update(config if isinstance(config, dict) else {})
        _config_cache = merged
        return dict(merged)


def _resolve_path(value: str, default: str) -> Path:
    raw = str(value or default)
    path = Path(raw)
    return path if path.is_absolute() else (ROOT / path)


def _is_loopback_host(host: str) -> bool:
    return str(host or "").strip().lower() in (
        "127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1",
    )


def local_ipv4_addresses() -> list[str]:
    """Adresses IPv4 de cette machine, pour afficher l'URL du réseau local."""
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
        ):
            ip = info[4][0]
            if ip:
                addresses.add(str(ip))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(
        ip for ip in addresses
        if not ip.startswith(("127.", "169.254.", "0."))
    )


def network_urls(config: dict) -> list[str]:
    try:
        port = int(config.get("port", 8780))
    except (TypeError, ValueError):
        port = 8780
    return [f"http://{ip}:{port}" for ip in local_ipv4_addresses()]


_current_weather_cache: dict[tuple, tuple[float, dict]] = {}
_current_weather_lock = threading.Lock()


def _current_weather(latitude: float, longitude: float) -> dict | None:
    """Temps réel Open-Meteo (température + code météo), cache 10 min."""
    key = (round(latitude, 3), round(longitude, 3))
    now = time.time()
    with _current_weather_lock:
        cached = _current_weather_cache.get(key)
        if cached and now - cached[0] < 600:
            return cached[1]

    url = (
        "https://api.open-meteo.com/v1/forecast?"
        + urlencode({
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "current": "temperature_2m,weather_code",
            "timezone": "auto",
        })
    )
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        current = payload.get("current") or {}
        result = {
            "temp": current.get("temperature_2m"),
            "code": current.get("weather_code"),
        }
        if result["temp"] is None:
            return None
    except (OSError, ValueError):
        return None

    with _current_weather_lock:
        _current_weather_cache[key] = (time.time(), result)
        if len(_current_weather_cache) > 32:
            threshold = time.time() - 1200
            _current_weather_cache.update({
                item: value for item, value in _current_weather_cache.items()
                if value[0] > threshold
            })
    return result


def requires_local_admin(path: str) -> bool:
    """Routes sensibles : réglages, Pronote, contrôle serveur, sauvegarde."""
    return path.startswith(
        (
            "/api/config", "/api/control/", "/api/pronote/", "/api/tailscale/",
            "/api/storage/", "/api/wiser/find", "/api/system",
            "/api/energy/test",
        )
    ) or path in ("/api/import", "/api/export")


def public_config(config: dict) -> dict:
    """Vue sans mot de passe : l'interface ne le reçoit jamais."""
    return {
        "email": str(config.get("email", "")),
        "host": str(config.get("host", "127.0.0.1")),
        "port": int(config.get("port", 8780)),
        "lan_enabled": not _is_loopback_host(config.get("host", "127.0.0.1")),
        "network_urls": network_urls(config),
        "allow_remote_admin": bool(config.get("allow_remote_admin", False)),
        "mock": bool(config.get("mock")),
        "password_set": bool(config.get("password")),
        "wiser_enabled": bool(config.get("wiser_enabled")),
        "wiser_host": str(config.get("wiser_host", "")),
        "wiser_secret_set": bool(config.get("wiser_secret")),
        "pronote_url": str(config.get("pronote_url", "")),
        "pronote_username": str(config.get("pronote_username", "")),
        "pronote_ent": str(config.get("pronote_ent", "")),
        "pronote_account": str(config.get("pronote_account", "student")),
        "pronote_child": str(config.get("pronote_child", "")),
        "pronote_password_set": bool(config.get("pronote_password")),
        "pronote_travel_minutes": int(
            20 if config.get("pronote_travel_minutes") is None
            else config["pronote_travel_minutes"]
        ),
        "pronote_morning_prep_minutes": int(
            20 if config.get("pronote_morning_prep_minutes") is None
            else config["pronote_morning_prep_minutes"]
        ),
        "pronote_student_status": str(
            config.get("pronote_student_status") or "externe"
        ),
        "pronote_comfort_mode": str(config.get("pronote_comfort_mode", "heat")),
        "pronote_comfort_temp": float(config.get("pronote_comfort_temp", 20) or 20),
        "pronote_reduced_mode": str(config.get("pronote_reduced_mode", "heat")),
        "pronote_reduced_temp": float(config.get("pronote_reduced_temp", 16) or 16),
        "weather_enabled": bool(config.get("weather_enabled", True)),
        "weather_city": str(config.get("weather_city", "")),
        "weather_latitude": float(config.get("weather_latitude", 48.8566) or 0),
        "weather_longitude": float(config.get("weather_longitude", 2.3522) or 0),
        "med_enabled": bool(config.get("med_enabled", False)),
        "med_url": str(config.get("med_url", "https://www.myelectricaldata.fr")),
        "med_pdl": str(config.get("med_pdl", "")),
        "med_start": str(config.get("med_start", "")),
        "med_token_set": bool(config.get("med_token")),
        "tailscale_enabled": bool(config.get("tailscale_enabled", False)),
        "tailscale_auto_serve": bool(config.get("tailscale_auto_serve", False)),
        "tailscale_hostname": str(config.get("tailscale_hostname") or "mecasa"),
        "tailscale_bin": str(config.get("tailscale_bin") or ""),
        "tailscale_authkey_set": bool(config.get("tailscale_authkey")),
        "state_file": str(config.get("state_file", "data/state.json")),
        "history_dir": str(config.get("history_dir", "data/history")),
        "history_interval": int(config.get("history_interval", 900) or 900),
        "history_days": int(config.get("history_days", 180) or 180),
        "structural_delay": float(config.get("structural_delay", 3) or 3),
        "cache_static": bool(config.get("cache_static", True)),
        "gzip_enabled": bool(config.get("gzip_enabled", True)),
    }


def validate_med_values(body: dict) -> dict:
    """Valide et normalise les champs MyElectricalData d'un formulaire.

    Utilisé par l'enregistrement de la configuration et par le test de
    connexion, afin que les deux routes appliquent les mêmes règles.
    """
    values = {}
    if "med_url" in body:
        url = str(body.get("med_url") or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError(
                "Adresse MyElectricalData invalide (http:// ou https://)"
            )
        values["med_url"] = url or "https://www.myelectricaldata.fr"

    if "med_pdl" in body:
        pdl = re.sub(r"\s+", "", str(body.get("med_pdl") or ""))
        if pdl and not re.fullmatch(r"\d{14}", pdl):
            raise ValueError("PDL invalide : 14 chiffres attendus")
        values["med_pdl"] = pdl

    if "med_start" in body:
        value = str(body.get("med_start") or "").strip()
        if not value:
            values["med_start"] = ""
        else:
            try:
                start = datetime.fromisoformat(value[:10]).date()
            except ValueError as err:
                raise ValueError("Date de début invalide (AAAA-MM-JJ)") from err
            today = datetime.now().date()
            if start > today:
                raise ValueError("La date de début ne peut pas être future")
            oldest = today - timedelta(days=MAX_HISTORY_DAYS - 1)
            if start < oldest:
                raise ValueError(
                    "L'historique Enedis est limité à 3 ans (1095 jours)"
                )
            values["med_start"] = start.isoformat()
    return values


def save_config(body: dict) -> tuple[dict, bool]:
    """Applique les changements et indique si un redémarrage est requis."""
    current = load_config()
    updated = dict(current)

    if "email" in body:
        email = str(body.get("email") or "").strip()
        if email and "@" not in email:
            raise ValueError("Adresse e-mail invalide")
        updated["email"] = email

    if body.get("password_clear"):
        updated["password"] = ""
    elif body.get("password"):
        updated["password"] = str(body["password"])

    if "port" in body:
        try:
            port = int(body.get("port"))
        except (TypeError, ValueError) as err:
            raise ValueError("Port invalide") from err
        if not 1024 <= port <= 65535:
            raise ValueError("Le port doit être compris entre 1024 et 65535")
        updated["port"] = port

    if "host" in body:
        host = str(body.get("host") or "").strip()
        if not host or any(ch.isspace() for ch in host) or "/" in host:
            raise ValueError("Hôte invalide")
        updated["host"] = host

    for key in ("allow_remote_admin", "mock", "wiser_enabled"):
        if key in body:
            updated[key] = bool(body.get(key))

    if "wiser_host" in body:
        updated["wiser_host"] = str(body.get("wiser_host") or "").strip()

    if body.get("wiser_secret_clear"):
        updated["wiser_secret"] = ""
    elif body.get("wiser_secret"):
        updated["wiser_secret"] = str(body["wiser_secret"]).strip()

    for key in ("pronote_url", "pronote_username", "pronote_ent", "pronote_child"):
        if key in body:
            updated[key] = str(body.get(key) or "").strip()

    if body.get("pronote_password_clear"):
        updated["pronote_password"] = ""
    elif body.get("pronote_password"):
        updated["pronote_password"] = str(body["pronote_password"])

    if "pronote_account" in body:
        account = str(body.get("pronote_account") or "student").strip().lower()
        if account not in ("student", "parent"):
            raise ValueError("Type de compte Pronote invalide")
        updated["pronote_account"] = account

    if "pronote_travel_minutes" in body:
        try:
            travel = int(body.get("pronote_travel_minutes"))
        except (TypeError, ValueError) as err:
            raise ValueError("Temps de trajet Pronote invalide") from err
        if not 0 <= travel <= 180:
            raise ValueError(
                "Le temps de trajet doit être compris entre 0 et 180 minutes"
            )
        updated["pronote_travel_minutes"] = travel

    if "pronote_morning_prep_minutes" in body:
        try:
            prep = int(body.get("pronote_morning_prep_minutes"))
        except (TypeError, ValueError) as err:
            raise ValueError("Temps de préparation/lever Pronote invalide") from err
        if not 0 <= prep <= 180:
            raise ValueError(
                "Le temps de préparation/lever doit être compris entre 0 et 180 minutes"
            )
        updated["pronote_morning_prep_minutes"] = prep

    if "pronote_student_status" in body:
        status = str(body.get("pronote_student_status") or "").strip().lower()
        if status not in STUDENT_STATUSES:
            raise ValueError("Statut scolaire Pronote invalide")
        updated["pronote_student_status"] = status

    for key, label in (
        ("pronote_comfort_mode", "mode confort"),
        ("pronote_reduced_mode", "mode réduit"),
    ):
        if key in body:
            mode = str(body.get(key) or "heat")
            if mode not in dict(MODES):
                raise ValueError(f"Mode Pronote inconnu ({label})")
            updated[key] = mode

    for key, label in (
        ("pronote_comfort_temp", "consigne confort"),
        ("pronote_reduced_temp", "consigne réduite"),
    ):
        if key in body:
            try:
                temp = float(body.get(key))
            except (TypeError, ValueError) as err:
                raise ValueError(f"Température Pronote invalide ({label})") from err
            if not 16 <= temp <= 30:
                raise ValueError(
                    f"La {label} doit être comprise entre 16 et 30 °C"
                )
            updated[key] = round(temp, 1)

    if "weather_enabled" in body:
        updated["weather_enabled"] = bool(body.get("weather_enabled"))

    for key, label, limit in (
        ("weather_latitude", "latitude", 90),
        ("weather_longitude", "longitude", 180),
    ):
        if key in body:
            try:
                value = float(body.get(key))
            except (TypeError, ValueError) as err:
                raise ValueError(f"Coordonnée météo invalide ({label})") from err
            if not -limit <= value <= limit:
                raise ValueError(
                    f"La {label} doit être comprise entre -{limit} et {limit}"
                )
            updated[key] = round(value, 4)

    if "weather_city" in body:
        updated["weather_city"] = str(body.get("weather_city") or "").strip()[:80]

    # --- Consommation électrique (MyElectricalData) ---------------------
    if "med_enabled" in body:
        updated["med_enabled"] = bool(body.get("med_enabled"))

    updated.update(validate_med_values(body))

    if body.get("med_token"):
        updated["med_token"] = str(body["med_token"]).strip()
    elif body.get("med_token_clear"):
        updated["med_token"] = ""

    for key in ("tailscale_enabled", "tailscale_auto_serve", "cache_static",
                "gzip_enabled"):
        if key in body:
            updated[key] = bool(body.get(key))

    if "tailscale_hostname" in body:
        hostname = str(body.get("tailscale_hostname") or "").strip().lower()
        if not hostname:
            hostname = "mecasa"
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname):
            raise ValueError(
                "Nom Tailscale invalide (lettres minuscules, chiffres et tirets)"
            )
        updated["tailscale_hostname"] = hostname

    if "tailscale_bin" in body:
        updated["tailscale_bin"] = str(body.get("tailscale_bin") or "").strip()[:400]

    if body.get("tailscale_authkey_clear"):
        updated["tailscale_authkey"] = ""
    elif body.get("tailscale_authkey"):
        updated["tailscale_authkey"] = str(body["tailscale_authkey"]).strip()

    # --- Stockage basse consommation -------------------------------------
    for key in ("state_file", "history_dir"):
        if key in body:
            value = str(body.get(key) or "").strip()
            if not value:
                raise ValueError(f"{key} ne peut pas être vide")
            updated[key] = value

    for key, low, high in (
        ("history_interval", 60, 86400),
        ("history_days", 7, 3650),
        ("structural_delay", 1, 600),
    ):
        if key in body:
            try:
                value = float(body.get(key))
            except (TypeError, ValueError) as err:
                raise ValueError(f"{key} invalide") from err
            if not low <= value <= high:
                raise ValueError(f"{key} doit être compris entre {low} et {high}")
            updated[key] = int(value) if key != "structural_delay" else value

    restart = any(
        updated.get(key) != current.get(key)
        for key in (
            "email", "password", "host", "port", "mock", "wiser_enabled",
            "wiser_host", "wiser_secret", "state_file", "history_dir",
            "history_interval", "history_days", "structural_delay",
            "cache_static", "gzip_enabled", "tailscale_enabled",
            "tailscale_auto_serve", "tailscale_hostname",
        )
    )

    CONFIG_PATH.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    global _config_cache
    with _config_lock:
        _config_cache = dict(updated)
    return updated, restart


def merge_pronote_config(current: dict, body: dict) -> dict:
    """Config Pronote effective pour un test, sans rien enregistrer."""
    config = dict(current)
    for key in (
        "pronote_url", "pronote_ent", "pronote_account", "pronote_username",
        "pronote_child",
    ):
        if key in body:
            config[key] = str(body.get(key) or "").strip()
    if body.get("pronote_password"):
        config["pronote_password"] = str(body["pronote_password"])
    return config


def merge_energy_config(current: dict, body: dict) -> dict:
    """Config MyElectricalData effective pour un test, sans rien enregistrer."""
    config = dict(current)
    config.update(validate_med_values(body))
    if body.get("med_token"):
        config["med_token"] = str(body["med_token"]).strip()
    if "med_enabled" in body:
        config["med_enabled"] = bool(body.get("med_enabled"))
    return config


def week_info(store: MemoryStore, monday) -> dict:
    settings = store.settings()
    overrides = store.week_overrides()
    moment = datetime(monday.year, monday.month, monday.day, 12)
    return {
        "week_type": week_type(moment, settings, overrides),
        "iso_week": monday.isocalendar().week,
    }


def aligned_week_start(store: MemoryStore, base_monday, target: str,
                       horizon: int = 26):
    settings = store.settings()
    overrides = store.week_overrides()
    monday = base_monday
    for _ in range(horizon):
        moment = datetime(monday.year, monday.month, monday.day, 12)
        if week_type(moment, settings, overrides) == target:
            return monday
        monday += timedelta(days=7)
    return None


# ------------------------------------------------------------------ tailscale


def tailscale_binary() -> str | None:
    """Chemin du binaire Tailscale, même s'il n'est pas dans le PATH.

    Sur Windows, l'installateur ne modifie pas le PATH : le binaire vit dans
    ``C:\\Program Files\\Tailscale``. On accepte aussi une valeur explicite
    (variable ``MECASA_TAILSCALE_BIN`` ou réglage ``tailscale_bin``).
    """
    candidates: list[str] = []
    env = os.environ.get("MECASA_TAILSCALE_BIN", "").strip()
    if env:
        candidates.append(env)
    try:
        configured = str(load_config().get("tailscale_bin") or "").strip()
    except Exception:  # noqa: BLE001
        configured = ""
    if configured:
        candidates.append(configured)
    found = shutil.which("tailscale")
    if found:
        candidates.append(found)
    if os.name == "nt":
        candidates += [
            r"C:\Program Files\Tailscale\tailscale.exe",
            r"C:\Program Files (x86)\Tailscale\tailscale.exe",
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""), "Tailscale", "tailscale.exe"
            ),
        ]
    else:
        candidates += [
            "/usr/bin/tailscale",
            "/usr/sbin/tailscale",
            "/usr/local/bin/tailscale",
            "/snap/bin/tailscale",
            "/opt/homebrew/bin/tailscale",
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _tailscale_cmd(args: list[str], timeout: int = 10) -> tuple[int, str, str]:
    flags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags = subprocess.CREATE_NO_WINDOW
    binary = tailscale_binary()
    if not binary:
        return 127, "", (
            "Tailscale introuvable. Installe-le, ou renseigne son chemin dans "
            "Réglages → Tailscale (ex. C:\\Program Files\\Tailscale\\tailscale.exe)."
        )
    try:
        result = subprocess.run(
            [binary] + args,
            capture_output=True, text=True, timeout=timeout, creationflags=flags,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except FileNotFoundError:
        return 127, "", f"Binaire Tailscale introuvable : {binary}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as err:  # noqa: BLE001
        return 1, "", str(err)


def _clean_dns_name(value) -> str:
    return str(value or "").strip().rstrip(".")


def tailscale_serve_args(port: int, path: str = "/", https_port: int = 443) -> list[str]:
    """Commande de publication : HTTPS 443 sur le tailnet vers le port local.

    ``tailscale serve --bg --set-path=/ http://127.0.0.1:8780`` équivaut à
    l'ancienne notation ``--set-url /`` : le service est exposé sur
    ``https://<noeud>.<tailnet>.ts.net/`` (certificat HTTPS automatique).
    """
    return [
        "serve", "--bg", f"--https={int(https_port)}",
        f"--set-path={path}", f"http://127.0.0.1:{int(port)}",
    ]


def summarize_serve_config(raw: str, expected_port: int | None = None,
                           expected_host: str | None = None) -> dict:
    """Analyse ``tailscale serve status --json``.

    Retourne l'état de publication et vérifie que la racine ``/`` pointe bien
    vers ``http://127.0.0.1:<port>`` en HTTPS 443.
    """
    result = {
        "serve_active": False, "serve_url": None, "serve_target": None,
        "serve_path": None, "serve_https_port": None, "serve_ok": False,
    }
    text = (raw or "").strip()
    if not text:
        return result
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return result
    web = data.get("Web") if isinstance(data, dict) else None
    if not isinstance(web, dict):
        return result

    best = None
    best_score = (-1, -1)
    for host_port, entry in web.items():
        if not isinstance(entry, dict):
            continue
        handlers = entry.get("Handlers")
        if not isinstance(handlers, dict):
            continue
        host, _, port_text = str(host_port).rpartition(":")
        try:
            https_port = int(port_text)
        except ValueError:
            continue
        for path, handler in handlers.items():
            if not isinstance(handler, dict):
                continue
            target = str(handler.get("Proxy") or "").strip()
            if not target:
                continue
            score = (1 if path == "/" else 0, 1 if https_port == 443 else 0)
            if score <= best_score:
                continue
            best_score = score
            best = {
                "host": _clean_dns_name(host), "https_port": https_port,
                "path": str(path or "/"), "target": target,
            }
    if best is None:
        return result

    result.update({
        "serve_active": True,
        "serve_target": best["target"],
        "serve_path": best["path"],
        "serve_https_port": best["https_port"],
        "serve_url": (
            f"https://{best['host']}/" if best["host"] else None
        ),
    })
    if expected_port:
        result["serve_ok"] = (
            best["path"] == "/"
            and best["https_port"] == 443
            and best["target"].rstrip("/").endswith(f"127.0.0.1:{int(expected_port)}")
            and (
                not expected_host
                or best["host"].lower() == _clean_dns_name(expected_host).lower()
            )
        )
    return result


def tailscale_checks(status: dict, hostname: str | None = None,
                     local_host: str | None = None) -> list[dict]:
    """Diagnostic pas à pas du nécessaire pour l'app Android."""
    checks: list[dict] = []

    def add(ok: bool, label: str, detail: str = "") -> None:
        checks.append({"ok": bool(ok), "label": label, "detail": detail})

    installed = bool(status.get("installed"))
    running = bool(status.get("running"))
    dns_name = _clean_dns_name(status.get("dns_name"))
    add(installed, "Client Tailscale installé",
        "" if installed else "Commande « tailscale » introuvable dans le PATH")
    add(running, "Nœud connecté au tailnet",
        "" if running else "Utilise « Connecter » ou lance « tailscale up »")
    add(bool(status.get("magic_dns_enabled")), "MagicDNS activé",
        "" if status.get("magic_dns_enabled")
        else "Active MagicDNS dans la console d'administration Tailscale")
    add(bool(dns_name), "Nom DNS du nœud",
        "" if dns_name else "Le nœud n'a pas encore de nom MagicDNS")
    if hostname and dns_name:
        short = dns_name.split(".")[0].lower()
        wanted = hostname.lower()
        if short == wanted:
            add(True, f"Nom du nœud : {hostname}")
        else:
            conflict = next(
                (
                    item for item in (status.get("services") or [])
                    if str(item.get("name", "")).lower() == f"svc:{wanted}"
                ),
                None,
            )
            if conflict:
                addrs = ", ".join(conflict.get("addrs") or []) or "adresse inconnue"
                detail = (
                    f"Le nom « {hostname} » est réservé par le Service Tailscale "
                    f"{conflict['name']} ({addrs}). Supprime ce Service dans la "
                    "console → Services, puis reconnecte le nœud."
                )
            else:
                detail = (
                    f"Le nœud s'appelle « {short} » au lieu de « {hostname} ». "
                    f"Relance « tailscale up --hostname={hostname} » ou renomme "
                    "le nœud dans la console → Machines."
                )
            add(False, f"Nom du nœud : {hostname}", detail)
    add(bool(status.get("https_ready")), "HTTPS Certificates activé",
        "" if status.get("https_ready")
        else "Active HTTPS Certificates dans la console Tailscale")
    if local_host is not None:
        reachable = str(local_host) in ("0.0.0.0", "::", "", "127.0.0.1",
                                        "::1", "localhost")
        add(reachable, "MeCasa écoute sur 127.0.0.1",
            "" if reachable
            else "Règle « host » sur 0.0.0.0 pour que tailscale serve atteigne MeCasa")
    add(bool(status.get("serve_ok")), "MeCasa publié en HTTPS",
        "" if status.get("serve_ok")
        else "Publie avec le bouton « Publier » (tailscale serve)")
    return checks


def get_tailscale_status(port: int | None = None,
                         hostname: str | None = None,
                         local_host: str | None = None) -> dict:
    status = {
        "installed": False, "running": False, "ip": None, "dns_name": None,
        "backend_state": None, "serve_active": False, "serve_url": None,
        "serve_target": None, "serve_path": None, "serve_https_port": None,
        "serve_ok": False, "magic_dns_enabled": False, "magic_dns_suffix": None,
        "cert_domains": [], "https_ready": False, "https_url": None,
        "health": [], "checks": [], "warnings": [], "services": [],
        "binary": tailscale_binary(), "auth_url": None,
    }
    data = None
    code, out, _ = _tailscale_cmd(["status", "--json"], timeout=15)
    if code == 0 and out.strip():
        status["installed"] = True
        try:
            data = json.loads(out)
        except (ValueError, TypeError):
            data = None
    else:
        code2, out2, _ = _tailscale_cmd(["ip", "-4"], timeout=5)
        if code2 == 0 and out2.strip():
            status["installed"] = True
            status["ip"] = out2.strip().splitlines()[0].strip()
        code3, out3, _ = _tailscale_cmd(["status"], timeout=5)
        if code3 == 0 and out3.strip():
            status["installed"] = True
            if "Running" in out3:
                status["running"] = True

    if isinstance(data, dict):
        status["backend_state"] = data.get("BackendState")
        status["running"] = data.get("BackendState") == "Running"
        self_info = data.get("Self") or {}
        dns_name = _clean_dns_name(self_info.get("DNSName"))
        status["dns_name"] = dns_name or self_info.get("HostName")
        addrs = self_info.get("TailscaleIPs") or data.get("TailscaleIPs") or []
        if addrs:
            status["ip"] = addrs[0]
        tailnet = data.get("CurrentTailnet") or {}
        suffix = _clean_dns_name(
            tailnet.get("MagicDNSSuffix") or data.get("MagicDNSSuffix")
        )
        status["magic_dns_suffix"] = suffix or None
        status["magic_dns_enabled"] = bool(
            tailnet.get("MagicDNSEnabled", bool(suffix))
        )
        certs = [
            _clean_dns_name(item).lower()
            for item in (data.get("CertDomains") or [])
            if _clean_dns_name(item)
        ]
        status["cert_domains"] = certs
        status["https_ready"] = bool(dns_name) and dns_name.lower() in certs
        status["health"] = [str(item) for item in (data.get("Health") or [])]
        auth_url = str(data.get("AuthURL") or "").strip()
        status["auth_url"] = auth_url or None
        # Services Tailscale dont ce nœud est proxy : leur nom MagicDNS peut
        # réserver celui du nœud (ex. un Service « mecasa » bloque « mecasa »).
        services: list[dict] = []
        cap_map = self_info.get("CapMap") or {}
        if isinstance(cap_map, dict):
            for cap, entries in cap_map.items():
                if not str(cap).startswith("services/"):
                    continue
                for entry in entries if isinstance(entries, list) else [entries]:
                    if not isinstance(entry, dict):
                        continue
                    name = str(
                        entry.get("Name") or f"svc:{str(cap).split('/', 1)[1]}"
                    )
                    services.append({
                        "name": name,
                        "addrs": [str(item) for item in (entry.get("Addrs") or [])],
                        "ports": [str(item) for item in (entry.get("Ports") or [])],
                    })
        status["services"] = services

    if status.get("dns_name"):
        status["https_url"] = f"https://{_clean_dns_name(status['dns_name'])}/"

    code_s, out_s, _ = _tailscale_cmd(["serve", "status", "--json"], timeout=10)
    serve = summarize_serve_config(out_s, port, status.get("dns_name"))
    if not serve["serve_active"]:
        code_t, out_t, _ = _tailscale_cmd(["serve", "status"], timeout=10)
        text_t = (out_t or "").strip()
        # « No serve config » sort sur stdout avec le code 0 : ce n'est pas
        # une publication active.
        if code_t == 0 and text_t and "no serve config" not in text_t.lower():
            serve["serve_active"] = True
    status.update(serve)
    if not status["serve_url"] and status["serve_active"]:
        status["serve_url"] = status["https_url"]

    status["checks"] = tailscale_checks(status, hostname, local_host)
    status["warnings"] = [
        item["detail"] for item in status["checks"]
        if not item["ok"] and item["detail"]
    ]
    return status


def tailscale_serve_enable(port: int, path: str = "/",
                           https_port: int = 443) -> dict:
    args = tailscale_serve_args(port, path, https_port)
    code, out, err = _tailscale_cmd(args, timeout=20)
    if code != 0:
        raise RuntimeError(err or out or "Échec de l'activation de tailscale serve")
    return {
        "ok": True,
        "command": "tailscale " + " ".join(args),
        "output": (out or err).strip(),
    }


def tailscale_serve_disable() -> dict:
    code, out, err = _tailscale_cmd(["serve", "reset"], timeout=15)
    if code != 0:
        raise RuntimeError(err or out or "Échec de la désactivation de tailscale serve")
    return {"ok": True, "output": (out or err).strip()}


def tailscale_up(authkey: str, hostname: str | None = None) -> dict:
    """Connecte le nœud. Sans clé, ``tailscale up`` réutilise la session.

    La clé est optionnelle pour renommer un nœud déjà connecté.
    """
    args = ["up"]
    if authkey:
        args.append(f"--authkey={authkey}")
    if hostname:
        args.append(f"--hostname={hostname}")
    args.append("--accept-routes=false")
    code, out, err = _tailscale_cmd(args, timeout=60)
    if code != 0:
        raise RuntimeError(err or out or "Échec de la connexion Tailscale")
    result = {"ok": True, "output": (out or err).strip()}
    # `tailscale up --hostname=X` réussit même si X est déjà pris : le nœud
    # garde alors « X-1 ». On le détecte pour l'afficher au lieu de laisser
    # croire que le renommage a fonctionné.
    if hostname:
        try:
            status = get_tailscale_status(hostname=hostname)
        except Exception:  # noqa: BLE001
            status = {}
        dns_name = _clean_dns_name(status.get("dns_name"))
        actual = dns_name.split(".")[0].lower() if dns_name else ""
        if actual and actual != hostname.lower():
            result["dns_name"] = dns_name
            result["warning"] = (
                f"Le nœud s'appelle « {actual} » au lieu de « {hostname} » : "
                "ce nom est déjà pris (Service Tailscale ou autre nœud). "
                "Libère-le dans la console Tailscale, puis reconnecte."
            )
    return result


def apply_tailscale_startup(config: dict) -> dict:
    """Au démarrage : connecte le nœud et publie MeCasa si demandé.

    Ne lève jamais : une erreur Tailscale ne doit pas empêcher MeCasa de
    démarrer. Les messages sont renvoyés pour affichage dans la console.
    """
    result = {"attempted": False, "up": None, "serve": None,
              "url": None, "errors": [], "auth_url": None}
    enabled = bool(config.get("tailscale_enabled"))
    auto_serve = bool(config.get("tailscale_auto_serve"))
    if not enabled and not auto_serve:
        return result

    result["attempted"] = True
    port = int(config.get("port", 8780))
    hostname = str(config.get("tailscale_hostname") or "mecasa").strip() or "mecasa"
    authkey = str(config.get("tailscale_authkey") or "").strip()

    try:
        current = get_tailscale_status(port, hostname, config.get("host"))
    except Exception as err:  # noqa: BLE001
        current = {"running": False}
        result["errors"].append(f"diagnostic : {err}")

    if enabled:
        short = _clean_dns_name(current.get("dns_name")).split(".")[0].lower()
        needs_up = (not current.get("running")) or (
            bool(short) and short != hostname.lower()
        )
        if needs_up:
            if current.get("running") or authkey:
                try:
                    result["up"] = tailscale_up(authkey, hostname=hostname)
                    if result["up"].get("warning"):
                        result["errors"].append(f"nom : {result['up']['warning']}")
                except Exception as err:  # noqa: BLE001
                    result["errors"].append(f"connexion : {err}")
            else:
                result["auth_url"] = current.get("auth_url")
                result["errors"].append(
                    "nœud non connecté et aucune clé d'authentification enregistrée"
                )

    if auto_serve:
        try:
            result["serve"] = tailscale_serve_enable(port)
        except Exception as err:  # noqa: BLE001
            result["errors"].append(f"publication : {err}")

    try:
        final = get_tailscale_status(port, hostname, config.get("host"))
        result["url"] = final.get("https_url") or final.get("serve_url")
    except Exception:  # noqa: BLE001
        pass
    return result


# ------------------------------------------------------------------- système


def _memory_info() -> dict | None:
    try:
        if os.path.isfile("/proc/meminfo"):
            values: dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    parts = rest.split()
                    if parts and parts[0].isdigit():
                        values[key] = int(parts[0])
            total = values.get("MemTotal")
            available = values.get("MemAvailable") or values.get("MemFree")
            if total:
                return {
                    "total": total * 1024,
                    "available": (available or 0) * 1024,
                    "used": (total - (available or 0)) * 1024,
                    "percent": round(100 * (total - (available or 0)) / total, 1),
                }
    except OSError:
        pass
    return None


def _process_rss() -> int | None:
    try:
        import resource  # type: ignore
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except (ImportError, OSError, ValueError):
        return None


def system_info(store: MemoryStore, engine: Engine, config: dict) -> dict:
    data_dir = str(DATA_DIR)
    disk = None
    try:
        usage = shutil.disk_usage(data_dir if os.path.isdir(data_dir) else str(ROOT))
        disk = {
            "path": data_dir,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(100 * usage.used / usage.total, 1) if usage.total else 0,
        }
    except OSError:
        pass
    return {
        "app": {
            "name": APP_NAME,
            "version": __version__,
            "now": datetime.now().isoformat(timespec="seconds"),
            "uptime": int(time.time() - START_TS),
            "pid": os.getpid(),
        },
        "host": {
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "memory": _memory_info(),
        "process_rss": _process_rss(),
        "disk": disk,
        "storage": store.storage_info(),
        "engine": {
            "running": bool(engine._thread and engine._thread.is_alive()),
            "mock": engine.mock,
            "last_poll": engine.last_poll.isoformat(timespec="seconds")
            if engine.last_poll else None,
        },
        "network": {
            "host": str(config.get("host", "0.0.0.0")),
            "port": int(config.get("port", 8780)),
            "urls": network_urls(config),
        },
        "flags": {
            "mock": bool(config.get("mock")),
            "wiser": bool(config.get("wiser_enabled")),
            "weather": bool(config.get("weather_enabled", True)),
        },
    }


# -------------------------------------------------------------- cache statique


class StaticCache:
    """Charge web/ en RAM une fois pour toutes (zéro lecture disque/requête)."""

    def __init__(self, root: Path, gzip_enabled: bool = True):
        self.root = root
        self.gzip_enabled = gzip_enabled
        self.files: dict[str, dict] = {}
        self.loaded_at = 0.0

    def load(self) -> int:
        self.files.clear()
        if not self.root.is_dir():
            return 0
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            rel = path.relative_to(self.root).as_posix()
            etag = '"' + hashlib.sha1(data).hexdigest()[:20] + '"'
            suffix = path.suffix.lower()
            mime = MIME_OVERRIDES.get(suffix)
            if not mime:
                guessed, _ = mimetypes.guess_type(path.name)
                mime = guessed or "application/octet-stream"
                if mime.startswith("text/") or suffix in (".js", ".css", ".svg",
                                                          ".json", ".html"):
                    mime += "; charset=utf-8"
            entry = {"data": data, "etag": etag, "mime": mime, "gz": None}
            if (
                self.gzip_enabled
                and len(data) > 512
                and (mime.startswith("text/") or suffix in (".js", ".css", ".svg",
                                                             ".json", ".html",
                                                             ".webmanifest"))
            ):
                compressed = gzip.compress(data, compresslevel=6)
                if len(compressed) < len(data):
                    entry["gz"] = compressed
            self.files[rel] = entry
        self.loaded_at = time.time()
        return len(self.files)

    def get(self, url_path: str) -> dict | None:
        return self.files.get(url_path.lstrip("/"))


# ------------------------------------------------------------------- serveur


class App:
    def __init__(
        self,
        store: MemoryStore,
        engine: Engine,
        weather: WeatherService | None = None,
        energy: EnergyService | None = None,
    ):
        self.store = store
        self.engine = engine
        self.weather = weather or WeatherService()
        self.energy = energy or EnergyService()
        self.static = StaticCache(WEB, gzip_enabled=True)


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{__version__}"
    protocol_version = "HTTP/1.1"

    # --------------------------------------------------------------- plomberie

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format, *args):  # noqa: A002 - signature imposée
        pass  # pas d'écriture disque : le journal applicatif vit en mémoire

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/"):
                self._api(method, path)
            elif method == "GET":
                self._static(path)
            else:
                self._json(405, {"error": "Méthode non autorisée"})
        except Exception as err:  # noqa: BLE001 - on renvoie l'erreur à l'UI
            self._json(500, {"error": str(err)})

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' "
            "'unsafe-inline'; script-src 'self'; connect-src 'self'; "
            "font-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'",
        )

    def _json(self, status: int, payload) -> None:
        body = dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > 1024 * 1024:
            raise ValueError("Corps de requête trop volumineux")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except ValueError as err:
            raise ValueError("Corps JSON invalide") from err
        if not isinstance(payload, dict):
            raise ValueError("Corps JSON invalide : objet attendu")
        return payload

    def _static(self, path: str) -> None:
        entry = None
        if path in ("/", "/index.html"):
            entry = self.app.static.get("index.html")
        elif path == "/favicon.ico":
            self.send_response(204)
            self._security_headers()
            self.end_headers()
            return
        else:
            entry = self.app.static.get(path)

        if entry is None:
            self._json(404, {"error": "Fichier introuvable"})
            return

        etag = entry["etag"]
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=3600")
            self._security_headers()
            self.end_headers()
            return

        body = entry["data"]
        use_gzip = entry["gz"] is not None and "gzip" in (
            self.headers.get("Accept-Encoding") or ""
        ).lower()
        if use_gzip:
            body = entry["gz"]

        self.send_response(200)
        self.send_header("Content-Type", entry["mime"])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        if path in ("/", "/index.html", "/sw.js"):
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    # -------------------------------------------------------------------- API

    def _loopback(self) -> bool:
        return str(self.client_address[0]) in (
            "127.0.0.1", "::1", "::ffff:127.0.0.1",
        )

    def _schedule_shutdown(self, restart: bool) -> None:
        server = self.server
        engine = self.app.engine
        store = self.app.store

        def run() -> None:
            engine.stop()
            thread = getattr(engine, "_thread", None)
            if thread and thread.is_alive():
                thread.join(timeout=20)
            store.close()
            if restart:
                try:
                    spawn_restart()
                except Exception as err:  # noqa: BLE001
                    print(f"[restart] échec du lancement : {err}")
            server.shutdown()

        threading.Timer(0.4, run).start()

    def _api(self, method: str, path: str) -> None:
        app = self.app

        if requires_local_admin(path) and not self._loopback():
            config = load_config()
            if not config.get("allow_remote_admin"):
                self._json(403, {
                    "error": (
                        "Administration réservée à la machine locale. Activez "
                        "« Autoriser les réglages depuis le réseau » dans "
                        "Réglages → Connexion & serveur."
                    )
                })
                return

        if method == "GET" and path == "/api/health":
            self._json(200, {"ok": True, "app": APP_NAME, "version": __version__})
            return

        if method == "GET" and path == "/api/weather":
            config = load_config()
            if not config.get("weather_enabled", True):
                self._json(200, {"ok": False, "error": "Météo désactivée"})
                return
            try:
                latitude = float(config.get("weather_latitude"))
                longitude = float(config.get("weather_longitude"))
            except (TypeError, ValueError):
                self._json(400, {"error": "Coordonnées météo non configurées"})
                return
            current = _current_weather(latitude, longitude)
            try:
                daily, daily_source = app.weather.daily(latitude, longitude, 7)
            except WeatherError:
                daily, daily_source = None, None
            city = str(config.get("weather_city") or "").strip()
            if current is None:
                # Repli : l'historique horaire déjà en cache.
                today = datetime.now().date()
                try:
                    result = app.weather.hourly(latitude, longitude, today, today)
                except WeatherError as err:
                    self._json(502, {"error": str(err)})
                    return
                hour = datetime.now().hour
                day_values = result.hours.get(today.isoformat(), {})
                temp = day_values.get(hour)
                if temp is None:
                    past = {h: v for h, v in day_values.items() if h <= hour}
                    if past:
                        temp = past[max(past)]
                self._json(200, {
                    "ok": True,
                    "temp": temp,
                    "code": None,
                    "source": result.source,
                    "city": city or None,
                    "daily": daily,
                    "daily_source": daily_source,
                })
                return
            self._json(200, {
                "ok": True,
                "temp": current["temp"],
                "code": current["code"],
                "source": "Open-Meteo (temps réel)",
                "city": city or None,
                "daily": daily,
                "daily_source": daily_source,
            })
            return

        if method == "GET" and path == "/api/weather/search":
            # Proxy du géocodage Open-Meteo : la CSP du navigateur interdit
            # les requêtes directes vers d'autres origines.
            query_string = parse_qs(urlparse(self.path).query)
            search = (query_string.get("q") or [""])[0].strip()
            if len(search) < 2:
                self._json(400, {"error": "Saisis au moins 2 caractères"})
                return
            url = (
                "https://geocoding-api.open-meteo.com/v1/search?"
                + urlencode({
                    "name": search[:100],
                    "count": "6",
                    "language": "fr",
                    "format": "json",
                })
            )
            request = urllib.request.Request(
                url, headers={"Accept": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (OSError, ValueError) as err:
                self._json(502, {"error": f"Recherche de ville impossible : {err}"})
                return
            results = []
            for item in payload.get("results") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    results.append({
                        "name": str(item.get("name") or ""),
                        "region": str(item.get("admin1") or ""),
                        "country": str(item.get("country") or ""),
                        "latitude": float(item["latitude"]),
                        "longitude": float(item["longitude"]),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
            self._json(200, {"ok": True, "results": results})
            return

        if method == "GET" and path == "/api/state":
            self._json(200, app.engine.snapshot())
            return

        if method == "GET" and path == "/api/system":
            self._json(200, system_info(app.store, app.engine, load_config()))
            return

        if method == "POST" and path == "/api/storage/flush":
            app.store.flush(force=True)
            self._json(200, {"ok": True, "storage": app.store.storage_info()})
            return

        if method == "DELETE" and path == "/api/logs":
            cleared = app.store.clear_events()
            app.store.flush(force=True)
            self._json(200, {"ok": True, "cleared": cleared})
            return

        if method == "GET" and path == "/api/stats":
            query = parse_qs(urlparse(self.path).query)
            range_name = (query.get("range") or ["week"])[0]
            if range_name not in RANGES:
                self._json(400, {"error": "Période invalide (day, week ou month)"})
                return

            anchor = parse_anchor((query.get("date") or [""])[0])
            room_query = (query.get("room_id") or [""])[0]
            room = None

            if room_query:
                try:
                    room_id = int(room_query)
                except (TypeError, ValueError):
                    self._json(400, {"error": "Paramètre room_id invalide"})
                    return
                stored = app.store.room(room_id)
                if stored is None:
                    self._json(404, {"error": "Pièce introuvable"})
                    return
                room = {**stored, "devices": app.store.room_devices(room_id)}
            else:
                try:
                    device_id = int((query.get("device_id") or [""])[0])
                except (TypeError, ValueError):
                    self._json(400, {"error": "Paramètre room_id manquant"})
                    return
                device = next(
                    (item for item in app.store.devices()
                     if int(item["device_id"]) == device_id),
                    None,
                )
                if device is None:
                    self._json(404, {"error": "Appareil introuvable"})
                    return
                stored = (
                    app.store.room(int(device["room_id"]))
                    if device.get("room_id") else None
                )
                room = {
                    "id": stored["id"] if stored else None,
                    "name": stored["name"] if stored else device.get("name"),
                    "thermostat_device_id": (
                        stored.get("thermostat_device_id") if stored else None
                    ),
                    "actuator_device_id": (
                        stored.get("actuator_device_id") if stored else None
                    ),
                    "devices": (
                        app.store.room_devices(stored["id"])
                        if stored else [device]
                    ),
                }

            try:
                payload = build_stats(
                    app.store, app.weather, room, range_name, anchor, load_config()
                )
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            self._json(200, payload)
            return

        if method == "GET" and path == "/api/energy":
            query = parse_qs(urlparse(self.path).query)
            view = (query.get("view") or ["month"])[0]
            if view not in ENERGY_VIEWS:
                self._json(400, {
                    "error": "Vue invalide (day, week, month ou year)"
                })
                return
            month = (query.get("month") or [""])[0]
            # Vue « Jour » : fenêtre glissante (paramètres optionnels).
            day = (query.get("day") or [""])[0]
            days = (query.get("days") or [""])[0]
            self._json(
                200,
                build_energy(
                    app.energy, load_config(), view, month, day=day, days=days
                ),
            )
            return

        # ------------------------------------------------------------- pièces

        if method == "GET" and path == "/api/rooms":
            rooms = [
                {**room, "devices": app.store.room_devices(room["id"])}
                for room in app.store.rooms()
            ]
            self._json(200, {"rooms": rooms})
            return

        if method == "POST" and path == "/api/rooms":
            try:
                room = app.store.add_room((self._body() or {}).get("name"))
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            app.store.log("info", f"Pièce ajoutée : {room['name']}")
            self._json(200, {"room": room})
            return

        match = re.fullmatch(r"/api/rooms/(\d+)", path)
        if match and method == "PUT":
            try:
                room = app.store.update_room(int(match.group(1)), self._body())
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            if room is None:
                self._json(404, {"error": "Pièce introuvable"})
                return
            self._json(200, {"room": room})
            return

        if match and method == "DELETE":
            if not app.store.delete_room(int(match.group(1))):
                self._json(404, {"error": "Pièce introuvable"})
                return
            app.store.log("info", "Pièce supprimée")
            self._json(200, {"ok": True})
            return

        # ------------------------------------------------------------- config

        if method == "GET" and path == "/api/config":
            self._json(200, public_config(load_config()))
            return

        if method == "PUT" and path == "/api/config":
            try:
                updated, restart = save_config(self._body())
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            self._json(200, {
                "ok": True,
                "restart_required": restart,
                "config": public_config(updated),
            })
            return

        if method == "POST" and path == "/api/config/test":
            self._json(200, test_cozytouch_login(self._body()))
            return

        if method == "POST" and path == "/api/energy/test":
            body = self._body()
            try:
                config = merge_energy_config(load_config(), body)
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            self._json(200, test_energy(config))
            return

        # ----------------------------------------------------------- tailscale

        if method == "GET" and path == "/api/tailscale/status":
            try:
                cfg = load_config()
                hostname = str(cfg.get("tailscale_hostname") or "mecasa")
                status = get_tailscale_status(
                    int(cfg.get("port", 8780)), hostname, cfg.get("host"),
                )
                status["enabled"] = bool(cfg.get("tailscale_enabled"))
                status["auto_serve"] = bool(cfg.get("tailscale_auto_serve"))
                status["hostname"] = hostname
                self._json(200, status)
            except Exception as err:  # noqa: BLE001
                self._json(500, {"error": str(err)})
            return

        if method == "POST" and path == "/api/tailscale/serve/enable":
            try:
                cfg = load_config()
                port = int(cfg.get("port", 8780))
                result = tailscale_serve_enable(port)
                status = get_tailscale_status(
                    port, str(cfg.get("tailscale_hostname") or "mecasa"),
                    cfg.get("host"),
                )
                result["url"] = status.get("https_url") or status.get("serve_url")
                result["status"] = status
                self._json(200, result)
            except Exception as err:  # noqa: BLE001
                self._json(500, {"error": str(err)})
            return

        if method == "POST" and path == "/api/tailscale/serve/disable":
            # Répondre AVANT de retirer la publication : si la page est
            # ouverte via « tailscale serve », le proxy disparaît et la
            # réponse serait perdue (le navigateur voit une erreur réseau).
            try:
                self._json(200, {"ok": True, "scheduled": True})
            except Exception:  # noqa: BLE001 - client déjà déconnecté
                pass

            def _unpublish() -> None:
                try:
                    tailscale_serve_disable()
                    app.store.log("info", "Publication Tailscale retirée")
                except Exception as err:  # noqa: BLE001
                    app.store.log("error", f"Publication Tailscale : {err}")

            threading.Timer(0.8, _unpublish).start()
            return

        if method == "POST" and path == "/api/tailscale/up":
            try:
                body = self._body()
                cfg = load_config()
                authkey = str(body.get("authkey") or cfg.get("tailscale_authkey") or "").strip()
                hostname = str(cfg.get("tailscale_hostname") or "mecasa")
                status = get_tailscale_status(
                    int(cfg.get("port", 8780)), hostname, cfg.get("host"),
                )
                if not status.get("running") and not authkey:
                    self._json(400, {
                        "error": (
                            "Ce nœud n'est pas connecté. Enregistre une clé "
                            "d'authentification Tailscale, ou autorise le nœud "
                            "depuis le lien ci-dessous."
                        ),
                        "auth_url": status.get("auth_url"),
                        "status": status,
                    })
                    return
                self._json(200, tailscale_up(authkey, hostname=hostname))
            except Exception as err:  # noqa: BLE001
                self._json(500, {"error": str(err)})
            return

        if method == "POST" and path == "/api/control/stop":
            self._json(200, {"ok": True, "action": "stop"})
            self._schedule_shutdown(restart=False)
            return

        if method == "POST" and path == "/api/control/restart":
            self._json(200, {"ok": True, "action": "restart"})
            self._schedule_shutdown(restart=True)
            return

        if method == "GET" and path == "/api/meta":
            self._json(200, {
                "app": APP_NAME,
                "version": __version__,
                "modes": MODES,
                "weekdays": WEEKDAY_NAMES,
                "now": datetime.now().isoformat(timespec="seconds"),
            })
            return

        if method == "GET" and path == "/api/export":
            self._json(200, app.store.export())
            return

        if method == "POST" and path == "/api/import":
            try:
                app.store.import_data(self._body())
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            app.store.flush(force=True)
            self._json(200, {"ok": True})
            return

        if method == "POST" and path == "/api/apply":
            app.engine.tick(force=True)
            self._json(200, app.engine.snapshot())
            return

        # ------------------------------------------- planning depuis Pronote

        if method == "POST" and path == "/api/pronote/search":
            body = self._body()
            try:
                results = search_schools(body.get("query"), body.get("limit", 10))
            except PronoteError as err:
                self._json(400, {"error": str(err)})
                return
            self._json(200, {"results": results})
            return

        if method == "POST" and path == "/api/pronote/test":
            body = self._body()
            config = merge_pronote_config(load_config(), body)
            self._json(200, test_pronote_connection(config))
            return

        if method == "POST" and path == "/api/pronote/preview":
            body = self._body()
            try:
                monday = parse_week_start(body.get("week"))
                plan = build_pronote_plan(load_config(), monday, body)
            except PronoteError as err:
                self._json(400, {"error": str(err)})
                return
            plan.update(week_info(app.store, monday))
            self._json(200, plan)
            return

        if method == "POST" and path == "/api/pronote/import":
            body = self._body()
            try:
                device_id = int(body.get("device_id"))
            except (TypeError, ValueError):
                self._json(400, {"error": "device_id manquant"})
                return

            if not app.store.device(device_id):
                self._json(404, {"error": "Appareil inconnu"})
                return

            weeks = body.get("weeks")
            if not isinstance(weeks, list):
                weeks = [body.get("week") or "A"]
            weeks = [str(value).upper() for value in weeks]
            weeks = [value for value in weeks if value in ("A", "B")]
            if not weeks:
                self._json(400, {"error": "Semaine invalide (A ou B attendue)"})
                return

            preview_days = body.get("days")
            if preview_days is not None and (
                not isinstance(preview_days, list) or len(preview_days) != 7
            ):
                self._json(400, {"error": "Planning Pronote invalide : 7 jours attendus"})
                return

            base_value = body.get("week_start") or body.get("week")
            try:
                base_monday = parse_week_start(base_value)
            except PronoteError as err:
                self._json(400, {"error": str(err)})
                return

            preview_type = str(body.get("preview_week_type") or "").upper()
            if preview_type not in ("A", "B"):
                preview_type = week_info(app.store, base_monday)["week_type"]

            weeks = list(dict.fromkeys(weeks))
            config = load_config()
            week_starts: dict[str, str] = {}
            try:
                for target in weeks:
                    if preview_days is not None and target == preview_type:
                        days = preview_days
                        source_start = base_monday
                    else:
                        source_start = aligned_week_start(
                            app.store, base_monday, target
                        )
                        if source_start is None:
                            hint = ""
                            if app.store.settings().get("week_mode") != "parity":
                                hint = (
                                    " Le mode d'alternance actuel ne distingue pas "
                                    "les semaines A et B (Réglages → Semaines A/B)."
                                )
                            self._json(400, {
                                "error": (
                                    f"Aucune semaine {target} trouvée dans les 26 "
                                    "semaines qui suivent la date choisie." + hint
                                )
                            })
                            return
                        plan = build_pronote_plan(config, source_start, body)
                        days = plan["days"]
                    if not isinstance(days, list) or len(days) != 7:
                        self._json(400, {
                            "error": "Planning Pronote invalide : 7 jours attendus"
                        })
                        return
                    # Conserver les créneaux du week-end existants (sam/dim) :
                    # l'import Pronote ne touche qu'aux jours de classe.
                    existing = app.store.slots(device_id, target)
                    if existing:
                        by_day = [[] for _ in range(7)]
                        for slot in existing:
                            by_day[int(slot["weekday"])].append(slot)
                        for weekday in (5, 6):
                            if by_day[weekday] and not (days[weekday] or []):
                                days[weekday] = [
                                    {
                                        "time": f"{int(s['minutes']) // 60:02d}:{int(s['minutes']) % 60:02d}",
                                        "mode": s.get("mode", "off"),
                                        "temp": s.get("temp", 20),
                                        "fan": s.get("fan"),
                                    }
                                    for s in sorted(by_day[weekday], key=lambda x: int(x["minutes"]))
                                ]
                    app.store.set_slots(device_id, target, days)
                    week_starts[target] = source_start.isoformat()
            except PronoteError as err:
                self._json(400, {"error": str(err)})
                return
            except (TypeError, ValueError) as err:
                self._json(400, {"error": f"Planning invalide : {err}"})
                return

            detail = ", ".join(
                f"semaine {target} (du {start})"
                for target, start in week_starts.items()
            )
            app.store.log("info", f"Planning Pronote importé — {detail}", device_id)
            app.engine.tick(force=True)
            self._json(200, {
                "ok": True,
                "device_id": device_id,
                "weeks": weeks,
                "week_starts": week_starts,
                "week_start": base_monday.isoformat(),
                "days": preview_days,
            })
            return

        if method == "POST" and path == "/api/sync":
            app.engine.poll()
            app.engine.seed_demo()
            self._json(200, app.engine.snapshot())
            return

        if method == "GET" and path == "/api/discover":
            setup = app.engine.client.get_setup()
            devices = []
            for device in setup.get("devices") or []:
                caps = device.get("capabilities")
                if isinstance(caps, list):
                    ids = sorted(
                        int(c["capabilityId"]) for c in caps
                        if "capabilityId" in c
                    )
                elif isinstance(caps, dict):
                    source = caps.get("values", caps)
                    ids = sorted(int(k) for k in source if str(k).isdigit())
                else:
                    ids = []
                devices.append({
                    "deviceId": device.get("deviceId"),
                    "name": device.get("customName") or device.get("name"),
                    "modelId": device.get("modelId"),
                    "productId": device.get("productId"),
                    "capabilities": ids,
                })
            self._json(200, {"devices": devices})
            return

        if method == "GET" and path == "/api/wiser/find":
            try:
                candidates = scan_network()
            except Exception as err:  # noqa: BLE001
                self._json(500, {"error": str(err)})
                return
            self._json(200, {"candidates": candidates})
            return

        if method == "GET" and path == "/api/wiser/schedules":
            if not app.engine.wiser_client:
                self._json(400, {
                    "error": "Passerelle Wiser non configurée ou désactivée"
                })
                return
            try:
                schedules = app.engine.wiser_client.get_schedules()
            except WiserError as err:
                self._json(502, {"error": str(err)})
                return
            result = []
            for item in schedules or []:
                if not isinstance(item, dict):
                    continue
                days = item.get("days")
                if not isinstance(days, list) or len(days) != 7:
                    days = [[] for _ in range(7)]
                result.append({
                    "schedule_id": item.get("schedule_id"),
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "days": days,
                })
            self._json(200, {"schedules": result})
            return

        # ---------------------------------------------------- planning Wiser

        def wiser_schedule_for(device_id: int):
            device = app.store.device(device_id)
            if not device:
                return None, None, (404, {"error": "Appareil inconnu"})
            if device.get("device_type", "atlantic") != "wiser":
                return None, None, (
                    400, {"error": "Cette zone n'est pas une zone Wiser"}
                )
            if not app.engine.wiser_client:
                return None, None, (
                    400, {"error": "Passerelle Wiser non configurée ou désactivée"}
                )
            room_id = int(device.get("wiser_room_id") or (device_id - 9000))
            try:
                schedule = app.engine.wiser_client.get_schedule(room_id)
            except WiserError as err:
                return None, None, (502, {"error": str(err)})
            if not schedule:
                return None, None, (
                    404,
                    {"error": "Aucun planning natif Wiser trouvé pour cette pièce"},
                )
            return device, schedule, None

        match = re.fullmatch(r"/api/wiser/schedule/(\d+)", path)
        if match and method == "GET":
            device, schedule, error = wiser_schedule_for(int(match.group(1)))
            if error:
                self._json(*error)
                return
            self._json(200, {
                "device_id": device["device_id"],
                "room_id": int(
                    device.get("wiser_room_id") or (device["device_id"] - 9000)
                ),
                "room_name": device.get("name"),
                "schedule_id": schedule.get("schedule_id"),
                "schedule_name": schedule.get("name"),
                "days": schedule.get("days") or [[] for _ in range(7)],
            })
            return

        if method == "POST" and path == "/api/wiser/import":
            body = self._body()
            try:
                device_id = int(body.get("device_id"))
            except (TypeError, ValueError):
                self._json(400, {"error": "device_id manquant"})
                return
            device, schedule, error = wiser_schedule_for(device_id)
            if error:
                self._json(*error)
                return

            weeks = body.get("weeks")
            if not isinstance(weeks, list):
                weeks = [body.get("week") or "A"]
            weeks = [str(value).upper() for value in weeks]
            weeks = [value for value in weeks if value in ("A", "B")]
            if not weeks:
                self._json(400, {"error": "Semaine invalide (A ou B attendue)"})
                return

            days = schedule.get("days") or [[] for _ in range(7)]
            for target in weeks:
                app.store.set_slots(device_id, target, days)

            app.store.log(
                "info",
                f"Planning Wiser importé (semaine {', '.join(weeks)})",
                device_id,
            )
            self._json(200, {
                "ok": True,
                "device_id": device_id,
                "weeks": weeks,
                "schedule_id": schedule.get("schedule_id"),
                "days": days,
            })
            return

        if method == "PUT" and path == "/api/settings":
            body = self._body()
            self._json(200, {"settings": app.store.set_settings(body)})
            return

        match = re.fullmatch(r"/api/devices/(\d+)", path)
        if match and method == "PUT":
            try:
                device = app.store.update_device(int(match.group(1)), self._body())
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            if not device:
                self._json(404, {"error": "Appareil inconnu"})
                return
            self._json(200, {"device": device})
            return

        if match and method == "DELETE":
            if not app.store.device(int(match.group(1))):
                self._json(404, {"error": "Appareil inconnu"})
                return
            app.store.delete_device(int(match.group(1)))
            app.store.log("info", "Appareil retiré")
            self._json(200, {"ok": True})
            return

        match = re.fullmatch(r"/api/programs/(\d+)/([AB])", path)
        if match and method == "PUT":
            body = self._body()
            days = body.get("days")
            if not isinstance(days, list) or len(days) != 7:
                self._json(400, {"error": "Format attendu : 7 listes de créneaux"})
                return
            target_id = int(match.group(1))
            week = match.group(2)
            room = app.store.room(target_id)
            try:
                if room:
                    app.store.set_slots(room_id=target_id, week=week, days=days)
                    app.store.log(
                        "info",
                        f"Planning Semaine {week} enregistré "
                        f"(pièce {room.get('name')})",
                    )
                else:
                    app.store.set_slots(target_id, week, days)
                    app.store.log(
                        "info", f"Planning Semaine {week} enregistré", target_id
                    )
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            self._json(200, {"ok": True})
            return

        if method == "POST" and path == "/api/override":
            body = self._body()
            try:
                device_id = int(body["device_id"])
                minutes = int(body.get("minutes", 60))
                mode = str(body.get("mode", "heat"))
                temp = float(body.get("temp", 20))
            except (KeyError, TypeError, ValueError):
                self._json(400, {"error": "Paramètres de forçage invalides"})
                return
            if not app.store.device(device_id):
                self._json(404, {"error": "Appareil inconnu"})
                return
            until = datetime.now() + timedelta(minutes=max(1, minutes))
            try:
                app.store.set_override(device_id, mode, temp, until)
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            mode_lbl = MODE_LABELS.get(mode, mode)
            if mode in ("off", "fan"):
                msg = f"Mode forcé {mode_lbl} jusqu'à {until:%H:%M}"
            else:
                msg = f"Mode forcé {mode_lbl} {temp:g} °C jusqu'à {until:%H:%M}"
            app.store.log("info", msg, device_id)
            app.engine.tick(force=True)
            self._json(200, app.engine.snapshot())
            return

        match = re.fullmatch(r"/api/override/(\d+)", path)
        if match and method == "DELETE":
            dev_id = int(match.group(1))
            app.store.clear_override(dev_id)
            app.store.log("info", "Reprise du planning normal", dev_id)
            app.engine.tick(force=True)
            self._json(200, {"ok": True})
            return

        if method == "PUT" and path == "/api/weeks":
            body = self._body()
            monday = body.get("monday")
            if not monday:
                self._json(400, {"error": "Date du lundi manquante"})
                return
            week = body.get("week")
            if week not in (None, "A", "B"):
                self._json(400, {"error": "Semaine invalide"})
                return
            app.store.set_week_override(monday, week)
            self._json(200, {
                "ok": True, "overrides": app.store.week_overrides()
            })
            return

        if method == "GET" and path == "/api/weeks":
            self._json(200, app.store.week_overrides())
            return

        self._json(404, {"error": "Route inconnue"})


def test_cozytouch_login(body: dict) -> dict:
    """Vérifie un couple e-mail / mot de passe sans rien enregistrer."""
    current = load_config()
    email = str(body.get("email") or current.get("email") or "").strip()
    password = str(body.get("password") or current.get("password") or "")

    if not email or not password:
        return {
            "ok": False,
            "status": "missing",
            "message": "Renseigne l'e-mail et le mot de passe Cozytouch.",
        }

    client = AtlanticClient(email, password, timeout=15)
    logged_in = False
    try:
        client.login()
        logged_in = True
        setup = client.get_setup()
    except AuthError:
        return {
            "ok": False,
            "status": "auth",
            "message": "E-mail ou mot de passe refusé par Cozytouch.",
        }
    except AtlanticError as err:
        prefix = (
            "Identifiants acceptés, mais lecture des appareils impossible"
            if logged_in
            else "Connexion au serveur Atlantic impossible"
        )
        return {
            "ok": False,
            "status": "setup" if logged_in else "network",
            "message": f"{prefix} : {err}",
        }
    finally:
        client.close()

    devices = []
    for item in setup.get("devices") or []:
        try:
            device_id = int(item.get("deviceId", 0))
        except (TypeError, ValueError):
            continue
        if not device_id:
            continue
        caps = _caps(item)
        if _mode_value(caps.get(7)) == "":
            continue
        devices.append({
            "device_id": device_id,
            "name": device_name(item, caps),
            "available": bool(item.get("isAvailable", True)),
        })

    address = (setup.get("address") or {}).get("formattedAddress") or ""
    count = len(devices)
    pluriel = "s" if count != 1 else ""
    return {
        "ok": True,
        "status": "ok",
        "message": (
            f"Identifiants valides. {count} appareil{pluriel} "
            f"pilotable{pluriel} trouvé{pluriel}."
        ),
        "email": email,
        "setup_name": str(setup.get("name") or ""),
        "address": address,
        "device_count": count,
        "devices": devices,
    }


def spawn_restart() -> None:
    """Relance le serveur dans un processus détaché (Linux et Windows)."""
    env = dict(os.environ)
    env["MECASA_START_DELAY"] = "3"
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    kwargs: dict = {"cwd": str(ROOT), "env": env, "close_fds": True}
    if os.name == "nt":
        flags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags |= subprocess.DETACHED_PROCESS
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "mecasa.main"], **kwargs)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Les navigateurs abandonnent souvent la connexion avant la fin de
        # l'envoi (navigation, onglet fermé) : ce n'est pas une erreur à
        # journaliser ni à afficher dans la console.
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError,
                            ConnectionAbortedError)):
            try:
                request.close()
            except OSError:
                pass
            return
        super().handle_error(request, client_address)


def main() -> None:
    sys.dont_write_bytecode = True
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    try:
        start_delay = float(os.environ.get("MECASA_START_DELAY", "0") or 0)
    except ValueError:
        start_delay = 0.0
    if start_delay > 0:
        time.sleep(start_delay)

    config = load_config()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    state_path = _resolve_path(config.get("state_file"), "data/state.json")
    history_dir = _resolve_path(config.get("history_dir"), "data/history")

    store = MemoryStore(
        str(state_path),
        str(history_dir),
        history_days=int(config.get("history_days", 180) or 180),
        history_interval=float(config.get("history_interval", 900) or 900),
        structural_delay=float(config.get("structural_delay", 3) or 3),
    )
    if store.load_error:
        print(f"[stockage] {store.load_error}")

    mock = bool(config.get("mock")) or not config.get("email") or not \
        config.get("password")
    if mock:
        # S'il y a déjà des appareils (migration), la simulation les reprend :
        # pas d'appareils fictifs qui viendraient polluer l'état réel.
        client = MockAtlanticClient(store.devices())
        print("[mode] SIMULATION : aucun identifiant fourni, données fictives.")
    else:
        client = AtlanticClient(str(config["email"]), str(config["password"]))

    wiser_client = None
    if config.get("wiser_enabled"):
        wiser_host = str(config.get("wiser_host") or "").strip()
        wiser_secret = str(config.get("wiser_secret") or "").strip()
        if wiser_host and wiser_secret:
            wiser_client = WiserClient(wiser_host, wiser_secret)
            print(f"[wiser] Passerelle Schneider Wiser connectée sur {wiser_host}")
        elif mock:
            wiser_client = MockWiserClient()
            print("[wiser] Passerelle Schneider Wiser simulée (mode simulation)")
        else:
            print(
                "[wiser] Ignoré : renseigne wiser_host et wiser_secret "
                f"(Hub détecté sur {wiser_host or 'le réseau'})."
            )

    engine = Engine(store, client, wiser_client=wiser_client)

    preexisting_devices = bool(store.devices())
    try:
        engine.poll()
        engine.seed_demo(include_history=not preexisting_devices)
    except AuthError as err:
        print(f"[auth] {err}")
    except AtlanticError as err:
        print(f"[cloud] {err}")

    engine.start()

    host = str(config.get("host", "127.0.0.1"))
    port = int(config.get("port", 8780))

    server = None
    last_error = None
    for _ in range(60):
        try:
            server = Server((host, port), Handler)
            break
        except OSError as err:
            last_error = err
            time.sleep(0.5)

    if server is None:
        engine.stop()
        store.close()
        raise SystemExit(f"Impossible d'écouter sur {host}:{port} : {last_error}")

    app = App(store, engine, WeatherService())
    app.static.gzip_enabled = bool(config.get("gzip_enabled", True))
    count = app.static.load()
    server.app = app  # type: ignore[attr-defined]

    tailscale_startup = apply_tailscale_startup(config)

    def _stop(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is not None:
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass

    print("")
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    print(f"  {APP_NAME} {__version__}  ->  http://{display_host}:{port}")
    if not _is_loopback_host(host):
        for url in network_urls(config):
            print(f"  Réseau local        ->  {url}")
    print(f"  Écoute              ->  {host}:{port}")
    print(f"  État (RAM+snapshot) ->  {state_path}")
    print(f"  Historique          ->  {history_dir}")
    print(f"  Fichiers web en RAM ->  {count} fichiers")
    print(f"  Mode                ->  {'simulation' if mock else 'réel'}")
    if tailscale_startup.get("attempted"):
        for message in tailscale_startup.get("errors") or []:
            print(f"  Tailscale           ->  erreur : {message}")
        if tailscale_startup.get("auth_url"):
            print(f"  Tailscale (login)   ->  {tailscale_startup['auth_url']}")
        if tailscale_startup.get("url"):
            print(f"  Tailscale (HTTPS)   ->  {tailscale_startup['url']}")
        elif tailscale_startup.get("serve"):
            print("  Tailscale           ->  service publié (URL indisponible)")
    print("  Ctrl+C pour arrêter.")
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        engine.stop()
        store.close()
        server.server_close()


if __name__ == "__main__":
    main()

