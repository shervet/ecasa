"""Regles de planning : type de semaine et creneau actif.

Le principe est celui d'un thermostat programmable : chaque creneau definit
un etat (mode + consigne) qui s'applique jusqu'au creneau suivant. Un creneau
manquant en debut de journee herite donc de l'etat de la veille, ce qui rend
les nuits et les week-ends simples a decrire.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# Libelles affiches et valeur envoyee a la clim (capacite 7).
MODES = [
    ("off", "Arrêt"),
    ("cool", "Froid"),
    ("heat", "Chaud"),
    ("dry", "Déshumidification"),
    ("fan", "Ventilation"),
    ("auto", "Auto"),
]

MODE_LABELS = dict(MODES)

MODE_VALUES = {
    "off": "0",
    "auto": "1",
    "cool": "3",
    "heat": "4",
    "dry": "8",
    "fan": "7",
}

VALUE_MODES = {value: name for name, value in MODE_VALUES.items()}
VALUE_LABELS = {value: MODE_LABELS.get(name, name) for name, value in MODE_VALUES.items()}

WEEKDAY_NAMES = [
    "Lundi",
    "Mardi",
    "Mercredi",
    "Jeudi",
    "Vendredi",
    "Samedi",
    "Dimanche",
]


def _monday_week_type(monday_date: date, settings: dict, overrides: dict) -> str:
    """Type de semaine ('A'/'B') pour le lundi ISO donne."""
    monday = monday_date.isoformat()
    if monday in overrides:
        value = overrides[monday]
        if value in ("A", "B"):
            return value

    mode = settings.get("week_mode", "parity")

    if mode == "none":
        return "A"

    if mode == "explicit":
        return (
            "A"
            if settings.get("default_week", "A") not in ("A", "B")
            else settings.get("default_week", "A")
        )

    iso_week = monday_date.isocalendar().week
    value = "A" if iso_week % 2 == 1 else "B"
    if str(settings.get("week_flip", "0")).lower() in ("1", "true", "on"):
        value = "B" if value == "A" else "A"
    return value


def week_type(moment: datetime, settings: dict, overrides: dict) -> str:
    """Renvoie 'A' ou 'B' pour la semaine qui contient `moment`.

    Trois modes :
      - parity   : semaine ISO impaire = A, paire = B (avec option d'inversion)
      - explicit : tout en `default_week`, sauf les semaines listees
      - none     : toujours A

    En mode parity, le reglage ``week_switch_day`` permet de basculer le
    vendredi (a ``friday_switch_minutes``) au lieu du lundi : le week-end
    utilise alors la semaine suivante.
    """
    mode = settings.get("week_mode", "parity")

    monday_date = moment.date() - timedelta(days=moment.weekday())

    if mode == "parity" and str(
        settings.get("week_switch_day", "monday")
    ).lower() == "friday":
        switched = False
        if moment.weekday() in (5, 6):
            # Samedi / dimanche : la bascule du vendredi a eu lieu.
            switched = True
        elif moment.weekday() == 4:
            raw_switch = settings.get("friday_switch_minutes")
            try:
                switch_min = (
                    460
                    if raw_switch is None or str(raw_switch).strip() == ""
                    else int(float(raw_switch))
                )
            except (TypeError, ValueError):
                switch_min = 460
            switch_min = max(0, min(1439, switch_min))
            now_min = moment.hour * 60 + moment.minute
            switched = now_min >= switch_min
        if switched:
            return _monday_week_type(
                monday_date + timedelta(days=7), settings, overrides
            )

    return _monday_week_type(monday_date, settings, overrides)


def _index(weekday: int, minutes: int) -> int:
    return int(weekday) * 1440 + int(minutes)


def find_slot(slots: list[dict], moment: datetime) -> dict | None:
    """Creneau actif a `moment` (le dernier commence), avec bouclage semaine."""
    if not slots:
        return None

    now_index = _index(moment.weekday(), moment.hour * 60 + moment.minute)

    best = None
    best_index = -1
    for slot in slots:
        slot_index = _index(slot["weekday"], slot["minutes"])
        if slot_index <= now_index and slot_index > best_index:
            best, best_index = slot, slot_index

    if best is not None:
        return best

    # Avant le premier creneau de la semaine : on reprend le dernier.
    return max(slots, key=lambda s: _index(s["weekday"], s["minutes"]))


def next_slot(slots: list[dict], moment: datetime) -> dict | None:
    """Prochain changement d'etat apres `moment`, ou None."""
    if not slots:
        return None

    now_index = _index(moment.weekday(), moment.hour * 60 + moment.minute)
    week_length = 7 * 1440

    candidates = []
    for slot in slots:
        slot_index = _index(slot["weekday"], slot["minutes"])
        delta = (slot_index - now_index) % week_length
        if delta == 0:
            delta = week_length
        candidates.append((delta, slot))

    delta, slot = min(candidates, key=lambda item: item[0])
    return {
        **slot,
        "in_minutes": delta,
        "at": (moment + timedelta(minutes=delta)).replace(second=0, microsecond=0),
    }


def describe(slot: dict | None) -> str:
    if not slot:
        return "Aucun créneau"
    mode = slot.get("mode", "off")
    label = MODE_LABELS.get(mode, mode)
    if mode == "off":
        return "Arrêt"
    if mode == "fan":
        return "Ventilation"
    try:
        temp = float(slot["temp"])
        return f"{label} {temp:g} °C"
    except (KeyError, TypeError, ValueError):
        return label


def iso_monday(moment: date) -> str:
    return (moment - timedelta(days=moment.weekday())).isoformat()
