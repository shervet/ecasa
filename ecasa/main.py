"""Serveur HTTP local eCasa + API JSON.

Lancement :
    python -m ecasa.main

L'interface est servie sur http://127.0.0.1:8780 par défaut. Tous les fichiers
web sont chargés en mémoire au démarrage : une requête ne touche jamais la
carte SD. Les réponses texte sont compressées en gzip à la volée et servies
avec un ETag.
"""

from __future__ import annotations

import gzip
import hashlib
import ipaddress
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
from . import connectors as connector_registry
from .atlantic import AtlanticClient, AtlanticError, AuthError, MockAtlanticClient
from .energy import (
    ENERGY_VIEWS,
    MAX_HISTORY_DAYS,
    MED_DEFAULT_URL,
    EnergyService,
    build_energy,
    parse_med_start,
    test_energy,
)
from .engine import Engine, _caps, _mode_value, device_name
from .external_services import _run, restart_external_services
from .find_wiser import scan_network
from .pronote import (
    STUDENT_STATUSES,
    PronoteError,
    build_plan as build_pronote_plan,
    friday_departure_minutes,
    parse_week_start,
    search_schools,
    test_connection as test_pronote_connection,
)
from . import heating_curve
from .schedule import MODE_LABELS, MODES, WEEKDAY_NAMES, find_slot, week_type
from .stats import RANGES, build_stats, parse_anchor
from .storage import MemoryStore, dumps
from .weather import WeatherError, WeatherService
from .wiser import MockWiserClient, WiserClient, WiserError

APP_NAME = "eCasa"
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
    # Heure de lever (minutes après minuit) : passage en température confort.
    "pronote_wake_minutes": 7 * 60,
    "pronote_student_status": "externe",
    "pronote_comfort_mode": "heat",
    "pronote_comfort_temp": 20,
    "pronote_reduced_mode": "heat",
    "pronote_reduced_temp": 16,
    # Pièces liées au planning Pronote (import automatique nightly).
    # pronote_room_id conservé pour compatibilité (valeur unique).
    "pronote_room_id": 0,
    "pronote_room_ids": [],
    # Modes gérés par Pronote ("A" et/ou "B"). Vide = les deux (legacy).
    "pronote_weeks": [],
    # Réglages par pièce (confort/réduit) : {"<room_id>": {"comfort_mode": "cool",
    # "comfort_temp": 24, "reduced_mode": "cool", "reduced_temp": 26}}. Les clés
    # absentes héritent des réglages globaux ci-dessus.
    "pronote_room_overrides": {},
    # Jour de bascule de l'alternance : "monday" ou "friday".
    "pronote_switch_day": "monday",
    # Heure de coucher (minutes après minuit) : passage en température réduite.
    "pronote_bedtime_minutes": 22 * 60,
    # Mise à jour automatique du planning Pronote : heure (minutes après
    # minuit) et activation. Désactivée = import manuel uniquement.
    "pronote_auto_enabled": True,
    "pronote_direct_return": False,
    "pronote_sync_minutes": 3 * 60,
    # Connecteurs d'autres systèmes (Daikin, Bosch, Netatmo, Mitsubishi) :
    # réglages enregistrés dès maintenant, pilotes livrés progressivement.
    "connectors": connector_registry.default_settings(),
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
    # --- Consommation électrique (ConsoAPI par bokup) -----------------------
    "consoapi_enabled": False,
    "consoapi_url": "https://conso.boris.sh/api",
    "consoapi_token": "",
    "consoapi_pdl": "",
    "consoapi_start": "",
    # Fournisseur actif pour la consommation électrique.
    "energy_provider": "myelectricaldata",
    "tailscale_enabled": False,
    "tailscale_auto_serve": False,
    # Nom du nœud dans le tailnet : l'app Android résout ecasa.<tailnet>.ts.net.
    "tailscale_hostname": "ecasa",
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
_config_write_lock = threading.Lock()  # sérialise les écritures de config.json


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
        # Migration : pronote_room_id (unique) -> pronote_room_ids (liste).
        if not merged.get("pronote_room_ids"):
            single = int(merged.get("pronote_room_id") or 0)
            if single:
                merged["pronote_room_ids"] = [single]
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


def _truthy(value, default: bool = False) -> bool:
    """Booleen tolerant : accepte true/false, 0/1, oui/non, on/off."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() not in ("", "0", "false", "non", "no", "off")


def _minutes_of_day(value, default: int) -> int:
    """Minutes apres minuit, accepte un entier ou une heure « HH:MM »."""
    if isinstance(value, str) and ":" in value:
        parts = value.split(":")
        try:
            value = int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError):
            return default
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(24 * 60 - 1, minutes))


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
            "/api/energy/test", "/api/update/",
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
        "pronote_wake_minutes": int(
            7 * 60 if config.get("pronote_wake_minutes") is None
            else config["pronote_wake_minutes"]
        ),
        "pronote_student_status": str(
            config.get("pronote_student_status") or "externe"
        ),
        "pronote_comfort_mode": str(config.get("pronote_comfort_mode", "heat")),
        "pronote_comfort_temp": float(config.get("pronote_comfort_temp", 20) or 20),
        "pronote_reduced_mode": str(config.get("pronote_reduced_mode", "heat")),
        "pronote_reduced_temp": float(config.get("pronote_reduced_temp", 16) or 16),
        "pronote_room_id": int(config.get("pronote_room_id") or 0),
        "pronote_room_ids": [
            int(x) for x in (config.get("pronote_room_ids") or [])
            if str(x).isdigit() or isinstance(x, int)
        ],
        "pronote_school_days": config.get("pronote_school_days") or {},
        "pronote_weeks": [
            str(week).upper()
            for week in (config.get("pronote_weeks") or [])
            if str(week).upper() in ("A", "B")
        ],
        "pronote_bedtime_minutes": int(config.get("pronote_bedtime_minutes") or (22 * 60)),
        "pronote_switch_day": (
            "friday"
            if str(config.get("pronote_switch_day")).strip().lower() == "friday"
            else "monday"
        ),
        "pronote_auto_enabled": _truthy(config.get("pronote_auto_enabled"), True),
        "pronote_direct_return": _truthy(config.get("pronote_direct_return"), False),
        "pronote_sync_minutes": _minutes_of_day(
            config.get("pronote_sync_minutes"), 3 * 60
        ),
        "pronote_room_overrides": {
            str(room_id): dict(values)
            for room_id, values in (config.get("pronote_room_overrides") or {}).items()
            if isinstance(values, dict) and values
        },
        "connectors": connector_registry.public_settings(config),
        "weather_enabled": bool(config.get("weather_enabled", True)),
        "weather_city": str(config.get("weather_city", "")),
        "weather_latitude": float(config.get("weather_latitude", 48.8566) or 0),
        "weather_longitude": float(config.get("weather_longitude", 2.3522) or 0),
        "med_enabled": bool(config.get("med_enabled", False)),
        "med_url": str(config.get("med_url", "https://www.myelectricaldata.fr")),
        "med_pdl": str(config.get("med_pdl", "")),
        "med_start": str(config.get("med_start", "")),
        "med_token_set": bool(config.get("med_token")),
    "consoapi_enabled": bool(config.get("consoapi_enabled", False)),
    "consoapi_url": str(config.get("consoapi_url", "https://conso.boris.sh/api")),
    "consoapi_pdl": str(config.get("consoapi_pdl", "")),
    "consoapi_start": str(config.get("consoapi_start", "")),
    "consoapi_token_set": bool(config.get("consoapi_token")),
    "energy_provider": str(config.get("energy_provider", "myelectricaldata")),
        "tailscale_enabled": bool(config.get("tailscale_enabled", False)),
        "tailscale_auto_serve": bool(config.get("tailscale_auto_serve", False)),
        "tailscale_hostname": str(config.get("tailscale_hostname") or "ecasa"),
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


def validate_consoapi_values(body: dict) -> dict:
    """Valide et normalise les champs ConsoAPI d'un formulaire."""
    values = {}
    if "consoapi_url" in body:
        url = str(body.get("consoapi_url") or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("Adresse ConsoAPI invalide (http:// ou https://)")
        values["consoapi_url"] = url or "https://conso.boris.sh/api"
    if "consoapi_pdl" in body:
        pdl = re.sub(r"\s+", "", str(body.get("consoapi_pdl") or ""))
        if pdl and not re.fullmatch(r"\d{14}", pdl):
            raise ValueError("PDL invalide : 14 chiffres attendus")
        values["consoapi_pdl"] = pdl
    if "consoapi_start" in body:
        value = str(body.get("consoapi_start") or "").strip()
        if not value:
            values["consoapi_start"] = ""
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
                raise ValueError("L'historique Enedis est limité à 3 ans (1095 jours)")
            values["consoapi_start"] = start.isoformat()
    return values


def _write_config_locked(config: dict) -> dict:
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    global _config_cache
    with _config_lock:
        _config_cache = dict(config)
    return config


def save_config(body: dict) -> tuple[dict, bool]:
    """Applique les changements et indique si un redémarrage est requis."""
    with _config_write_lock:
        return _save_config_locked(body)


def _save_config_locked(body: dict) -> tuple[dict, bool]:
    current = load_config(force=True)
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

    # Ancien réglage « préparation/lever », remplacé par l'heure de lever.
    updated.pop("pronote_morning_prep_minutes", None)

    if "pronote_wake_minutes" in body:
        value = body.get("pronote_wake_minutes")
        if isinstance(value, str) and ":" in value:
            parts = value.split(":")
            try:
                value = int(parts[0]) * 60 + int(parts[1])
            except (ValueError, IndexError) as err:
                raise ValueError("Heure de lever invalide") from err
        try:
            minutes = int(value)
        except (TypeError, ValueError) as err:
            raise ValueError("Heure de lever invalide") from err
        if not (0 <= minutes <= 24 * 60):
            raise ValueError("Heure de lever invalide")
        updated["pronote_wake_minutes"] = minutes

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

    if "pronote_room_id" in body:
        try:
            room_id = int(body.get("pronote_room_id") or 0)
        except (TypeError, ValueError) as err:
            raise ValueError("Pièce Pronote invalide") from err
        if room_id < 0:
            raise ValueError("Pièce Pronote invalide")
        updated["pronote_room_id"] = room_id
        # Synchronise la liste si on utilise l'ancien champ unique.
        if "pronote_room_ids" not in body:
            updated["pronote_room_ids"] = [room_id] if room_id else []

    if "pronote_room_ids" in body:
        try:
            ids = [int(x) for x in (body.get("pronote_room_ids") or [])]
        except (TypeError, ValueError) as err:
            raise ValueError("Pièces Pronote invalides") from err
        ids = [i for i in ids if i >= 0]
        updated["pronote_room_ids"] = ids
        # Compat : on garde pronote_room_id = première pièce liée (ou 0).
        if "pronote_room_id" not in body:
            updated["pronote_room_id"] = ids[0] if ids else 0

    if "pronote_weeks" in body:
        raw = body.get("pronote_weeks")
        if isinstance(raw, str):
            raw = [part for part in raw.replace(",", " ").split() if part]
        if not isinstance(raw, list):
            raise ValueError("Semaines Pronote invalides")
        weeks: list[str] = []
        for value in raw:
            week = str(value).strip().upper()
            if week not in ("A", "B"):
                raise ValueError("Semaines Pronote invalides (A ou B attendues)")
            if week not in weeks:
                weeks.append(week)
        updated["pronote_weeks"] = weeks

    if "pronote_switch_day" in body:
        updated["pronote_switch_day"] = (
            "friday"
            if str(body.get("pronote_switch_day")).strip().lower() == "friday"
            else "monday"
        )

    if "pronote_bedtime_minutes" in body:
        value = body.get("pronote_bedtime_minutes")
        if isinstance(value, str) and ":" in value:
            parts = value.split(":")
            try:
                value = int(parts[0]) * 60 + int(parts[1])
            except (ValueError, IndexError) as err:
                raise ValueError("Heure de coucher invalide") from err
        try:
            minutes = int(value)
        except (TypeError, ValueError) as err:
            raise ValueError("Heure de coucher invalide") from err
        if not (0 <= minutes <= 24 * 60):
            raise ValueError("Heure de coucher invalide")
        updated["pronote_bedtime_minutes"] = minutes

    if "pronote_room_overrides" in body:
        updated["pronote_room_overrides"] = _clean_room_overrides(
            body.get("pronote_room_overrides")
        )

    if "connectors" in body:
        updated["connectors"] = connector_registry.merge_settings(
            body.get("connectors"), updated.get("connectors") or {}
        )

    if "pronote_auto_enabled" in body:
        updated["pronote_auto_enabled"] = _truthy(
            body.get("pronote_auto_enabled"), True
        )

    if "pronote_direct_return" in body:
        updated["pronote_direct_return"] = _truthy(
            body.get("pronote_direct_return"), False
        )

    if "pronote_sync_minutes" in body:
        raw = body.get("pronote_sync_minutes")
        if isinstance(raw, str) and ":" in raw:
            parts = raw.split(":")
            try:
                raw = int(parts[0]) * 60 + int(parts[1])
            except (ValueError, IndexError) as err:
                raise ValueError("Heure de mise à jour Pronote invalide") from err
        try:
            minutes = int(raw)
        except (TypeError, ValueError) as err:
            raise ValueError("Heure de mise à jour Pronote invalide") from err
        if not (0 <= minutes < 24 * 60):
            raise ValueError("Heure de mise à jour Pronote invalide")
        updated["pronote_sync_minutes"] = minutes

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

    # --- Consommation électrique (ConsoAPI par bokup) ---------------------
    if "consoapi_enabled" in body:
        updated["consoapi_enabled"] = bool(body.get("consoapi_enabled"))
    if "consoapi_url" in body:
        url = str(body.get("consoapi_url") or "").strip().rstrip("/")
        updated["consoapi_url"] = url or "https://conso.boris.sh/api"
    if "consoapi_pdl" in body:
        pdl = re.sub(r"\s+", "", str(body.get("consoapi_pdl") or ""))
        updated["consoapi_pdl"] = pdl
    if "consoapi_start" in body:
        value = str(body.get("consoapi_start") or "").strip()
        updated["consoapi_start"] = value
    if body.get("consoapi_token"):
        updated["consoapi_token"] = str(body["consoapi_token"]).strip()
    elif body.get("consoapi_token_clear"):
        updated["consoapi_token"] = ""

    if "energy_provider" in body:
        provider = str(body.get("energy_provider") or "").strip().lower()
        if provider in ("myelectricaldata", "consoapi"):
            updated["energy_provider"] = provider

    for key in ("tailscale_enabled", "tailscale_auto_serve", "cache_static",
                "gzip_enabled"):
        if key in body:
            updated[key] = bool(body.get(key))

    if "tailscale_hostname" in body:
        hostname = str(body.get("tailscale_hostname") or "").strip().lower()
        if not hostname:
            hostname = "ecasa"
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

    return _write_config_locked(updated), restart


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
    """Config effective pour un test, sans rien enregistrer."""
    config = dict(current)
    config.update(validate_med_values(body))
    config.update(validate_consoapi_values(body))
    if body.get("med_token"):
        config["med_token"] = str(body["med_token"]).strip()
    if "med_enabled" in body:
        config["med_enabled"] = bool(body.get("med_enabled"))
    if body.get("consoapi_token"):
        config["consoapi_token"] = str(body["consoapi_token"]).strip()
    if "consoapi_enabled" in body:
        config["consoapi_enabled"] = bool(body.get("consoapi_enabled"))
    if "energy_provider" in body:
        provider = str(body.get("energy_provider") or "").strip().lower()
        if provider in ("myelectricaldata", "consoapi"):
            config["energy_provider"] = provider
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


# --------------------------------------------- import et synchro Pronote


DAY_START_MIN = 7 * 60
DAY_END_MIN = 20 * 60


def _slot_minutes(slot: dict) -> int:
    if "minutes" in slot:
        try:
            return int(slot["minutes"])
        except (TypeError, ValueError):
            pass
    time = str(slot.get("time") or "00:00")
    parts = time.split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return 0


ROOM_OVERRIDE_KEYS = ("comfort_mode", "comfort_temp", "reduced_mode", "reduced_temp")


def _clean_room_overrides(value) -> dict:
    """Valide les réglages Pronote par pièce (modes et consignes).

    Les clés absentes héritent des réglages globaux : un dictionnaire vide pour
    une pièce signifie « comme tout le monde ».
    """
    if not isinstance(value, dict):
        raise ValueError("Réglages Pronote par pièce invalides")
    cleaned: dict[str, dict] = {}
    for raw_room, raw_item in value.items():
        try:
            room_id = int(raw_room)
        except (TypeError, ValueError):
            continue
        if room_id <= 0 or not isinstance(raw_item, dict):
            continue
        item: dict = {}
        for key in ("comfort_mode", "reduced_mode"):
            mode = raw_item.get(key)
            if mode in (None, ""):
                continue
            if mode not in dict(MODES):
                raise ValueError("Mode Pronote par pièce invalide")
            item[key] = str(mode)
        for key in ("comfort_temp", "reduced_temp"):
            raw_temp = raw_item.get(key)
            if raw_temp in (None, ""):
                continue
            try:
                temp = float(raw_temp)
            except (TypeError, ValueError) as err:
                raise ValueError("Consigne Pronote par pièce invalide") from err
            if not (5 <= temp <= 30):
                raise ValueError("Consigne Pronote par pièce hors limites")
            item[key] = temp
        if item:
            cleaned[str(room_id)] = item
    return cleaned


def pronote_room_options(config: dict, room_id) -> dict:
    """Réglages Pronote propres à une pièce (vide = réglages globaux)."""
    overrides = config.get("pronote_room_overrides") or {}
    if not isinstance(overrides, dict):
        return {}
    item = overrides.get(str(room_id)) or {}
    if not isinstance(item, dict):
        return {}
    return {
        key: item[key]
        for key in ROOM_OVERRIDE_KEYS
        if key in item and item[key] not in (None, "")
    }


def _clean_scopes(value) -> list | None:
    """Valide la portee d'import renvoyee par Pronote (7 jours)."""
    if not isinstance(value, list) or len(value) != 7:
        return None
    cleaned: list[tuple[str, int]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        kind, index = item
        if kind not in ("full", "prefix", "suffix"):
            return None
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        cleaned.append((kind, max(0, index)))
    return cleaned


def _merge_preserved_slots(
    days: list, existing: list[dict], scopes: list | None = None
) -> list[list[dict]]:
    """Applique l'import Pronote jour par jour (manuel et automatique).

    - Jour avec cours et portee « full » : le planning existant de ce jour est
      remplacé intégralement par le planning Pronote.
    - Jour partiel (« prefix » / « suffix ») : seule la portion gérée par
      Pronote est écrite (matinée du vendredi, fin de journée du lundi, début
      du mercredi), le reste de la journée reste éditable.
    - Jour sans cours (aucun créneau Pronote) : le planning existant est
      conservé tel quel et reste éditable.
    """
    result = [list(day or []) for day in days]
    if not existing:
        return result
    by_day: list[list[dict]] = [[] for _ in range(7)]
    for slot in existing:
        by_day[int(slot["weekday"])].append(slot)

    def convert(slot: dict) -> dict:
        return {
            "time": (
                f"{int(slot['minutes']) // 60:02d}:"
                f"{int(slot['minutes']) % 60:02d}"
            ),
            "mode": slot.get("mode", "off"),
            "temp": slot.get("temp", 20),
            "fan": slot.get("fan"),
        }

    def convert_all(slots: list[dict]) -> list[dict]:
        return [
            convert(slot)
            for slot in sorted(slots, key=lambda item: int(item["minutes"]))
        ]

    for weekday in range(7):
        new_day = result[weekday]
        old_day = by_day[weekday]
        if not new_day:
            result[weekday] = convert_all(old_day)
            continue
        if not old_day:
            continue

        kind, index = ("full", 0)
        if scopes and weekday < len(scopes):
            kind, index = scopes[weekday]

        if kind == "prefix":
            if index <= 0:
                result[weekday] = convert_all(old_day)
                continue
            index = min(index, len(new_day))
            boundary = _slot_minutes(new_day[index - 1])
            kept = [slot for slot in old_day if _slot_minutes(slot) > boundary]
            result[weekday] = new_day[:index] + convert_all(kept)
        elif kind == "suffix":
            if index >= len(new_day):
                result[weekday] = convert_all(old_day)
                continue
            boundary = _slot_minutes(new_day[index])
            kept = [slot for slot in old_day if _slot_minutes(slot) < boundary]
            result[weekday] = convert_all(kept) + new_day[index:]
    return result


def pronote_linked_weeks(config: dict) -> list[str]:
    """Semaines gérées par Pronote.

    Par défaut les deux semaines (A et B) restent gérées, comme avant
    l'introduction du réglage. Dès que l'utilisateur en choisit une seule,
    l'autre semaine redevient librement modifiable.
    """
    weeks = []
    for value in config.get("pronote_weeks") or []:
        week = str(value).strip().upper()
        if week in ("A", "B") and week not in weeks:
            weeks.append(week)
    return weeks or ["A", "B"]


def _sync_pronote_links(config: dict, room_id: int, weeks: list[str]) -> None:
    """Aligne les jours de cours enregistrés sur les semaines réellement liées.

    Les semaines non liées ne doivent plus porter de jours « Pronote » : sinon
    l'interface les verrouille alors qu'elles sont librement modifiables.
    """
    school_days = config.setdefault("pronote_school_days", {})
    room_days = school_days.setdefault(str(room_id), {})
    for week in ("A", "B"):
        if week not in weeks:
            room_days[week] = [False] * 7


def import_pronote_weeks(
    store: MemoryStore,
    config: dict,
    target_id: int,
    base_monday,
    weeks: list[str],
    *,
    room: bool = False,
    preview_days: list | None = None,
    preview_type: str | None = None,
    body: dict | None = None,
) -> dict[str, str]:
    """Importe une ou deux semaines Pronote dans une pièce ou un appareil.

    Renvoie la date source utilisée pour chaque semaine A/B. Lève
    ``PronoteError`` ou ``ValueError`` avec un message affichable.
    """
    week_starts: dict[str, str] = {}
    switch_day = (
        (body or {}).get("switch_day")
        or store.settings().get("week_switch_day")
        or config.get("pronote_switch_day")
        or "monday"
    )
    for target in weeks:
        # L'aperçu affiché est écrit tel quel dans la semaine cible, même
        # si le type de la semaine analysée diffère : l'utilisateur choisit
        # la destination (A/B), pas une semaine alignée automatiquement.
        scopes = None
        if preview_days is not None and (
            target == preview_type or len(weeks) == 1
        ):
            days = preview_days
            source_start = base_monday
            scopes = _clean_scopes((body or {}).get("scopes"))
        else:
            source_start = aligned_week_start(store, base_monday, target)
            if source_start is None:
                hint = ""
                if store.settings().get("week_mode") != "parity":
                    hint = (
                        " Le mode d'alternance actuel ne distingue pas "
                        "les semaines A et B (Réglages → Semaines A/B)."
                    )
                raise PronoteError(
                    f"Aucune semaine {target} trouvée dans les 26 semaines "
                    "qui suivent la date choisie." + hint
                )
            options = {**(body or {}), "switch_day": switch_day}
            if room:
                # Réglages par pièce : une clim peut être en froid confort alors
                # que les chambres Wiser restent en chaud.
                options.update(pronote_room_options(config, target_id))
            plan = build_pronote_plan(config, source_start, options)
            days = plan["days"]
            scopes = _clean_scopes(plan.get("scopes"))

        if not isinstance(days, list) or len(days) != 7:
            raise ValueError("Planning Pronote invalide : 7 jours attendus")

        if str(switch_day).strip().lower() == "friday":
            # Le vendredi s'arrete sur le depart : cette heure pilote la
            # bascule de semaine du moteur.
            friday_min = friday_departure_minutes(
                days,
                int(config.get("pronote_wake_minutes") or 420),
                int(config.get("pronote_travel_minutes") or 20),
            )
            store.set_settings({"friday_switch_minutes": str(friday_min)})

        existing = (
            store.slots(room_id=target_id, week=target)
            if room
            else store.slots(target_id, target)
        )
        if room and not existing:
            # Anciennes données : le planning peut encore être rattaché à
            # l'actionneur ou au thermostat de la pièce.
            room_data = store.room(target_id) or {}
            for key in ("thermostat_device_id", "actuator_device_id"):
                device_id = room_data.get(key)
                if not device_id:
                    continue
                existing = store.slots(int(device_id), target)
                if existing:
                    break

        # Un jour partiellement importe (garde alternee, mercredi matin) reste
        # editable : Pronote n'en gere qu'une portion.
        school_flags = [
            bool(day) and (scopes is None or scopes[index][0] == "full")
            for index, day in enumerate(days)
        ]
        days = _merge_preserved_slots(days, existing, scopes)
        if room:
            store.set_slots(room_id=target_id, week=target, days=days)
            school_days = config.setdefault("pronote_school_days", {})
            room_days = school_days.setdefault(str(target_id), {"A": [False] * 7, "B": [False] * 7})
            if target not in ("A", "B"):
                room_days.setdefault(target, [False] * 7)
            room_days[target] = school_flags
            # La pièce a été modifiée par Pronote (import manuel ou synchro
            # nocturne) : si elle est liée à un modèle, l'écart est signalé.
            link = store.link_for_room(target_id)
            if link:
                store.set_model_link_pending(
                    target_id,
                    not store.room_matches_model(
                        target_id,
                        link.get("model_id"),
                        _pronote_locked_days(config, target_id),
                    ),
                )
        else:
            store.set_slots(target_id, target, days)
        week_starts[target] = source_start.isoformat()
    return week_starts


SYNC_HOUR = 3
PRONOTE_SYNC_MINUTES = 3 * 60
SYNC_STATE_FILE = DATA_DIR / "sync.json"


def pronote_auto_enabled(config: dict) -> bool:
    """Vrai si la mise à jour automatique du planning Pronote est active."""
    return _truthy(config.get("pronote_auto_enabled"), True)


def pronote_sync_minutes(config: dict) -> int:
    """Heure (minutes après minuit) de la mise à jour Pronote automatique."""
    return _minutes_of_day(
        config.get("pronote_sync_minutes"), PRONOTE_SYNC_MINUTES
    )


def _sync_state_path(path=None) -> Path:
    return Path(path) if path else SYNC_STATE_FILE


def load_sync_state(path=None) -> dict:
    """Dernières dates de synchro réussies (une par tâche)."""
    try:
        data = json.loads(_sync_state_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_sync_state(state: dict, path=None) -> bool:
    """Écrit l'état de synchro de façon atomique. Ne lève jamais."""
    target = _sync_state_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(target)
    except OSError as err:
        print(f"[sync] état non enregistré : {err}")
        return False
    return True


def daily_sync_due(
    now: datetime,
    state: dict | None = None,
    hour: int = SYNC_HOUR,
    pronote_minutes: int | None = None,
    pronote_enabled: bool = True,
) -> bool:
    """Vrai si une synchro quotidienne reste à faire.

    Chaque tâche est suivie séparément (``energy`` et ``pronote``) : elle ne
    s'exécute qu'une fois par jour. L'électricité part à ``hour`` (3 h) ; le
    planning Pronote à ``pronote_minutes`` (réglable, 3 h par défaut) et
    seulement si la mise à jour automatique est activée. Une tentative, même
    en échec, est comptée : il n'y a donc pas de réessai automatique le
    même jour. La relance reste manuelle (bouton Actualiser des
    statistiques, import Pronote depuis le planning).
    """
    state = state or {}
    today = now.date().isoformat()
    now_minutes = now.hour * 60 + now.minute
    if state.get("energy") != today and now_minutes >= int(hour) * 60:
        return True
    if pronote_enabled and state.get("pronote") != today:
        target = int(hour) * 60 if pronote_minutes is None else int(pronote_minutes)
        if now_minutes >= target:
            return True
    return False


def run_pronote_auto_import(app, now: datetime | None = None) -> dict:
    """Récupère le planning Pronote et l'applique aux pièces liées.

    Ne lève pas d'exception : renvoie un état ``skipped`` ou ``ok``.
    """
    config = load_config()
    if not pronote_auto_enabled(config):
        return {"ok": False, "skipped": "disabled"}
    room_ids = config.get("pronote_room_ids")
    if not room_ids:
        single = int(config.get("pronote_room_id") or 0)
        room_ids = [single] if single else []
    room_ids = [int(x) for x in room_ids if x]
    valid_rooms = [rid for rid in room_ids if app.store.room(rid)]
    if not valid_rooms:
        return {"ok": False, "skipped": "no_room"}
    if not all(
        str(config.get(key) or "").strip()
        for key in ("pronote_url", "pronote_username", "pronote_password")
    ):
        return {"ok": False, "skipped": "not_configured"}

    now = now or datetime.now()
    monday = now.date() - timedelta(days=now.weekday())
    # Seules les semaines liées à Pronote sont rafraîchies : une semaine non
    # liée reste librement modifiable dans l'interface.
    weeks = pronote_linked_weeks(config)
    all_starts = {}
    for rid in valid_rooms:
        try:
            week_starts = import_pronote_weeks(
                app.store,
                config,
                rid,
                monday,
                weeks,
                room=True,
            )
            _sync_pronote_links(config, rid, weeks)
            all_starts[rid] = week_starts
        except Exception as err:  # noqa: BLE001
            app.store.log("warn", f"Synchronisation Pronote échouée pour la pièce {rid} : {err}")
    if not all_starts:
        return {"ok": False, "skipped": "error"}
    with _config_write_lock:
        fresh = load_config(force=True)
        fresh["pronote_school_days"] = config.get("pronote_school_days") or {}
        _write_config_locked(fresh)
    app.store.log(
        "info",
        "Planning Pronote synchronisé automatiquement.",
    )
    app.engine.tick(force=True)
    return {"ok": True, "rooms": all_starts, "week_start": monday.isoformat()}

def _energy_configured(config: dict) -> bool:
    return bool(
        config.get("med_enabled")
        and str(config.get("med_token") or "").strip()
        and str(config.get("med_pdl") or "").strip()
    )


def run_daily_sync(
    app,
    now: datetime | None = None,
    state: dict | None = None,
    state_path=None,
) -> dict:
    """Exécute les tâches quotidiennes dues : électricité puis Pronote.

    Ne lève pas d'exception et **n'exécute chaque tâche qu'une seule fois par
    jour** : la date est persistée dès la tentative, réussie ou non. Un échec
    est journalisé et attend le lendemain, ou une synchro manuelle (bouton
    Actualiser des statistiques, import Pronote depuis le planning).
    """
    now = now or datetime.now()
    today = now.date().isoformat()
    state = dict(state if state is not None else load_sync_state(state_path))
    config = load_config()
    result: dict = {"at": now.isoformat(timespec="seconds"), "ok": True,
                    "energy": None, "pronote": None}

    if state.get("energy") != today:
        state["energy"] = today
        provider = str(config.get("energy_provider") or "myelectricaldata").strip().lower()
        if provider == "consoapi":
            service = getattr(app, "consoapi_energy", None) or app.energy
            base_url = str(config.get("consoapi_url") or "https://conso.boris.sh/api").strip() or "https://conso.boris.sh/api"
            pdl = str(config.get("consoapi_pdl") or config.get("med_pdl") or "").strip()
            token = str(config.get("consoapi_token") or "").strip()
            start_val = config.get("consoapi_start")
            configured = bool(token and pdl and config.get("consoapi_enabled"))
        else:
            service = app.energy
            base_url = str(config.get("med_url") or MED_DEFAULT_URL).strip() or MED_DEFAULT_URL
            pdl = str(config.get("med_pdl") or "").strip()
            token = str(config.get("med_token") or "").strip()
            start_val = config.get("med_start")
            configured = bool(token and pdl and config.get("med_enabled", True))
        if not configured:
            result["energy"] = {"ok": False, "skipped": "not_configured"}
        else:
            try:
                values = service.daily(
                    base_url, pdl, token,
                    parse_med_start(start_val, now.date()),
                    now.date(),
                    refresh=True,
                )
            except Exception as err:  # noqa: BLE001 - réseau ou quota
                result["energy"] = {"ok": False, "error": str(err)}
                result["ok"] = False
                state["energy_error"] = str(err)
            else:
                warning = getattr(service, "last_warning", None)
                result["energy"] = {
                    "ok": warning is None,
                    "last_date": max(values) if values else None,
                    "warning": warning,
                }
                if warning:
                    result["ok"] = False
                    state["energy_error"] = warning
                else:
                    state.pop("energy_error", None)

    if state.get("pronote") != today:
        state["pronote"] = today
        try:
            pronote = run_pronote_auto_import(app, now)
        except Exception as err:  # noqa: BLE001
            pronote = {"ok": False, "error": str(err)}
        result["pronote"] = pronote
        if pronote.get("ok") or pronote.get("skipped") in (
            "no_room", "unknown_room", "not_configured", "disabled",
        ):
            state.pop("pronote_error", None)
        else:
            result["ok"] = False
            state["pronote_error"] = (
                pronote.get("error") or pronote.get("skipped") or "échec"
            )

    state["at"] = result["at"]
    save_sync_state(state, state_path)

    energy = result.get("energy")
    if energy is not None:
        if energy.get("ok"):
            app.store.log(
                "info",
                "Consommation synchronisée"
                f" (dernière donnée {energy.get('last_date') or '—'})",
            )
            print(f"[sync] électricité ok — {energy.get('last_date') or '—'}")
        elif not energy.get("skipped"):
            app.store.log(
                "erreur",
                f"Synchro consommation impossible : {energy.get('error')}",
            )
            print(f"[sync] électricité en échec : {energy.get('error')}")

    pronote = result.get("pronote")
    if pronote is not None:
        if pronote.get("ok"):
            # Le journal détaillé (semaines importées) est déjà écrit par
            # run_pronote_auto_import : on n'ajoute qu'une trace console.
            print(f"[sync] Pronote pièce {pronote.get('room_id')}")
        elif not pronote.get("skipped"):
            app.store.log(
                "erreur",
                "Synchro Pronote impossible : "
                f"{pronote.get('error') or pronote.get('skipped')}",
            )
            print(f"[sync] Pronote en échec : {pronote}")
    return result


class DailySync:
    """Fil de fond : une synchro électricité + Pronote par jour.

    L'électricité part à 3 h ; le planning Pronote à l'heure réglée dans
    Réglages → Pronote (3 h par défaut), et seulement si la mise à jour
    automatique est activée. Si eCasa démarre après l'heure prévue, le
    rattrapage se fait immédiatement, sauf si la tâche a déjà été tentée ce
    jour-là (état persisté dans ``data/sync.json``). Il n'y a donc jamais deux
    synchros automatiques le même jour, même après plusieurs redémarrages.
    """

    def __init__(self, app, hour: int = SYNC_HOUR, check_seconds: float = 60,
                 state_path=None):
        self.app = app
        self.hour = hour
        self.check_seconds = check_seconds
        self.state_path = state_path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="daily-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            state = load_sync_state(self.state_path)
            config = load_config()
            if daily_sync_due(
                now,
                state,
                self.hour,
                pronote_sync_minutes(config),
                pronote_auto_enabled(config),
            ):
                try:
                    run_daily_sync(self.app, now, state, self.state_path)
                except Exception as err:  # noqa: BLE001
                    self.app.store.log(
                        "erreur", f"Synchronisation quotidienne : {err}"
                    )
                    print(f"[sync] échec : {err}")
            self._stop.wait(self.check_seconds)


# ------------------------------------------------------------------ tailscale


def tailscale_binary() -> str | None:
    """Chemin du binaire Tailscale, même s'il n'est pas dans le PATH.

    Sur Windows, l'installateur ne modifie pas le PATH : le binaire vit dans
    ``C:\\Program Files\\Tailscale``. On accepte aussi une valeur explicite
    (variable ``ECASA_TAILSCALE_BIN`` ou réglage ``tailscale_bin``).
    """
    candidates: list[str] = []
    env = os.environ.get("ECASA_TAILSCALE_BIN", "").strip()
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
    can_pilot = bool(status.get("can_pilot", True))
    add(can_pilot, "Service autorisé à piloter Tailscale",
        "" if can_pilot else (
            "Lance une seule fois en SSH : sudo tailscale set --operator="
            f"{status.get('service_user') or '<utilisateur>'}"
        ))
    add(running, "Nœud connecté au tailnet",
        "" if running else "Utilise « Connecter » ou lance « sudo tailscale up »")
    add(bool(status.get("magic_dns_enabled")), "MagicDNS activé",
        "" if status.get("magic_dns_enabled")
        else "Active MagicDNS dans la console d'administration Tailscale")
    add(bool(dns_name), "Nom DNS du nœud",
        "" if dns_name else "Le nœud n'a pas encore de nom MagicDNS")
    if hostname and dns_name and running:
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
                    f"Relance « sudo tailscale up --hostname={hostname} » ou renomme "
                    "le nœud dans la console → Machines."
                )
            add(False, f"Nom du nœud : {hostname}", detail)
    add(bool(status.get("https_ready")), "HTTPS Certificates activé",
        "" if status.get("https_ready")
        else "Active HTTPS Certificates dans la console Tailscale (DNS → HTTPS Certificates)")
    if local_host is not None:
        reachable = str(local_host) in ("0.0.0.0", "::", "", "127.0.0.1",
                                        "::1", "localhost")
        add(reachable, "eCasa écoute sur 127.0.0.1",
            "" if reachable
            else "Règle « host » sur 0.0.0.0 pour que tailscale serve atteigne eCasa")
    add(bool(status.get("serve_ok")), "eCasa publié en HTTPS",
        "" if status.get("serve_ok")
        else "Publie avec le bouton « Publier » (sudo tailscale serve)")
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
        "service_user": None, "operator_user": None, "can_pilot": True,
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
        # DNSName n'existe qu'une fois le nœud connecté ; le HostName local ne
        # doit pas être présenté comme un nom MagicDNS valide.
        status["dns_name"] = dns_name or None
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
        # réserver celui du nœud (ex. un Service « ecasa » bloque « ecasa »).
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

    if status.get("installed"):
        can_pilot, operator, service_user = _tailscale_can_pilot()
        status["can_pilot"] = can_pilot
        status["operator_user"] = operator
        status["service_user"] = service_user or None

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
        detail = err or out or "Échec de l'activation de tailscale serve"
        if _looks_privileged(detail):
            raise RuntimeError(_tailscale_root_hint(" ".join(args), detail))
        raise RuntimeError(detail)
    return {
        "ok": True,
        "command": "tailscale " + " ".join(args),
        "output": (out or err).strip(),
    }


def tailscale_serve_disable() -> dict:
    code, out, err = _tailscale_cmd(["serve", "reset"], timeout=15)
    if code != 0:
        detail = err or out or "Échec de la désactivation de tailscale serve"
        if _looks_privileged(detail):
            raise RuntimeError(
                _tailscale_root_hint("tailscale serve reset", detail)
            )
        raise RuntimeError(detail)
    return {"ok": True, "output": (out or err).strip()}


def tailscale_up(authkey: str = "", hostname: str | None = None) -> dict:
    """Connecte (ou enregistre) le nœud.

    Avec une clé : enregistrement direct, sans interaction. Sans clé :
    ``tailscale up --timeout`` ouvre la session et renvoie l'URL
    d'autorisation à valider dans la console Tailscale.
    """
    args = ["up"]
    if authkey:
        args.append(f"--authkey={authkey}")
    if hostname:
        args.append(f"--hostname={hostname}")
    args.append("--accept-routes=false")
    timeout = 90
    if not authkey:
        # Sans clé, `tailscale up` bloque jusqu'à l'autorisation manuelle :
        # on borne l'attente, l'URL reste disponible via `status --json`.
        args.append("--timeout=8s")
        timeout = 25
    code, out, err = _tailscale_cmd(args, timeout=timeout)
    detail = (err or out or "").strip()
    if detail and _looks_privileged(detail):
        raise RuntimeError(_tailscale_root_hint(" ".join(args), detail))
    if authkey and code != 0:
        raise RuntimeError(detail or "Échec de la connexion Tailscale")
    if not authkey:
        try:
            current = get_tailscale_status(hostname=hostname)
        except Exception:  # noqa: BLE001
            current = {}
        if not current.get("running"):
            auth_url = current.get("auth_url")
            if auth_url:
                return {
                    "ok": False,
                    "needs_login": True,
                    "auth_url": auth_url,
                    "detail": (
                        "Ouvre le lien pour autoriser ce nœud, puis clique "
                        "« Actualiser » : la connexion se termine toute seule."
                    ),
                }
            raise RuntimeError(detail or "Échec de la connexion Tailscale")
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


def _tailscale_service_user() -> str:
    """Compte qui fait tourner eCasa (celui à autoriser via ``--operator``)."""
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return str(os.environ.get("USER") or os.environ.get("LOGNAME") or "")


def tailscale_operator_user() -> str | None:
    """Compte autorisé à piloter tailscaled sans root (``OperatorUser``).

    Retourne ``""`` quand les préférences sont lisibles mais sans operator, et
    ``None`` quand elles ne peuvent pas être lues (binaire absent, arrêté…).
    """
    code, out, _err = _tailscale_cmd(["debug", "prefs"], timeout=8)
    if code != 0 or not (out or "").strip():
        return None
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return str(data.get("OperatorUser") or "").strip()


def _tailscale_can_pilot() -> tuple[bool, str | None, str]:
    """Le service peut-il piloter Tailscale ? (root ou ``--operator``).

    On ne conclut à un refus que si les préférences sont lisiblement vides :
    sinon (Windows, binaire absent, réponse inattendue) on laisse passer pour
    ne pas afficher un faux avertissement.
    """
    user = _tailscale_service_user()
    if os.name != "linux" or not hasattr(os, "geteuid"):
        return True, None, user
    if os.geteuid() == 0:
        return True, user or None, user
    operator = tailscale_operator_user()
    if operator is None:
        return True, None, user
    allowed = bool(operator) and operator.lower() == user.lower()
    return allowed, operator or None, user


def _tailscale_root_hint(command: str, detail: str = "") -> str:
    """Message actionnable quand tailscaled refuse une action non privilégiée."""
    user = _tailscale_service_user() or "<utilisateur>"
    text = (
        "Le service eCasa n'a pas les droits requis (NoNewPrivileges). "
        "Autorise une seule fois son compte à piloter Tailscale : "
        f"« sudo tailscale set --operator={user} », puis relance l'action ici. "
        f"Sinon, en SSH : sudo {command}"
    )
    cleaned = str(detail or "").strip()
    if cleaned and cleaned not in text:
        text += f" — {cleaned}"
    return text


def _looks_privileged(detail: str) -> bool:
    """Erreur qui trahit un manque de droits root (service non privilégié)."""
    low = str(detail or "").lower()
    return any(
        key in low
        for key in (
            "no new privileges",
            "must be root",
            "access denied",
            "permission denied",
            "operation not permitted",
            "a password is required",
            "a terminal is required",
        )
    )


def _privileged_failure(command: str, detail: str = "") -> dict:
    """Résultat d'échec lisible quand root est requis mais inaccessible."""
    hint = (
        "Droits root requis. Le service eCasa tourne sans privilèges "
        "(NoNewPrivileges) : sudo ne peut pas élever les droits depuis "
        "l'interface. Lance cette commande en SSH :"
    )
    suffix = f" — {detail.strip()}" if str(detail or "").strip() else ""
    return {
        "ok": False,
        "needs_root": True,
        "detail": f"{hint} {command}{suffix}",
        "command": command,
    }


def _sudo_available() -> bool:
    """``sudo`` peut-il élever les droits sans mot de passe ni blocage ?"""
    if os.geteuid() == 0:
        return True
    if not shutil.which("sudo"):
        return False
    code, _detail = _run(["sudo", "-n", "true"], timeout=5)
    return code == 0


def tailscale_install() -> dict:
    import platform as _platform
    system = _platform.system().lower()
    if system == "linux":
        command = "curl -fsSL https://tailscale.com/install.sh | sh"
        ssh_command = f"sudo sh -c '{command}'"
        # Le service systemd tourne sans privilèges : inutile d'appeler sudo
        # s'il ne peut pas aboutir, on renvoie la commande à lancer en SSH.
        if os.geteuid() != 0:
            if not _sudo_available():
                return _privileged_failure(ssh_command)
            args = ["sudo", "-n", "sh", "-c", command]
        else:
            args = ["sh", "-c", command]
        code, detail = _run(args, timeout=300)
        if code != 0:
            if _looks_privileged(detail):
                return _privileged_failure(ssh_command, detail)
            return {"ok": False, "detail": detail or ("code %d" % code), "command": ssh_command}
        return {"ok": True, "detail": detail or "Tailscale installe"}
    if system == "darwin":
        return {"ok": False, "detail": "Installation manuelle requise sur macOS.", "command": "brew install --cask tailscale"}
    if system == "windows":
        code, detail = _run(
            ["winget", "install", "-e", "--id", "Tailscale.Tailscale",
             "--silent", "--accept-package-agreements", "--accept-source-agreements"],
            timeout=300,
        )
        if code != 0:
            return {"ok": False, "detail": detail or ("code %d" % code),
                    "command": "winget install -e --id Tailscale.Tailscale"}
        return {"ok": True, "detail": detail or "Tailscale installe"}
    return {"ok": False, "detail": "Systeme non pris en charge.", "command": ""}


def tailscale_uninstall() -> dict:
    import platform as _platform
    system = _platform.system().lower()
    if system == "linux":
        # « purge » + suppression de l'état : base propre pour une réinstallation.
        command = (
            "apt-get purge -y tailscale 2>/dev/null || dnf remove -y tailscale"
        )
        ssh_command = f"sudo sh -c '{command}'"
        if os.geteuid() != 0:
            if not _sudo_available():
                return _privileged_failure(ssh_command)
            args = ["sudo", "-n", "sh", "-c", command]
        else:
            args = ["sh", "-c", command]
        code, detail = _run(args, timeout=300)
        if code != 0:
            if _looks_privileged(detail):
                return _privileged_failure(ssh_command, detail)
            return {"ok": False, "detail": detail or ("code %d" % code), "command": ssh_command}
        return {"ok": True, "detail": detail or "Tailscale desinstalle"}
    if system == "darwin":
        return {"ok": False, "detail": "Desinstallation manuelle requise sur macOS.", "command": "brew uninstall --cask tailscale"}
    if system == "windows":
        code, detail = _run(
            ["winget", "uninstall", "-e", "--id", "Tailscale.Tailscale", "--silent"],
            timeout=300,
        )
        if code != 0:
            return {"ok": False, "detail": detail or ("code %d" % code),
                    "command": "winget uninstall -e --id Tailscale.Tailscale"}
        return {"ok": True, "detail": detail or "Tailscale desinstalle"}
    return {"ok": False, "detail": "Systeme non pris en charge.", "command": ""}


# ------------------------------------------------------------------ mise à jour (git)
def ecasa_git_root() -> Path | None:
    """Racine du dépôt git d'eCasa, ou None si installé hors git (archive)."""
    try:
        if (ROOT / ".git").exists():
            return ROOT
    except Exception:  # noqa: BLE001
        pass
    return None


def _git(args: list[str], timeout: int = 60) -> tuple[int, str]:
    root = ecasa_git_root()
    if root is None:
        return 1, "Dépôt git introuvable (installation hors git)."
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return 1, str(err)
    detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, detail


def ecasa_current_commit() -> str:
    code, out = _git(["rev-parse", "--short", "HEAD"], timeout=10)
    return out.strip() if code == 0 else ""


def ecasa_update_branch() -> str:
    code, out = _git(["symbolic-ref", "refs/remotes/origin/HEAD"], timeout=10)
    if code == 0 and out.strip():
        return out.strip().rsplit("/", 1)[-1]
    return "main"


def ecasa_update_check() -> dict:
    """Vérifie si une mise à jour est disponible sur origin (sans l'appliquer)."""
    result: dict = {
        "available": False,
        "current": ecasa_current_commit(),
        "remote": "",
        "behind": 0,
        "branch": "",
        "is_git": ecasa_git_root() is not None,
        "error": None,
    }
    if not result["is_git"]:
        result["error"] = (
            "eCasa n'est pas installé depuis git : mise à jour automatique indisponible."
        )
        return result
    branch = ecasa_update_branch()
    result["branch"] = branch
    code, out = _git(["fetch", "origin", "--quiet"], timeout=30)
    if code != 0:
        result["error"] = out or "Échec du git fetch (réseau ?)."
        return result
    code, out = _git(["rev-parse", "--short", f"origin/{branch}"], timeout=10)
    if code == 0:
        result["remote"] = out.strip()
    code, out = _git(["rev-list", "--count", f"HEAD..origin/{branch}"], timeout=10)
    if code == 0:
        try:
            result["behind"] = int(out.strip())
        except ValueError:
            result["behind"] = 0
    result["available"] = result["behind"] > 0
    return result


# --------------------------------------------------- veille des mises à jour
# Un fil de fond vérifie une fois par jour si origin contient de nouveaux
# commits. Le résultat est mis en cache sur disque et injecté dans le snapshot
# /api/state : l'interface peut donc afficher une pastille jaune sans jamais
# lancer git (donc sans bloquer les requêtes HTTP).

UPDATE_CHECK_FILE = DATA_DIR / "update-check.json"
UPDATE_CHECK_INTERVAL = 6 * 3600   # nouvelle tentative au plus toutes les 6 h
UPDATE_CHECK_MAX_AGE = 24 * 3600   # un contrôle par jour suffit
UPDATE_CHECK_LOCK = threading.Lock()
_UPDATE_STATE: dict | None = None


def _update_state_path(path=None) -> Path:
    return Path(path) if path else UPDATE_CHECK_FILE


def load_update_state(path=None) -> dict:
    """État persisté de la dernière vérification de mise à jour."""
    try:
        data = json.loads(_update_state_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_update_state(state: dict, path=None) -> bool:
    """Écrit l'état de mise à jour de façon atomique. Ne lève jamais."""
    target = _update_state_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(target)
    except OSError as err:
        print(f"[update] état non enregistré : {err}")
        return False
    return True


def update_state_snapshot() -> dict:
    """État de mise à jour lisible par l'interface (lecture seule, sans git)."""
    global _UPDATE_STATE
    with UPDATE_CHECK_LOCK:
        state = _UPDATE_STATE
        if state is None:
            state = load_update_state()
            _UPDATE_STATE = state
    return {
        "available": bool(state.get("available")),
        "behind": int(state.get("behind") or 0),
        "current": str(state.get("current") or ""),
        "remote": str(state.get("remote") or ""),
        "branch": str(state.get("branch") or ""),
        "checked_at": str(state.get("checked_at") or ""),
        "error": state.get("error"),
    }


def _parse_timestamp(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _record_update_state(
    result: dict,
    store=None,
    now: datetime | None = None,
    path=None,
    previous: dict | None = None,
) -> dict:
    """Mémorise un résultat de vérification et journalise l'avertissement.

    L'avertissement n'est écrit qu'une fois par commit distant : un contrôle
    quotidien ne doit pas remplir le journal.
    """
    global _UPDATE_STATE
    now = now or datetime.now()
    prev = load_update_state(path) if previous is None else previous
    state = {
        "checked_at": now.isoformat(timespec="seconds"),
        "available": bool(result.get("available")),
        "behind": int(result.get("behind") or 0),
        "current": str(result.get("current") or ""),
        "remote": str(result.get("remote") or ""),
        "branch": str(result.get("branch") or ""),
        "error": result.get("error"),
        "notified_remote": str(prev.get("notified_remote") or ""),
    }
    remote_key = state["remote"] or (
        f"{state['behind']} commit(s) en attente" if state["behind"] else ""
    )
    if state["available"] and not state["error"] and remote_key:
        if store is not None and state["notified_remote"] != remote_key:
            message = (
                f"Mise à jour disponible : {state['behind']} commit(s)"
                f" ({state['current'] or '?'} → {state['remote'] or 'origin'})."
                " Ouvrir Réglages → Mise à jour."
            )
            try:
                store.log("avertissement", message)
                state["notified_remote"] = remote_key
            except Exception as err:  # noqa: BLE001 - journal indisponible
                print(f"[update] avertissement non journalisé : {err}")
    elif not state["available"]:
        state["notified_remote"] = ""
    with UPDATE_CHECK_LOCK:
        _UPDATE_STATE = state
    save_update_state(state, path)
    if state["available"]:
        print(
            f"[update] mise à jour disponible : {state['behind']} commit(s)"
            f" ({state['current'] or '?'} → {state['remote'] or 'origin'})"
        )
    return state


def refresh_update_state(
    store=None, now: datetime | None = None, force: bool = False, path=None
) -> dict:
    """Vérifie les mises à jour si le cache a plus de 24 h (ou si ``force``).

    Ne lève jamais. Retourne l'état prêt pour l'interface.
    """
    now = now or datetime.now()
    previous = load_update_state(path)
    checked = _parse_timestamp(previous.get("checked_at"))
    fresh = (
        checked is not None
        and abs((now - checked).total_seconds()) < UPDATE_CHECK_MAX_AGE
    )
    if fresh and previous.get("current"):
        # Une mise à jour appliquée puis un redémarrage rendent le cache
        # obsolète : le commit courant ne correspond plus.
        try:
            if previous["current"] != ecasa_current_commit():
                fresh = False
        except Exception:  # noqa: BLE001
            pass
    if fresh and not force:
        global _UPDATE_STATE
        with UPDATE_CHECK_LOCK:
            _UPDATE_STATE = previous
        return update_state_snapshot()

    try:
        result = ecasa_update_check()
    except Exception as err:  # noqa: BLE001 - réseau, git absent…
        result = {
            "available": False,
            "behind": 0,
            "current": ecasa_current_commit(),
            "remote": "",
            "branch": "",
            "error": str(err),
        }
    _record_update_state(result, store=store, now=now, path=path, previous=previous)
    return update_state_snapshot()


# -------------------------------------------------------- métriques serveur
# Historique CPU (charge + température) sur les 60 dernières minutes, gardé en
# mémoire uniquement : rien n'est persisté, c'est une info serveur transitoire.

_CPU_HISTORY = []
_CPU_HISTORY_LOCK = threading.Lock()
_CPU_HISTORY_MAX = 60


def _read_cpu_load():
    """Charge CPU en pourcentage (moyenne sur 1 min, ramenée au nombre de cœurs)."""
    try:
        if hasattr(os, "getloadavg"):
            load1 = float(os.getloadavg()[0])
            cores = os.cpu_count() or 1
            return round(load1 / cores * 100.0, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        import psutil  # type: ignore
        return float(psutil.cpu_percent(interval=None))
    except Exception:  # noqa: BLE001
        return None


def _read_cpu_temp():
    """Température CPU en °C (Linux/Pi, puis psutil, puis vcgencmd)."""
    for path in ("/sys/class/thermal/thermal_zone0/temp",
                 "/sys/class/hwmon/hwmon0/temp1_input"):
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
            value = float(raw) / 1000.0
            if 0 < value < 150:
                return round(value, 1)
        except Exception:  # noqa: BLE001
            continue
    try:
        import psutil  # type: ignore
        sensors = getattr(psutil, "sensors_temperatures", None)
        if sensors:
            data = sensors() or {}
            for entries in data.values():
                for entry in entries or []:
                    value = getattr(entry, "current", None)
                    if value is not None and 0 < float(value) < 150:
                        return round(float(value), 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and "temp=" in (result.stdout or ""):
            raw = result.stdout.split("temp=", 1)[1].split("'", 1)[0]
            value = float(raw)
            if 0 < value < 150:
                return round(value, 1)
    except Exception:  # noqa: BLE001
        pass
    return None


def cpu_sample(now=None):
    """Prend une mesure CPU et l'ajoute à l'historique (60 points max)."""
    now = now or datetime.now()
    sample = {
        "ts": now.isoformat(timespec="seconds"),
        "load": _read_cpu_load(),
        "temp": _read_cpu_temp(),
    }
    with _CPU_HISTORY_LOCK:
        _CPU_HISTORY.append(sample)
        if len(_CPU_HISTORY) > _CPU_HISTORY_MAX:
            del _CPU_HISTORY[:len(_CPU_HISTORY) - _CPU_HISTORY_MAX]
    return sample


def cpu_history_snapshot():
    """Historique CPU prêt pour l'interface (60 dernières minutes)."""
    with _CPU_HISTORY_LOCK:
        samples = list(_CPU_HISTORY)
    return {
        "ts": [item["ts"] for item in samples],
        "load": [item["load"] for item in samples],
        "temp": [item["temp"] for item in samples],
    }


class CpuSampler:
    """Fil de fond : échantillonne le CPU toutes les minutes (60 points)."""

    def __init__(self, interval=60.0):
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="cpu-sampler", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        cpu_sample()
        while not self._stop.wait(self.interval):
            try:
                cpu_sample()
            except Exception as err:  # noqa: BLE001
                print("[cpu] échantillon impossible : %s" % err)


# -------------------------------------------------------- utilisation disque
# Instantanée pour la partition principale (shutil.disk_usage), avec une
# estimation grossière système vs applications/utilisateurs basée sur la taille
# des dossiers (mise en cache 10 min pour ne pas surcharger le serveur).

_DISK_CACHE = {"ts": None, "home": 0, "app": 0}
_DISK_CACHE_TTL = 600


def _dir_size(path) -> int:
    """Taille récursive d'un dossier en octets (best-effort, erreurs ignorées)."""
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for name in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def disk_usage_snapshot() -> dict:
    """Utilisation du disque principal pour le diagramme circulaire de l'accueil."""
    now = datetime.now()
    try:
        usage = shutil.disk_usage(str(ROOT))
    except Exception:  # noqa: BLE001
        try:
            usage = shutil.disk_usage("/")
        except Exception:  # noqa: BLE001
            return {"ts": now.isoformat(timespec="seconds"), "total": 0,
                    "used": 0, "free": 0, "system": 0, "apps": 0}
    total = int(usage.total)
    free = int(usage.free)
    used = int(usage.used)
    cache = _DISK_CACHE
    if cache["ts"] is None or (now - cache["ts"]).total_seconds() > _DISK_CACHE_TTL:
        home_path = Path.home()
        home_size = _dir_size(home_path) if home_path.exists() else 0
        app_size = _dir_size(ROOT)
        cache["home"] = home_size
        cache["app"] = app_size
        cache["ts"] = now
    apps = int(cache["home"])  # applications/utilisateurs (dossier personnel)
    system = max(0, used - apps)
    return {
        "ts": now.isoformat(timespec="seconds"),
        "total": total,
        "used": used,
        "free": free,
        "system": system,
        "apps": apps,
    }


def _pronote_locked_days(config: dict, room_id) -> dict:
    """Jours de cours Pronote verrouillés pour une pièce, par semaine (A/B).

    Les modèles appliqués à une pièce liée ne doivent jamais écraser ces
    journées : on les conserve telles quelles.
    """
    try:
        room_id = int(room_id)
    except (TypeError, ValueError):
        return {}
    raw_ids = config.get("pronote_room_ids")
    if isinstance(raw_ids, list):
        linked = []
        for value in raw_ids:
            try:
                linked.append(int(value))
            except (TypeError, ValueError):
                continue
    else:
        try:
            linked = [int(config.get("pronote_room_id") or 0)]
        except (TypeError, ValueError):
            linked = []
    if room_id not in linked:
        return {}
    weeks = []
    for value in config.get("pronote_weeks") or []:
        key = str(value).strip().upper()
        if key in ("A", "B") and key not in weeks:
            weeks.append(key)
    school_map = config.get("pronote_school_days") or {}
    per_room = school_map.get(str(room_id)) or {}
    if not weeks:
        for key in ("A", "B"):
            if any(bool(item) for item in (per_room.get(key) or [])):
                weeks.append(key)
    result = {}
    for week in weeks:
        days = per_room.get(week) or []
        result[week] = [
            bool(days[index]) if index < len(days) else False
            for index in range(7)
        ]
    return result


def _refresh_model_link(app, room_id) -> None:
    """Met à jour l'état « à synchroniser » d'une pièce liée à un modèle."""
    link = app.store.link_for_room(room_id)
    if not link:
        return
    config = load_config()
    locks = _pronote_locked_days(config, room_id)
    matches = app.store.room_matches_model(room_id, link.get("model_id"), locks)
    app.store.set_model_link_pending(room_id, not matches)


def _room_target_temp(store, room: dict) -> float | None:
    """Consigne cible de référence pour la table d'avance (prochaine montée ou actuelle)."""
    try:
        room_id = int(room.get("id"))
    except (TypeError, ValueError):
        return None
    actuator_id = room.get("actuator_device_id")
    device = None
    if actuator_id not in (None, "", 0, "0"):
        try:
            device = store.device(int(actuator_id))
        except (TypeError, ValueError):
            device = None
    if device is None:
        for candidate in store.devices():
            if int(candidate.get("room_id") or 0) == room_id:
                device = candidate
                break
    if device is None:
        return None
    try:
        from .engine import Engine as _Engine

        temp = None
        # Essaie de déduire une consigne pertinente depuis le planning courant.
        # (fallback simple : 19 °C si indisponible)
        # On ne dépend pas d'une instance Engine ici : on lit les slots du jour.
        settings = store.settings()
        now = datetime.now()
        week = week_type(now, settings, store.week_overrides())
        slots = store.slots(room_id=room_id, week=week)
        slot = find_slot(slots, now)
        if slot and slot.get("temp") is not None:
            temp = float(slot.get("temp"))
        if temp is None:
            return 19.0
        return temp
    except Exception:  # noqa: BLE001
        return 19.0


def _next_room_rise(app, room: dict):
    """Prochaine montée planifiée pour la pièce (même logique que le moteur)."""
    if not room or not room.get("heating_curve"):
        return None
    try:
        room_id = int(room.get("id"))
    except (TypeError, ValueError):
        return None
    actuator_id = room.get("actuator_device_id")
    if actuator_id in (None, "", 0, "0"):
        return None
    try:
        device = app.store.device(int(actuator_id))
    except (TypeError, ValueError):
        return None
    if not device:
        return None
    now = datetime.now()
    try:
        current_desired, _source, _until = app.engine.desired_for(device, now)
    except Exception:  # noqa: BLE001
        return None
    if not current_desired:
        return None
    try:
        rise = app.engine._next_rise(device, current_desired, now)
    except Exception:  # noqa: BLE001
        return None
    return rise


def _enrich_room_heating_curve(app, room: dict) -> dict:
    """Ajoute les informations de courbe de chauffe au snapshot de pièce."""
    if not room.get("heating_curve"):
        return room

    fit = room.get("curve_fit") if isinstance(room.get("curve_fit"), dict) else {}
    tau0 = fit.get("tau0")
    tau1 = fit.get("tau1")
    try:
        n = int(fit.get("n") or 0)
    except (TypeError, ValueError):
        n = 0
    if tau0 is None:
        tau0, tau1, learning = 3.0, 0.0, True
    else:
        try:
            tau0 = float(tau0)
            tau1 = float(tau1 or 0.0)
        except (TypeError, ValueError):
            tau0, tau1 = 3.0, 0.0
        learning = n < 3

    t_out = None
    try:
        t_out = app.engine._current_outside_temp()
    except Exception:  # noqa: BLE001
        t_out = None

    # Cible de référence pour la table : prochaine montée si connue, sinon consigne actuelle.
    target = 19.0
    rise = _next_room_rise(app, room)
    if rise and rise.get("temp") is not None:
        target = float(rise["temp"])
    else:
        ref = _room_target_temp(app.store, room)
        if ref is not None:
            target = float(ref)

    table = heating_curve.build_table(tau0, tau1, t_out, target)

    next_advance = None
    if rise and rise.get("in_minutes") is not None:
        try:
            t_room = app.engine._room_current_temp(room)
        except Exception:  # noqa: BLE001
            t_room = None
        if t_room is not None:
            minutes = heating_curve.advance_minutes(
                tau0, tau1, t_out, t_room, rise["temp"]
            )
            # Δt réel de la prochaine montée (même calcul que le moteur).
            next_advance = {
                "minutes": round(float(minutes), 1),
                "for_temp": round(float(rise["temp"]), 1),
            }

    room = dict(room)
    room["heating_curve"] = {
        "enabled": True,
        "tau0": round(float(tau0), 4),
        "tau1": round(float(tau1), 4),
        "n": n,
        "r2": round(float(fit.get("r2") or 0.0), 4) if isinstance(fit.get("r2"), (int, float)) else 0.0,
        "fit_at": fit.get("fit_at"),
        "learning": bool(learning),
        "eps": 0.2,
        "table": table,
        "next_advance": next_advance,
    }
    return room


def state_snapshot(app) -> dict:
    """Snapshot du moteur enrichi de l'état de mise à jour et des métriques CPU."""
    data = app.engine.snapshot()
    # Enrichissement des pièces avec la courbe de chauffe (snapshot room).
    try:
        rooms = data.get("rooms") or []
        data["rooms"] = [_enrich_room_heating_curve(app, room) for room in rooms]
    except Exception:  # noqa: BLE001
        pass
    try:
        data["model_links"] = app.store.room_model_links()
    except Exception:  # noqa: BLE001 - ne doit jamais casser le snapshot
        data["model_links"] = {}
    try:
        # Noms seuls (pas les plannings) : évite un GET /api/models à chaque
        # poll pour les pastilles « modèle » de l'accueil.
        data["model_names"] = {
            str(model.get("id")): str(model.get("name") or model.get("id"))
            for model in app.store.week_models()
        }
    except Exception:  # noqa: BLE001
        data["model_names"] = {}
    try:
        data["update"] = update_state_snapshot()
    except Exception:  # noqa: BLE001 - ne doit jamais casser le snapshot
        data["update"] = {"available": False}
    try:
        data["cpu"] = cpu_history_snapshot()
    except Exception:  # noqa: BLE001 - métriques facultatives
        data["cpu"] = {"ts": [], "load": [], "temp": []}
    try:
        data["disk"] = disk_usage_snapshot()
    except Exception:  # noqa: BLE001 - métriques facultatives
        data["disk"] = {"total": 0, "used": 0, "free": 0, "system": 0, "apps": 0}
    return data


class UpdateWatcher:
    """Fil de fond : vérifie une fois par jour les mises à jour disponibles."""

    def __init__(self, app, first_delay: float = 30,
                 interval: float = UPDATE_CHECK_INTERVAL):
        self.app = app
        self.first_delay = first_delay
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="update-watch", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(self.first_delay):
            return
        while not self._stop.is_set():
            try:
                refresh_update_state(self.app.store)
            except Exception as err:  # noqa: BLE001 - ne doit jamais tuer le fil
                print(f"[update] vérification impossible : {err}")
            self._stop.wait(self.interval)


def ecasa_update_apply() -> dict:
    """Applique la mise à jour (git pull) + dépendances, sans redémarrer."""
    root = ecasa_git_root()
    if root is None:
        return {"ok": False, "detail": "Dépôt git introuvable.", "command": ""}
    branch = ecasa_update_branch()
    # core.filemode=false : un simple changement de droit d'exécution (chmod +x
    # manuel, copie FAT/NTFS) ne doit pas bloquer le git pull.
    code, detail = _git(
        ["-c", "core.filemode=false", "pull", "--ff-only", "origin", branch],
        timeout=180,
    )
    if code != 0:
        return {
            "ok": False,
            "detail": detail or ("code %d" % code),
            "command": f"git pull --ff-only origin {branch}",
        }
    req = root / "requirements.txt"
    if req.exists():
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req)],
                cwd=str(root), capture_output=True, text=True, timeout=180, check=False,
            )
        except Exception:  # noqa: BLE001
            pass
    return {
        "ok": True,
        "detail": detail or "Mise à jour appliquée.",
        "command": f"git pull --ff-only origin {branch}",
    }


def apply_tailscale_startup(config: dict) -> dict:
    """Au démarrage : connecte le nœud et publie eCasa si demandé.

    Ne lève jamais : une erreur Tailscale ne doit pas empêcher eCasa de
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
    hostname = str(config.get("tailscale_hostname") or "ecasa").strip() or "ecasa"
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
                    if result["up"].get("auth_url"):
                        result["auth_url"] = result["up"]["auth_url"]
                        result["errors"].append(
                            "nœud à autoriser : ouvre l'URL affichée dans "
                            "Réglages → Tailscale"
                        )
                    if result["up"].get("warning"):
                        result["errors"].append(f"nom : {result['up']['warning']}")
                except Exception as err:  # noqa: BLE001
                    result["errors"].append(f"connexion : {err}")
            else:
                # Sans clé, `tailscale up` attend une autorisation manuelle :
                # on ne bloque pas le démarrage, l'utilisateur lance
                # « Connecter » depuis l'interface (clé ou lien).
                result["auth_url"] = current.get("auth_url")
                result["errors"].append(
                    "nœud non connecté : utilise « Connecter » dans "
                    "Réglages → Tailscale"
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
        consoapi_energy: EnergyService | None = None,
    ):
        self.store = store
        self.engine = engine
        self.weather = weather or WeatherService()
        self.energy = energy or EnergyService()
        self.consoapi_energy = consoapi_energy or EnergyService(provider="consoapi")
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
            elif method in ("GET", "HEAD"):
                self._static(path, write_body=method == "GET")
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
        if self.command != "HEAD":
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

    def _static(self, path: str, write_body: bool = True) -> None:
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
            if path in ("/", "/index.html", "/sw.js") or path.endswith(
                (".js", ".css")
            ):
                self.send_header("Cache-Control", "no-cache")
            else:
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
        if path in ("/", "/index.html", "/sw.js") or path.endswith(
            (".js", ".css")
        ):
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self._security_headers()
        self.end_headers()
        if write_body:
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        """Handle HEAD requests : mêmes en-têtes que GET, sans corps."""
        self._dispatch("HEAD")

    # -------------------------------------------------------------------- API

    def _loopback(self) -> bool:
        """Vrai si le client est la machine locale, le réseau local ou Tailscale.

        Autorise ainsi l'administration depuis le LAN / Tailscale sans avoir à
        activer explicitement « Autoriser les réglages depuis le réseau ».
        """
        addr = str(self.client_address[0] or "").strip()
        try:
            ip = ipaddress.ip_address(addr)
        except (ValueError, TypeError):
            return False
        # IPv4 encapsulée en IPv6 (::ffff:127.0.0.1, etc.)
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if ip.is_loopback:
            return True
        # Tailscale utilise la plage CGNAT 100.64.0.0/10.
        if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network(
            "100.64.0.0/10"
        ):
            return True
        # Réseau local privé (RFC 1918 et assimilés).
        if ip.is_private:
            return True
        return False

    def _schedule_shutdown(self, restart: bool) -> None:
        server = self.server
        engine = self.app.engine
        store = self.app.store

        def run() -> None:
            engine.stop()
            sync = getattr(self.app, "daily_sync", None)
            if sync is not None:
                sync.stop()
            thread = getattr(engine, "_thread", None)
            if thread and thread.is_alive():
                thread.join(timeout=20)
            if restart:
                # Un redémarrage doit aussi remettre à jour les services qui
                # tournent à côté de eCasa (pm2, unités systemd Node,
                # MyElectricalData local). Les erreurs sont seulement
                # journalisées : eCasa doit redémarrer dans tous les cas.
                try:
                    results = restart_external_services(load_config())
                except Exception as err:  # noqa: BLE001
                    results = [{
                        "name": "services externes",
                        "attempted": True,
                        "ok": False,
                        "detail": str(err),
                    }]
                for item in results:
                    if not item.get("attempted"):
                        continue
                    state = "ok" if item.get("ok") else "échec"
                    store.log(
                        "info" if item.get("ok") else "erreur",
                        f"Redémarrage {item['name']} : {state} — "
                        f"{item.get('detail') or ''}".strip(),
                    )
                    print(
                        f"[restart] {item['name']} : {state}"
                        f" {item.get('detail') or ''}".strip()
                    )
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
            self._json(200, state_snapshot(app))
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
            # Synchro manuelle : contourne le cache (préchargé à 3 h).
            refresh = (query.get("refresh") or [""])[0].lower() in (
                "1", "true", "oui", "yes",
            )
            # Option « à date » : les autres années s'arrêtent à la dernière
            # consommation connue de l'année en cours.
            as_of = (query.get("as_of") or [""])[0].lower() in (
                "1", "true", "oui", "yes",
            )
            config = load_config()
            provider = str(config.get("energy_provider") or "myelectricaldata").strip().lower()
            service = (app.consoapi_energy if provider == "consoapi"
                       else app.energy)
            self._json(
                200,
                build_energy(
                    service, config, view, month, day=day, days=days,
                    refresh=refresh, weather_service=app.weather,
                    as_of=as_of,
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
                room_id = int(match.group(1))
                body = dict(self._body() or {})
                # Champ serveur uniquement.
                body.pop("curve_fit", None)
                previous = app.store.room(room_id)
                enabled_before = bool((previous or {}).get("heating_curve"))
                room = app.store.update_room(room_id, body)
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            if room is None:
                self._json(404, {"error": "Pièce introuvable"})
                return
            # Recalcul du fit immédiatement à l'activation du toggle.
            if room.get("heating_curve") and not enabled_before:
                try:
                    app.engine.refresh_room_curve_fit(room_id, force=True)
                    room = app.store.room(room_id) or room
                except Exception:  # noqa: BLE001
                    try:
                        app.engine.mark_room_curve_fit_dirty(room_id)
                    except Exception:  # noqa: BLE001
                        pass
            elif room.get("heating_curve"):
                try:
                    app.engine.mark_room_curve_fit_dirty(room_id)
                except Exception:  # noqa: BLE001
                    pass
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
            body = self._body()
            try:
                updated, restart = save_config(body)
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            if "pronote_switch_day" in body:
                app.store.set_settings({
                    "week_switch_day": updated.get("pronote_switch_day") or "monday",
                })
                app.engine.tick(force=True)
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
            provider = str(
                (body or {}).get("energy_provider")
                or config.get("energy_provider")
                or "myelectricaldata"
            ).strip().lower()
            service = (app.consoapi_energy if provider == "consoapi"
                       else app.energy)
            self._json(200, test_energy(config, service=service))
            return

        # ----------------------------------------------------------- tailscale

        if method == "GET" and path == "/api/tailscale/status":
            try:
                cfg = load_config()
                hostname = str(cfg.get("tailscale_hostname") or "ecasa")
                status = get_tailscale_status(
                    int(cfg.get("port", 8780)), hostname, cfg.get("host"),
                )
                status["enabled"] = bool(cfg.get("tailscale_enabled"))
                status["auto_serve"] = bool(cfg.get("tailscale_auto_serve"))
                status["hostname"] = hostname
                status["authkey_set"] = bool(cfg.get("tailscale_authkey"))
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
                    port, str(cfg.get("tailscale_hostname") or "ecasa"),
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
                key = str(body.get("authkey") or "").strip()
                authkey = key or str(cfg.get("tailscale_authkey") or "").strip()
                hostname = str(cfg.get("tailscale_hostname") or "ecasa")
                port = int(cfg.get("port", 8780))
                result = tailscale_up(authkey, hostname=hostname)
                if key and key != cfg.get("tailscale_authkey"):
                    # La clé est conservée localement : la reconnexion au
                    # démarrage (reboot, coupure réseau) reste automatique.
                    try:
                        save_config({"tailscale_authkey": key})
                        result["authkey_saved"] = True
                    except Exception as err:  # noqa: BLE001
                        result["authkey_saved"] = False
                        result["detail"] = (
                            f"{result.get('detail') or ''} (clé non enregistrée : {err})"
                        ).strip()
                if result.get("auth_url"):
                    result["status"] = get_tailscale_status(
                        port, hostname, cfg.get("host"),
                    )
                self._json(200, result)
            except Exception as err:  # noqa: BLE001
                try:
                    cfg = load_config()
                    port = int(cfg.get("port", 8780))
                    hostname = str(cfg.get("tailscale_hostname") or "ecasa")
                    status = get_tailscale_status(port, hostname, cfg.get("host"))
                    auth_url = status.get("auth_url")
                except Exception:  # noqa: BLE001
                    status, auth_url = {}, None
                self._json(500, {
                    "error": str(err),
                    "auth_url": auth_url,
                    "status": status,
                })
            return

        if method == "POST" and path == "/api/tailscale/install":
            try:
                result = tailscale_install()
                self._json(200, result)
            except Exception as err:  # noqa: BLE001
                self._json(500, {"error": str(err)})
            return

        if method == "POST" and path == "/api/tailscale/uninstall":
            try:
                result = tailscale_uninstall()
                self._json(200, result)
            except Exception as err:  # noqa: BLE001
                self._json(500, {"error": str(err)})
            return

        if method == "GET" and path == "/api/update/check":
            try:
                result = ecasa_update_check()
                # Le cas « installé hors git » est normal : pas de bruit dans le
                # journal. On ne trace que les échecs réels (réseau, git absent).
                if result.get("is_git") and result.get("error"):
                    self.app.store.log(
                        "warn", f"Vérification de mise à jour : {result['error']}"
                    )
                elif result.get("is_git"):
                    # Le contrôle manuel met aussi à jour le cache quotidien
                    # (pastille jaune + avertissement dans le journal).
                    try:
                        _record_update_state(result, store=self.app.store)
                    except Exception as err:  # noqa: BLE001
                        print(f"[update] état non mémorisé : {err}")
                self._json(200, result)
            except Exception as err:  # noqa: BLE001
                self.app.store.log("warn", f"Vérification de mise à jour : {err}")
                self._json(500, {"error": str(err)})
            return

        if method == "POST" and path == "/api/update/apply":
            try:
                result = ecasa_update_apply()
                if result.get("ok"):
                    self.app.store.log(
                        "info", f"Mise à jour appliquée : {result.get('detail', '')}"
                    )
                else:
                    self.app.store.log(
                        "error", f"Mise à jour échouée : {result.get('detail', '')}"
                    )
                self._json(200, result)
                if result.get("ok"):
                    self._schedule_shutdown(restart=True)
            except Exception as err:  # noqa: BLE001
                self.app.store.log("error", f"Mise à jour échouée : {err}")
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
                "connectors": connector_registry.catalog(),
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
            self._json(200, state_snapshot(app))
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

        match = re.fullmatch(r"/api/connectors/([a-z]+)/(devices|apply)", path)
        if match and method == "GET" and match.group(2) == "devices":
            connector_id = match.group(1)
            if not connector_registry.connector(connector_id):
                self._json(404, {"error": "Connecteur inconnu"})
                return
            config = load_config()
            try:
                result = connector_registry.snapshot(
                    connector_id,
                    (config.get("connectors") or {}).get(connector_id) or {},
                )
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            except Exception as err:  # noqa: BLE001 - réseau
                self._json(502, {"error": f"Connecteur injoignable : {err}"})
                return
            self._json(200, result)
            return

        if match and method == "POST" and match.group(2) == "apply":
            connector_id = match.group(1)
            if not connector_registry.connector(connector_id):
                self._json(404, {"error": "Connecteur inconnu"})
                return
            body = self._body()
            try:
                device_id = body.get("device_id")
                mode = str(body.get("mode") or "heat")
                temp = float(body.get("temp") or 20)
                if mode not in dict(MODES):
                    raise ValueError("Mode invalide")
            except (TypeError, ValueError) as err:
                self._json(400, {"error": str(err)})
                return
            config = load_config()
            try:
                result = connector_registry.apply_state(
                    connector_id,
                    (config.get("connectors") or {}).get(connector_id) or {},
                    device_id,
                    mode,
                    temp,
                )
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            except Exception as err:  # noqa: BLE001 - réseau
                self._json(502, {"error": f"Commande refusée : {err}"})
                return
            app.store.log(
                "action",
                f"Connecteur {connector_id} : appareil {device_id} → {mode} "
                f"{temp:g} °C",
            )
            self._json(200, result)
            return

        if method == "POST" and path == "/api/connectors/test":
            body = self._body()
            config = load_config()
            connector_id = str(body.get("id") or "").strip()
            if not connector_id:
                self._json(400, {"error": "Connecteur manquant"})
                return
            self._json(200, connector_registry.test_connector(connector_id, config.get("connectors") or {}))
            return

        if method == "POST" and path == "/api/pronote/preview":
            body = self._body()
            config = load_config()
            options = dict(body)
            options["switch_day"] = (
                body.get("switch_day")
                or app.store.settings().get("week_switch_day")
                or config.get("pronote_switch_day")
                or "monday"
            )
            # Aperçu pour une pièce précise : applique ses réglages confort/réduit.
            raw_room = body.get("room_id")
            if raw_room not in (None, "", 0, "0"):
                try:
                    options.update(pronote_room_options(config, int(raw_room)))
                except (TypeError, ValueError):
                    pass
            try:
                monday = parse_week_start(body.get("week"))
                plan = build_pronote_plan(config, monday, options)
            except PronoteError as err:
                self._json(400, {"error": str(err)})
                return
            plan.update(week_info(app.store, monday))
            self._json(200, plan)
            return

        if method == "POST" and path == "/api/pronote/import":
            body = self._body()
            raw_room = body.get("room_id")
            use_room = raw_room not in (None, "", 0, "0")
            try:
                target_id = int(raw_room if use_room else body.get("device_id"))
            except (TypeError, ValueError):
                self._json(400, {"error": "room_id ou device_id manquant"})
                return

            if use_room:
                if not app.store.room(target_id):
                    self._json(404, {"error": "Pièce inconnue"})
                    return
            elif not app.store.device(target_id):
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
            try:
                week_starts = import_pronote_weeks(
                    app.store,
                    config,
                    target_id,
                    base_monday,
                    weeks,
                    room=use_room,
                    preview_days=preview_days,
                    preview_type=preview_type,
                    body=body,
                )
            except PronoteError as err:
                self._json(400, {"error": str(err)})
                return
            except (TypeError, ValueError) as err:
                self._json(400, {"error": f"Planning invalide : {err}"})
                return

            if use_room:
                if (
                    body.get("save_linked_weeks") is True
                    or body.get("linked_weeks") is not None
                ):
                    raw_linked = body.get("linked_weeks") or weeks
                    if isinstance(raw_linked, str):
                        raw_linked = [
                            part
                            for part in raw_linked.replace(",", " ").split()
                            if part
                        ]
                    linked = []
                    for value in raw_linked or []:
                        week = str(value).strip().upper()
                        if week in ("A", "B") and week not in linked:
                            linked.append(week)
                    if not linked:
                        linked = list(weeks)
                    config["pronote_weeks"] = linked
                else:
                    linked = pronote_linked_weeks(config)
                # Les semaines non importées doivent redevenir modifiables :
                # on efface leurs jours « Pronote » enregistrés.
                _sync_pronote_links(config, target_id, linked)
                with _config_write_lock:
                    fresh = load_config(force=True)
                    fresh["pronote_weeks"] = config.get("pronote_weeks") or []
                    fresh["pronote_school_days"] = config.get("pronote_school_days") or {}
                    _write_config_locked(fresh)

            detail = ", ".join(
                f"semaine {target} (du {start})"
                for target, start in week_starts.items()
            )
            if use_room:
                _refresh_model_link(app, target_id)
            target_label = "pièce" if use_room else "appareil"
            app.store.log(
                "info",
                f"Planning Pronote importé ({target_label} {target_id}) — {detail}",
                None if use_room else target_id,
            )
            app.engine.tick(force=True)
            self._json(200, {
                "ok": True,
                "room_id": target_id if use_room else None,
                "device_id": None if use_room else target_id,
                "weeks": weeks,
                "week_starts": week_starts,
                "week_start": base_monday.isoformat(),
                "days": preview_days,
            })
            return

        if method == "POST" and path == "/api/sync":
            app.engine.poll()
            app.engine.seed_demo()
            self._json(200, state_snapshot(app))
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
            device_info = app.store.device(device_id) or {}
            if device_info.get("room_id"):
                _refresh_model_link(app, device_info["room_id"])

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

        if method == "GET" and path == "/api/models":
            self._json(200, {
                "models": app.store.week_models(),
                "links": app.store.room_model_links(),
            })
            return

        if method == "PUT" and path == "/api/models":
            body = self._body()
            models = body.get("models")
            if models is None:
                models = body if isinstance(body, list) else []
            # Jours Pronote à préserver lors de la propagation aux pièces liées.
            config = load_config()
            locks = {
                key: _pronote_locked_days(config, key)
                for key in app.store.room_model_links()
            }
            try:
                saved = app.store.set_week_models(models, locked_days=locks)
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            app.engine.tick(force=True)
            app.store.flush(force=True)
            self._json(200, {
                "models": saved,
                "links": app.store.room_model_links(),
            })
            return

        match = re.fullmatch(r"/api/model-links/(\d+)", path)
        if match and method in ("POST", "PUT"):
            room_id = int(match.group(1))
            if app.store.room(room_id) is None:
                self._json(404, {"error": "Pièce inconnue"})
                return
            body = self._body() or {}
            model_id = str(body.get("model_id") or "").strip() or None
            try:
                app.store.set_room_model_link(room_id, model_id)
            except ValueError as err:
                self._json(400, {"error": str(err)})
                return
            if model_id:
                config = load_config()
                app.store.apply_model_to_room(
                    room_id, model_id, _pronote_locked_days(config, room_id)
                )
                model = app.store.week_model(model_id)
                app.store.log(
                    "info",
                    f"Pièce liée au modèle « {(model or {}).get('name', model_id)} »",
                    None,
                )
            else:
                app.store.log("info", "Pièce déliée de son modèle", None)
            app.engine.tick(force=True)
            app.store.flush(force=True)
            self._json(200, {
                "ok": True,
                "links": app.store.room_model_links(),
                "models": app.store.week_models(),
            })
            return

        if match and method == "DELETE":
            room_id = int(match.group(1))
            if app.store.room(room_id) is None:
                self._json(404, {"error": "Pièce inconnue"})
                return
            app.store.set_room_model_link(room_id, None)
            app.store.flush(force=True)
            self._json(200, {
                "ok": True,
                "links": app.store.room_model_links(),
            })
            return

        match = re.fullmatch(r"/api/model-links/(\d+)/update-model", path)
        if match and method == "POST":
            room_id = int(match.group(1))
            link = app.store.link_for_room(room_id)
            if not link:
                self._json(404, {"error": "Cette pièce n'est liée à aucun modèle"})
                return
            config = load_config()
            model = app.store.update_model_from_room(
                room_id, link.get("model_id"),
                _pronote_locked_days(config, room_id),
            )
            if model is None:
                self._json(404, {"error": "Modèle introuvable"})
                return
            # Le modèle est la source de vérité : on propage la mise à jour à
            # toutes les autres pièces liées au même modèle, en préservant
            # toujours leurs journées Pronote verrouillées.
            target_model_id = link.get("model_id")
            propagated = 0
            for key, other in app.store.room_model_links().items():
                try:
                    other_room_id = int(key)
                except (TypeError, ValueError):
                    continue
                if other_room_id == room_id:
                    continue
                other_model_id = (other or {}).get("model_id")
                if str(other_model_id) != str(target_model_id):
                    continue
                if app.store.apply_model_to_room(
                    other_room_id,
                    other_model_id,
                    _pronote_locked_days(config, key),
                ):
                    propagated += 1
            app.store.log(
                "info",
                f"Modèle « {model.get('name')} » mis à jour depuis la pièce "
                f"({propagated} pièce(s) liée(s) synchronisée(s))",
                None,
            )
            app.engine.tick(force=True)
            app.store.flush(force=True)
            self._json(200, {
                "ok": True,
                "model": model,
                "models": app.store.week_models(),
                "links": app.store.room_model_links(),
            })
            return

        if method == "PUT" and path == "/api/settings":
            body = self._body()
            settings = app.store.set_settings(body)
            if "week_switch_day" in body:
                with _config_write_lock:
                    config = load_config(force=True)
                    config["pronote_switch_day"] = (
                        "friday"
                        if str(settings.get("week_switch_day")).strip().lower()
                        == "friday"
                        else "monday"
                    )
                    _write_config_locked(config)
                app.engine.tick(force=True)
            app.store.flush(force=True)
            self._json(200, {"settings": settings})
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
            app.store.flush(force=True)
            self._json(200, {"device": device})
            return

        if match and method == "DELETE":
            if not app.store.device(int(match.group(1))):
                self._json(404, {"error": "Appareil inconnu"})
                return
            app.store.delete_device(int(match.group(1)))
            app.store.log("info", "Appareil retiré")
            app.store.flush(force=True)
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
                    _refresh_model_link(app, target_id)
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
            self._json(200, state_snapshot(app))
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
    env["ECASA_START_DELAY"] = "3"
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
    subprocess.Popen([sys.executable, "-m", "ecasa.main"], **kwargs)


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
        start_delay = float(os.environ.get("ECASA_START_DELAY", "0") or 0)
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

    weather_service = WeatherService()
    engine = Engine(store, client, wiser_client=wiser_client, weather=weather_service, config_getter=load_config)

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

    energy_cache = _resolve_path(config.get("energy_file"), "data/energy.json")
    app = App(
        store,
        engine,
        weather_service,
        EnergyService(cache_path=str(energy_cache)),
        EnergyService(provider="consoapi", cache_path=str(energy_cache)),
    )
    app.static.gzip_enabled = bool(config.get("gzip_enabled", True))
    count = app.static.load()
    server.app = app  # type: ignore[attr-defined]

    daily_sync = DailySync(app)
    app.daily_sync = daily_sync
    daily_sync.start()

    # Veille des mises à jour : première vérification ~30 s après le démarrage,
    # puis une tentative toutes les 6 h (le cache limite à un contrôle par jour).
    update_watch = UpdateWatcher(app)
    app.update_watch = update_watch
    update_watch.start()

    # Échantillonnage CPU : une mesure par minute, 60 points conservés en mémoire.
    cpu_sampler = CpuSampler()
    app.cpu_sampler = cpu_sampler
    cpu_sampler.start()

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
        daily_sync.stop()
        update_watch.stop()
        cpu_sampler.stop()
        engine.stop()
        store.close()
        server.server_close()


if __name__ == "__main__":
    main()

