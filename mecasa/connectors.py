"""Connecteurs d'appareils autres qu'Atlantic et Wiser.

Chaque connecteur décrit ses champs de configuration. Les réglages peuvent être
enregistrés dès maintenant ; les pilotes (lecture et écriture des consignes)
sont livrés au fur et à mesure des versions. Le catalogue est exposé par
``GET /api/meta`` et rendu sous forme de panneaux dépliables dans Réglages →
Appareils.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

CONNECTORS = [
    {
        "id": "daikin",
        "label": "Daikin",
        "kind": "local",
        "status": "beta",
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
        "status": "planned",
        "description": "Thermostats Bosch Smart Home via le contrôleur local.",
        "fields": [
            {
                "key": "host",
                "label": "Adresse du contrôleur",
                "type": "text",
                "placeholder": "192.168.1.20",
            },
            {
                "key": "token",
                "label": "Jeton d'accès",
                "type": "password",
                "secret": True,
            },
        ],
    },
    {
        "id": "netatmo",
        "label": "Netatmo",
        "kind": "cloud",
        "status": "planned",
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
        "status": "planned",
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
                # Champ secret laissé vide : on garde la valeur enregistrée.
                value = str(previous.get(key) or "")
            entry[key] = value
        if any(entry.values()):
            cleaned[str(raw_id)] = entry
    return cleaned


def test_connector(connector_id: str, settings: dict, timeout: int = 4) -> dict:
    """Teste un connecteur.

    Seul Daikin dispose d'une sonde réseau (API HTTP locale) ; les autres
    répondent honnêtement que le pilote est en cours d'écriture.
    """
    item = connector(connector_id)
    if not item:
        return {"ok": False, "error": "Connecteur inconnu"}
    values = (settings or {}).get(connector_id) or {}

    if connector_id == "daikin":
        host = str(values.get("host") or "").strip()
        if not host:
            return {"ok": False, "error": "Adresse du boîtier manquante"}
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        url = f"{host.rstrip('/')}/aircon/get_control_info"
        request = urllib.request.Request(
            url, headers={"User-Agent": "MeCasa/1.0 (test connecteur)"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read(2000).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as err:
            return {"ok": False, "error": f"Boîtier injoignable : {err}"}
        if "ret=OK" not in payload and "ret%3DOK" not in payload:
            return {
                "ok": False,
                "error": "Réponse inattendue du boîtier Daikin",
                "raw": payload[:300],
            }
        return {"ok": True, "message": "Boîtier Daikin joignable."}

    return {
        "ok": False,
        "status": item["status"],
        "message": (
            f"Connecteur {item['label']} : réglages enregistrés, pilote en cours "
            "d'écriture (suivre les versions)."
        ),
    }
