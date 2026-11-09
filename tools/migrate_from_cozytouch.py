#!/usr/bin/env python3
"""Migre une base SQLite cozytouch-scheduler vers le stockage MeCasa.

Usage :
    python tools/migrate_from_cozytouch.py --db ../cozytouch-scheduler/data/cozytouch.db
    python tools/migrate_from_cozytouch.py --db ... --state data/state.json --force

La migration reprend les réglages, pièces, appareils, plannings et semaines A/B.
Les mesures historiques et le journal ne sont pas copiés : MeCasa repart sur son
propre historique horaire, moins gourmand en écritures.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mecasa.storage import MemoryStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migration cozytouch-scheduler → MeCasa")
    parser.add_argument("--db", required=True, help="Chemin de l'ancienne base cozytouch.db")
    parser.add_argument(
        "--state",
        default=str(ROOT / "data" / "state.json"),
        help="Snapshot MeCasa à écrire (défaut : data/state.json)",
    )
    parser.add_argument(
        "--history",
        default=str(ROOT / "data" / "history"),
        help="Dossier d'historique MeCasa (défaut : data/history)",
    )
    parser.add_argument("--force", action="store_true", help="Écrase un snapshot existant")
    return parser.parse_args()


def rows(db: sqlite3.Connection, table: str) -> list[dict]:
    db.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in db.execute(f"select * from {table}")]
    except sqlite3.Error:
        return []


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    state_path = Path(args.state).expanduser().resolve()
    history_dir = Path(args.history).expanduser().resolve()

    if not db_path.is_file():
        print(f"✗ Base introuvable : {db_path}", file=sys.stderr)
        return 1
    if state_path.exists() and not args.force:
        print(
            f"✗ {state_path} existe déjà. Relance avec --force pour l'écraser.",
            file=sys.stderr,
        )
        return 1

    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        settings = {
            str(row["key"]): str(row["value"])
            for row in rows(db, "settings")
            if row.get("key")
        }
        rooms = rows(db, "rooms")
        devices = rows(db, "devices")
        slots = rows(db, "slots")
        week_overrides = rows(db, "week_overrides")
    finally:
        db.close()

    if not rooms and not devices:
        print("✗ La base ne contient ni pièce ni appareil. Rien à migrer.", file=sys.stderr)
        return 1

    store = MemoryStore(
        str(state_path),
        str(history_dir),
        history_days=180,
        history_interval=900,
        structural_delay=1,
    )

    store.import_data(
        {
            "settings": settings,
            "rooms": rooms,
            "devices": devices,
            "week_overrides": [
                {"monday": row["monday"], "week": row["week"]}
                for row in week_overrides
                if row.get("week") in ("A", "B")
            ],
        }
    )

    # Pièces dont l'appareil est l'actionneur : on migre leurs créneaux en
    # mode "pièce" plutôt qu'"appareil", pour que la sauvegarde fonctionne.
    actuator_to_room: dict[int, int] = {}
    for room in rooms:
        for key in ("actuator_device_id", "thermostat_device_id"):
            value = room.get(key)
            if value not in (None, "", 0, "0"):
                try:
                    actuator_to_room[int(value)] = int(room["id"])
                except (TypeError, ValueError):
                    pass

    grouped: dict[tuple[str, int, str], list[list[dict]]] = {}
    for slot in slots:
        week = str(slot.get("week") or "").upper()
        if week not in ("A", "B"):
            continue
        try:
            weekday = int(slot["weekday"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= weekday <= 6:
            continue
        minutes = int(slot.get("minutes") or 0)
        entry = {
            "time": f"{minutes // 60:02d}:{minutes % 60:02d}",
            "mode": str(slot.get("mode") or "off"),
            "temp": float(slot.get("temp") or 20),
            "fan": slot.get("fan"),
        }
        room_id = slot.get("room_id")
        device_id = slot.get("device_id")
        if room_id not in (None, "", 0, "0"):
            key = ("room", int(room_id), week)
        elif device_id not in (None, "", 0, "0") and int(device_id) in actuator_to_room:
            key = ("room", actuator_to_room[int(device_id)], week)
        elif device_id not in (None, "", 0, "0"):
            key = ("device", int(device_id), week)
        else:
            continue
        grouped.setdefault(key, [[] for _ in range(7)])[weekday].append(entry)

    imported_slots = 0
    for (kind, target_id, week), days in grouped.items():
        for day in days:
            day.sort(key=lambda item: item["time"])
        if kind == "room":
            store.set_slots(room_id=target_id, week=week, days=days)
        else:
            store.set_slots(target_id, week, days)
        imported_slots += sum(len(day) for day in days)

    store.flush(force=True)
    store.close()

    print("✓ Migration terminée")
    print(f"  Réglages   : {len(settings)}")
    print(f"  Pièces     : {len(rooms)}")
    print(f"  Appareils  : {len(devices)}")
    print(f"  Créneaux   : {imported_slots}")
    print(f"  Semaines   : {len(week_overrides)} surcharge(s)")
    print(f"  Snapshot   : {state_path}")
    print("  Historique : repart de zéro (les anciennes mesures ne sont pas copiées).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
