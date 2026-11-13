"""Connecteurs d'appareils autres qu'Atlantic et Wiser.

Chaque connecteur expose trois opérations, testables sans matériel grâce à
``_http`` (monkeypatché dans les tests) :

- ``snapshot(settings)`` : lit l'état des appareils (découverte + consignes) ;
- ``apply(settings, device, mode, temp)`` : applique un mode et une consigne ;
- ``test_connector(id, settings)`` : diagnostic affiché dans l'interface.

Protocoles :
- **Daikin** : API HTTP locale du boîtier Wi-Fi (``/aircon/...``) ;
- **Netatmo** : API cloud OAuth2 (refresh token) ;
- **Mitsubishi** : MELCloud (login + ListDevices + SetAta) ;
- **Bosch** : API locale du contrôleur Smart Home (HTTPS + certificat client).
  L'appairage se fait par un POST sur ``/smarthome/clients`` avec le mot de passe
  du contrôleur et le bouton physique maintenu enfoncé.
"""

from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MELCLOUD_URL = "https://app.melcloud.com/Mitsubishi.Wifi.Client"
NETATMO_URL = "https://api.netatmo.com"

CONNECTORS = [
    {
        "id": "daikin",
        "label": "Daikin",
        "kind": "local",
        "status": "ready",
        "description": "Climatisation Daikin via le boîtier Wi-Fi local (API HTTP).",
        "fields": [
            {
                "key": "host",
                "label": "Adresse du boîtier",
                "type": "text",
                "placeholder": "192.168.1.50",
            },
            {
                "key": "key",
                "label": "Clé d'accès",
                "type": "password",
                "secret": True,
                "placeholder": "facultatif",
            },
        ],
    },
    {
        "id": "bosch",
        "label": "Bosch",
        "kind": "local",
        "status": "ready",
        "description": (
            "Thermostats Bosch Smart Home via l'API locale du contrôleur. "
            "Le premier accès demande le mot de passe du contrôleur et un appui "
            "sur son bouton (certificat client généré automatiquement)."
        ),
        "fields": [
            {
                "key": "host",
                "label": "Adresse du contrôleur",
                "type": "text",
                "placeholder": "192.168.1.20",
            },
            {
                "key": "port",
                "label": "Port de l'API",
                "type": "text",
                "placeholder": "8444",
            },
            {
                "key": "password",
                "label": "Mot de passe du contrôleur",
                "type": "password",
                "secret": True,
            },
            {
                "key": "client_cert",
                "label": "Certificat client (facultatif)",
                "type": "password",
                "secret": True,
                "placeholder": "généré automatiquement",
            },
            {
                "key": "client_key",
                "label": "Clé privée client (facultatif)",
                "type": "password",
                "secret": True,
                "placeholder": "généré automatiquement",
            },
        ],
    },
    {
        "id": "netatmo",
        "label": "Netatmo",
        "kind": "cloud",
        "status": "ready",
        "description": "Thermostats et vannes Netatmo (API cloud, compte développeur).",
        "fields": [
            {"key": "client_id", "label": "Client ID", "type": "text"},
            {
                "key": "client_secret",
                "label": "Client secret",
                "type": "password",
                "secret": True,
            },
            {
                "key": "refresh_token",
                "label": "Refresh token",
                "type": "password",
                "secret": True,
            },
        ],
    },
    {
        "id": "mitsubishi",
        "label": "Mitsubishi",
        "kind": "cloud",
        "status": "ready",
        "description": "Climatisation Mitsubishi via MELCloud.",
        "fields": [
            {"key": "email", "label": "Identifiant MELCloud", "type": "text"},
            {
                "key": "password",
                "label": "Mot de passe MELCloud",
                "type": "password",
                "secret": True,
            },
        ],
    },
]

# Modes eCasa ⇄ modes natifs.
DAIKIN_MODES = {"auto": "0", "dry": "1", "cool": "2", "heat": "3", "fan": "4"}
DAIKIN_MODES_REVERSE = {value: key for key, value in DAIKIN_MODES.items()}
MELCLOUD_MODES = {"heat": 1, "dry": 2, "cool": 3, "fan": 7, "auto": 8}
MELCLOUD_MODES_REVERSE = {value: key for key, value in MELCLOUD_MODES.items()}


# --------------------------------------------------------------------- HTTP


def _http(method: str, url: str, headers: dict | None = None,
          body: bytes | None = None, timeout: int = 10):
    """Requête HTTP brute, injectable dans les tests."""
    request = urllib.request.Request(
        url, data=body, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def _json_call(method, url, headers=None, payload=None, timeout=10) -> dict:
    body = None
    headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    status, raw = _http(method, url, headers, body, timeout)
    try:
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise ValueError(f"Réponse illisible (HTTP {status})")
    if status >= 400:
        message = ""
        if isinstance(data, dict):
            message = str(data.get("error") or data.get("Message") or "")
        raise ValueError(f"HTTP {status}" + (f" : {message}" if message else ""))
    return data if isinstance(data, dict) else {"data": data}


def _kv(text: str) -> dict:
    """Analyse une réponse Daikin « ret=OK,pow=1,mode=3,… »."""
    result = {}
    for part in str(text or "").split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------- Daikin


def _daikin_base(host: str) -> str:
    base = str(host or "").strip().rstrip("/")
    if not base:
        raise ValueError("Adresse du boîtier manquante")
    if not base.startswith(("http://", "https://")):
        base = f"http://{base}"
    return base


def _daikin_get(base: str, path: str, timeout: int = 6) -> dict:
    status, raw = _http("GET", f"{base}/{path.lstrip('/')}", timeout=timeout)
    text = raw.decode("ascii", "replace")
    if status >= 400:
        raise ValueError(f"Boîtier Daikin : HTTP {status}")
    data = _kv(text)
    if data.get("ret") != "OK":
        raise ValueError("Réponse inattendue du boîtier Daikin")
    return data


def daikin_snapshot(settings: dict) -> dict:
    base = _daikin_base(settings.get("host"))
    info = _daikin_get(base, "common/basic_info")
    control = _daikin_get(base, "aircon/get_control_info")
    sensor = _daikin_get(base, "aircon/get_sensor_info")
    power = str(control.get("pow", "0")) == "1"
    mode = DAIKIN_MODES_REVERSE.get(str(control.get("mode", "")), "auto")
    return {
        "ok": True,
        "devices": [
            {
                "id": str(info.get("mac") or info.get("id") or settings.get("host")),
                "name": str(info.get("name") or "Daikin"),
                "mode": mode if power else "off",
                "temp": _float(control.get("stemp"), 20.0),
                "ambient": _float(sensor.get("htemp")),
                "outside": _float(sensor.get("otemp")),
                "online": True,
            }
        ],
    }


def daikin_apply(settings: dict, mode: str, temp: float) -> dict:
    base = _daikin_base(settings.get("host"))
    control = _daikin_get(base, "aircon/get_control_info")
    payload = {
        "pow": "1" if mode != "off" else "0",
        "mode": DAIKIN_MODES.get(mode if mode != "off" else "auto", "3"),
        "stemp": f"{float(temp):.1f}",
        "shum": str(control.get("shum", "0") or "0"),
        "f_rate": str(control.get("f_rate", "A") or "A"),
        "f_dir": str(control.get("f_dir", "0") or "0"),
    }
    query = urllib.parse.urlencode(payload)
    _daikin_get(base, f"aircon/set_control_info?{query}")
    return {"ok": True, "applied": payload}


# ---------------------------------------------------------------- MELCloud


def _melcloud_login(settings: dict) -> str:
    email = str(settings.get("email") or "").strip()
    password = str(settings.get("password") or "")
    if not email or not password:
        raise ValueError("Identifiant ou mot de passe MELCloud manquant")
    data = _json_call(
        "POST",
        f"{MELCLOUD_URL}/Login/ClientLogin",
        payload={"Email": email, "Password": password, "AppVersion": "1.0.0.0"},
    )
    context = str((data.get("LoginData") or {}).get("ContextKey") or "")
    if not context:
        raise ValueError("Connexion MELCloud refusée")
    return context


def melcloud_snapshot(settings: dict) -> dict:
    context = _melcloud_login(settings)
    devices = _json_call(
        "GET",
        f"{MELCLOUD_URL}/User/ListDevices",
        headers={"X-MitsContextKey": context},
    )
    items = devices if isinstance(devices, list) else devices.get("data") or []
    result = []
    for item in items:
        if not isinstance(item, dict) or "DeviceID" not in item:
            continue
        power = bool(item.get("Power"))
        mode = MELCLOUD_MODES_REVERSE.get(int(item.get("OperationMode") or 0), "auto")
        result.append(
            {
                "id": str(item["DeviceID"]),
                "name": str(item.get("DeviceName") or f"Clim {item['DeviceID']}"),
                "mode": mode if power else "off",
                "temp": _float(item.get("SetTemperature"), 20.0),
                "ambient": _float(item.get("RoomTemperature")),
                "outside": _float(item.get("OutdoorTemperature")),
                "online": bool(item.get("DeviceOnline", True)),
            }
        )
    if not result:
        raise ValueError("Aucun appareil MELCloud sur ce compte")
    return {"ok": True, "devices": result, "context": context}


def melcloud_apply(settings: dict, device_id, mode: str, temp: float) -> dict:
    context = _melcloud_login(settings)
    payload = {
        "DeviceID": int(device_id),
        "Power": mode != "off",
        "OperationMode": MELCLOUD_MODES.get(mode if mode != "off" else "heat", 1),
        "SetTemperature": float(temp),
    }
    _json_call(
        "POST",
        f"{MELCLOUD_URL}/Device/SetAta",
        headers={"X-MitsContextKey": context},
        payload=payload,
    )
    return {"ok": True, "applied": payload}


# ------------------------------------------------------------------ Netatmo


def _netatmo_token(settings: dict) -> str:
    client_id = str(settings.get("client_id") or "").strip()
    client_secret = str(settings.get("client_secret") or "")
    refresh = str(settings.get("refresh_token") or "")
    if not client_id or not client_secret or not refresh:
        raise ValueError("Client ID, secret et refresh token Netatmo requis")
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
        }
    ).encode("utf-8")
    status, raw = _http(
        "POST",
        f"{NETATMO_URL}/oauth2/token",
        {"Content-Type": "application/x-www-form-urlencoded"},
        body,
        10,
    )
    try:
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise ValueError(f"Réponse Netatmo illisible (HTTP {status})")
    token = str(data.get("access_token") or "")
    if status >= 400 or not token:
        raise ValueError("Connexion Netatmo refusée")
    return token


def netatmo_snapshot(settings: dict) -> dict:
    token = _netatmo_token(settings)
    headers = {"Authorization": f"Bearer {token}"}
    homes = _json_call("GET", f"{NETATMO_URL}/api/homesdata", headers=headers).get(
        "body", {}
    )
    result = []
    for home in homes.get("homes") or []:
        home_id = home.get("id")
        if not home_id:
            continue
        status_payload = _json_call(
            "GET",
            f"{NETATMO_URL}/api/homestatus?{urllib.parse.urlencode({'home_id': home_id})}",
            headers=headers,
        ).get("body", {})
        home_status = status_payload.get("home") or {}
        for room in home_status.get("rooms") or []:
            setpoint = room.get("therm_setpoint_temperature")
            result.append(
                {
                    "id": f"{home_id}:{room.get('id')}",
                    "name": str(room.get("name") or "Pièce Netatmo"),
                    "mode": "heat" if room.get("therm_setpoint_mode") != "off" else "off",
                    "temp": _float(setpoint, 20.0),
                    "ambient": _float(room.get("therm_measured_temperature")),
                    "outside": None,
                    "online": bool(room.get("reachable", True)),
                }
            )
    if not result:
        raise ValueError("Aucune pièce Netatmo sur ce compte")
    return {"ok": True, "devices": result}


def netatmo_apply(settings: dict, device_id, mode: str, temp: float) -> dict:
    token = _netatmo_token(settings)
    home_id, _, room_id = str(device_id).partition(":")
    if not home_id or not room_id:
        raise ValueError("Identifiant de pièce Netatmo invalide")
    payload = {
        "home_id": home_id,
        "room_id": room_id,
        "mode": "off" if mode == "off" else "manual",
    }
    if mode != "off":
        payload["temp"] = float(temp)
    _json_call(
        "POST",
        f"{NETATMO_URL}/api/setroomthermpoint",
        headers={"Authorization": f"Bearer {token}"},
        payload=payload,
    )
    return {"ok": True, "applied": payload}


# ------------------------------------------------------------------ Bosch

BOSCH_DEFAULT_PORT = 8444
BOSCH_PAIRING_ID = "oss_ecasa"
BOSCH_SERVICE = "RoomClimateControl"
BOSCH_MODELS = ("TRV", "THB", "BWTH", "RTH2", "ROOM_CLIMATE_CONTROL")


def _http_tls(method: str, url: str, headers: dict | None = None,
              body: bytes | None = None, timeout: int = 10, context=None):
    """Requête HTTPS avec certificat client (mTLS), injectable dans les tests."""
    request = urllib.request.Request(
        url, data=body, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def _bosch_target(settings: dict) -> tuple[str, int]:
    raw = str(settings.get("host") or "").strip().rstrip("/")
    if not raw:
        raise ValueError("Adresse du contrôleur Bosch manquante.")
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    port = int(_float(settings.get("port"), BOSCH_DEFAULT_PORT) or BOSCH_DEFAULT_PORT)
    host = raw
    candidate_host, _, candidate_port = raw.rpartition(":")
    if candidate_port.isdigit() and candidate_host:
        host, port = candidate_host, int(candidate_port)
    if not 0 < port < 65536:
        raise ValueError("Port du contrôleur Bosch invalide.")
    return host, port


def _bosch_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "bosch"


def _bosch_generate_certificate(cert_path: Path, key_path: Path) -> None:
    """Génère un certificat client auto-signé 2048 bits (paquet cryptography)."""
    try:
        import datetime as _datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as err:  # pragma: no cover - dépendance facultative
        raise ValueError(
            "Certificat client Bosch manquant : renseigne les champs « Certificat "
            "client » et « Clé privée client », ou installe le paquet "
            "« cryptography » pour les générer automatiquement."
        ) from err
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "eCasa")])
    now = _datetime.datetime.now(_datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _datetime.timedelta(days=1))
        .not_valid_after(now + _datetime.timedelta(days=9999))
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def _bosch_credentials(settings: dict) -> tuple[str, str]:
    """Chemins des PEM client : fournis dans les réglages ou générés/cachés."""
    directory = _bosch_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / "client-cert.pem"
    key_path = directory / "client-key.pem"
    for key_name, path in (("client_cert", cert_path), ("client_key", key_path)):
        value = str(settings.get(key_name) or "").strip()
        if value:
            path.write_text(value if value.endswith("\n") else value + "\n",
                            encoding="utf-8")
    if not (cert_path.exists() and key_path.exists()):
        _bosch_generate_certificate(cert_path, key_path)
    return str(cert_path), str(key_path)


def _bosch_certificate(cert_path) -> str:
    return Path(cert_path).read_text(encoding="utf-8")


def _bosch_ssl_context(cert_path, key_path):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context


def _bosch_call(method: str, url: str, *, headers: dict | None = None,
                payload=None, context=None, timeout: int = 15):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    head = {"Content-Type": "application/json", "api-version": "3.12"}
    head.update(headers or {})
    status, raw = _http_tls(method, url, head, body, timeout, context)
    try:
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        data = {}
    return status, data


def _bosch_password(settings: dict) -> str:
    password = str(settings.get("password") or "")
    if not password:
        raise ValueError("Mot de passe du contrôleur Bosch manquant.")
    return password


def _bosch_pairing_ports(port: int) -> list[int]:
    ports = [port - 1] if port > 1 else []
    ports.append(port)
    return ports


def _bosch_flatten_certificate(pem: str) -> str:
    """Format exigé par le contrôleur : PEM sur une ligne, CR autour des bornes."""
    text = str(pem or "").replace("\r", "").replace("\n", "")
    return (
        text.replace("-----BEGIN CERTIFICATE-----", "-----BEGIN CERTIFICATE-----\r")
        .replace("-----END CERTIFICATE-----", "\r-----END CERTIFICATE-----")
    )


def _bosch_pair(settings: dict, host: str, port: int, cert_path, context) -> None:
    """Déclare le client auprès du contrôleur (bouton physique maintenu)."""
    payload = {
        "@type": "client",
        "id": BOSCH_PAIRING_ID,
        "name": BOSCH_PAIRING_ID,
        "primaryRole": "ROLE_RESTRICTED_CLIENT",
        "certificate": _bosch_flatten_certificate(_bosch_certificate(cert_path)),
    }
    headers = {
        "Systempassword": base64.b64encode(
            _bosch_password(settings).encode("utf-8")
        ).decode("ascii"),
    }
    ports = _bosch_pairing_ports(port)
    for candidate in ports:
        url = f"https://{host}:{candidate}/smarthome/clients"
        try:
            status, data = _bosch_call(
                "POST", url, headers=headers, payload=payload,
                context=context, timeout=20,
            )
        except OSError:
            continue
        if status < 400:
            return
        message = ""
        if isinstance(data, dict):
            message = str(data.get("message") or data.get("error") or "")
        if status == 401:
            raise ValueError("Contrôleur Bosch : mot de passe refusé.")
        if status in (400, 403):
            raise ValueError(
                "Contrôleur Bosch : appairage refusé. Maintiens le bouton du "
                "contrôleur enfoncé pendant toute la durée de l'appairage, "
                "puis relance le test."
            )
        raise ValueError(
            f"Contrôleur Bosch : appairage impossible (HTTP {status}"
            + (f" : {message}" if message else "") + ")."
        )
    raise ValueError(
        "Contrôleur Bosch injoignable en HTTPS pour l'appairage (ports essayés : "
        + ", ".join(str(item) for item in ports)
        + "). Vérifie l'adresse et que le contrôleur est allumé."
    )


def _bosch_session(settings: dict):
    """Ouvre une session mTLS et renvoie (base, contexte, appareils)."""
    _bosch_password(settings)
    host, port = _bosch_target(settings)
    cert_path, key_path = _bosch_credentials(settings)
    context = _bosch_ssl_context(cert_path, key_path)
    base = f"https://{host}:{port}/smarthome"
    status, data = _bosch_call("GET", f"{base}/devices", context=context)
    if status in (401, 403):
        _bosch_pair(settings, host, port, cert_path, context)
        status, data = _bosch_call("GET", f"{base}/devices", context=context)
    if status in (401, 403):
        raise ValueError(
            "Contrôleur Bosch : client non appairé. Maintiens le bouton du "
            "contrôleur enfoncé, puis relance le test."
        )
    if status == 404:
        raise ValueError(
            f"Contrôleur Bosch introuvable sur le port {port} "
            "(l'API locale est en général sur 8444)."
        )
    if status >= 400:
        raise ValueError(f"Contrôleur Bosch : HTTP {status}")
    devices = data if isinstance(data, list) else (data or {}).get("devices") or []
    return base, context, devices


def _bosch_service_ids(device: dict) -> list[str]:
    services = device.get("deviceServiceIds") or device.get("services") or []
    result = []
    for item in services:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            value = item.get("id") or item.get("@type")
            if value:
                result.append(str(value))
    return result


def _bosch_state(base: str, context, device_id, service_id: str) -> dict:
    path = urllib.parse.quote(str(device_id), safe="")
    status, data = _bosch_call(
        "GET", f"{base}/devices/{path}/services/{service_id}",
        context=context, timeout=10,
    )
    if status >= 400 or not isinstance(data, dict):
        return {}
    state = data.get("state")
    return state if isinstance(state, dict) else data


def bosch_snapshot(settings: dict) -> dict:
    base, context, devices = _bosch_session(settings)
    result = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        services = _bosch_service_ids(device)
        model = str(device.get("deviceModel") or device.get("productId") or "")
        if BOSCH_SERVICE not in services and not model.startswith(BOSCH_MODELS):
            continue
        device_id = device.get("id") or device.get("deviceId")
        if not device_id:
            continue
        climate = (
            _bosch_state(base, context, device_id, BOSCH_SERVICE)
            if BOSCH_SERVICE in services else {}
        )
        thermal = _bosch_state(base, context, device_id, "TemperatureLevel")
        ambient = _float(thermal.get("temperature"))
        temp = _float(climate.get("setpointTemperature"), ambient if ambient is not None else 20.0)
        result.append(
            {
                "id": str(device_id),
                "name": str(device.get("name") or "Appareil Bosch"),
                "mode": (
                    "cool"
                    if str(climate.get("roomControlMode") or "").upper() == "COOLING"
                    else "heat"
                ),
                "temp": temp,
                "ambient": ambient,
                "outside": None,
                "online": (
                    str(device.get("status") or "AVAILABLE").upper() == "AVAILABLE"
                ),
            }
        )
    if not result:
        raise ValueError(
            "Aucun thermostat Bosch pilotable détecté. Vérifie que le "
            "contrôleur expose bien des thermostats (TRV, THB, RTH2…)."
        )
    return {"ok": True, "devices": result}


def bosch_apply(settings: dict, device_id, mode: str, temp: float) -> dict:
    base, context, devices = _bosch_session(settings)
    target = None
    for device in devices:
        if not isinstance(device, dict):
            continue
        if str(device.get("id") or device.get("deviceId") or "") == str(device_id):
            target = device
            break
    if target is None:
        raise ValueError("Appareil Bosch inconnu sur ce contrôleur.")
    if BOSCH_SERVICE not in _bosch_service_ids(target):
        raise ValueError("Cet appareil Bosch n'accepte pas de consigne de température.")
    state = _bosch_state(base, context, device_id, BOSCH_SERVICE)
    payload = {"@type": state.get("@type") or "climateControlState"}
    if mode == "off":
        payload["setpointTemperature"] = 5.0
        payload["operationMode"] = "MANUAL"
        payload["roomControlMode"] = "HEATING"
    else:
        payload["setpointTemperature"] = min(30.0, max(5.0, _float(temp, 20.0)))
        payload["operationMode"] = "MANUAL"
        payload["roomControlMode"] = "COOLING" if mode == "cool" else "HEATING"
    path = urllib.parse.quote(str(device_id), safe="")
    status, data = _bosch_call(
        "PUT", f"{base}/devices/{path}/services/{BOSCH_SERVICE}/state",
        payload=payload, context=context, timeout=20,
    )
    if status >= 400:
        message = str((data or {}).get("message") or "") if isinstance(data, dict) else ""
        raise ValueError(
            "Contrôleur Bosch : commande refusée"
            + (f" ({message})" if message else f" (HTTP {status})")
        )
    return {"ok": True, "applied": payload}


SNAPSHOTS = {
    "daikin": daikin_snapshot,
    "bosch": bosch_snapshot,
    "netatmo": netatmo_snapshot,
    "mitsubishi": melcloud_snapshot,
}
APPLIERS = {
    "daikin": lambda settings, device, mode, temp: daikin_apply(settings, mode, temp),
    "bosch": bosch_apply,
    "netatmo": netatmo_apply,
    "mitsubishi": melcloud_apply,
}


# ------------------------------------------------------------------ registre


def connector_ids() -> list[str]:
    return [item["id"] for item in CONNECTORS]


def connector(connector_id: str):
    for item in CONNECTORS:
        if item["id"] == connector_id:
            return item
    return None


def catalog() -> list[dict]:
    """Catalogue public (sans valeur) pour l'interface."""
    return [
        {
            "id": item["id"],
            "label": item["label"],
            "kind": item["kind"],
            "status": item["status"],
            "description": item["description"],
            "fields": [
                {key: value for key, value in field.items() if key != "secret"}
                | ({"secret": True} if field.get("secret") else {})
                for field in item["fields"]
            ],
        }
        for item in CONNECTORS
    ]


def default_settings() -> dict:
    return {
        item["id"]: {field["key"]: "" for field in item["fields"]}
        for item in CONNECTORS
    }


def public_settings(config: dict) -> dict:
    """Réglages sans secrets : booléen ``<champ>_set`` à la place."""
    stored = config.get("connectors") or {}
    result: dict[str, dict] = {}
    for item in CONNECTORS:
        values = stored.get(item["id"]) or {}
        entry: dict[str, object] = {}
        for field in item["fields"]:
            value = str(values.get(field["key"]) or "")
            if field.get("secret"):
                entry[f"{field['key']}_set"] = bool(value)
            else:
                entry[field["key"]] = value
        result[item["id"]] = entry
    return result


def merge_settings(body, current: dict) -> dict:
    """Valide les réglages reçus et conserve les secrets laissés vides."""
    if not isinstance(body, dict):
        raise ValueError("Réglages de connecteur invalides")
    current = current if isinstance(current, dict) else {}
    cleaned: dict[str, dict] = {}
    for raw_id, values in body.items():
        item = connector(str(raw_id))
        if not item:
            continue
        values = values if isinstance(values, dict) else {}
        previous = current.get(str(raw_id)) or {}
        entry: dict[str, str] = {}
        for field in item["fields"]:
            key = field["key"]
            raw = values.get(key)
            value = "" if raw is None else str(raw).strip()
            if not value and field.get("secret"):
                value = str(previous.get(key) or "")
            entry[key] = value
        if any(entry.values()):
            cleaned[str(raw_id)] = entry
    return cleaned


def test_connector(connector_id: str, settings: dict) -> dict:
    """Diagnostic : lit réellement l'état des appareils du connecteur."""
    item = connector(connector_id)
    if not item:
        return {"ok": False, "error": "Connecteur inconnu"}
    snapshot = SNAPSHOTS.get(connector_id)
    if snapshot is None:
        return {"ok": False, "error": "Connecteur non pris en charge"}
    values = (settings or {}).get(connector_id) or {}
    try:
        result = snapshot(values)
    except ValueError as err:
        return {"ok": False, "error": str(err)}
    except Exception as err:  # noqa: BLE001 - réseau
        return {"ok": False, "error": f"{item['label']} injoignable : {err}"}
    devices = result.get("devices") or []
    return {
        "ok": True,
        "message": f"{item['label']} : {len(devices)} appareil(s) détecté(s).",
        "devices": devices,
    }


def snapshot(connector_id: str, settings: dict) -> dict:
    """État normalisé des appareils d'un connecteur."""
    function = SNAPSHOTS.get(connector_id)
    if function is None:
        raise ValueError("Connecteur non pris en charge")
    return function(settings or {})


def apply_state(connector_id: str, settings: dict, device_id, mode: str,
                temp: float) -> dict:
    """Applique un mode et une consigne à un appareil du connecteur."""
    function = APPLIERS.get(connector_id)
    if function is None:
        raise ValueError("Connecteur non pris en charge")
    return function(settings or {}, device_id, mode, temp)
