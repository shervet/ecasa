#!/usr/bin/env python3
import argparse
import os
import shutil
import sys
import zipfile
from datetime import datetime

ITEMS = [
    ("config.json", "config.json"),
    ("data/state.json", "data/state.json"),
    ("data/energy.json", "data/energy.json"),
    ("data/history", "data/history"),
]
ALLOWED = {"config.json", "data/state.json", "data/energy.json"}
ALLOWED_PREFIX = "data/history/"


def export_zip(out_path: str) -> int:
    present = []
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arc in ITEMS:
            if os.path.isfile(src):
                zf.write(src, arc)
                present.append(arc)
            elif os.path.isdir(src):
                for root, _, files in os.walk(src):
                    for name in files:
                        full = os.path.join(root, name)
                        rel = os.path.relpath(full, ".").replace(os.sep, "/")
                        zf.write(full, rel)
                        present.append(rel)
    if not present:
        try:
            os.remove(out_path)
        except OSError:
            pass
        print(
            "Aucun fichier à exporter (config.json, data/state.json, data/energy.json, data/history).",
            file=sys.stderr,
        )
        return 2
    print(f"Export créé : {out_path}")
    for p in present:
        print(f"  - {p}")
    return 0


def import_zip(zip_path: str, backup_dir: str) -> int:
    with zipfile.ZipFile(zip_path) as zf:
        names = [n.replace("\\", "/").lstrip("/") for n in zf.namelist() if not n.endswith("/")]
        selected = [n for n in names if n in ALLOWED or n.startswith(ALLOWED_PREFIX)]
        if not selected:
            print(
                "Archive invalide : aucun fichier attendu (config.json, data/state.json, data/energy.json, data/history/*).",
                file=sys.stderr,
            )
            return 2

        backed = []
        for name in selected:
            if os.path.exists(name):
                dest = os.path.join(backup_dir, name)
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                shutil.copy2(name, dest)
                backed.append(name)

        for name in selected:
            os.makedirs(os.path.dirname(name) or ".", exist_ok=True)
            with zf.open(name) as src, open(name, "wb") as out:
                shutil.copyfileobj(src, out)

    print(f"Import terminé depuis : {zip_path}")
    for n in selected:
        print(f"  + {n}")
    if backed:
        print(f"Sauvegarde des fichiers remplacés : {backup_dir}/")
    else:
        print("Aucun fichier existant remplacé (pas de sauvegarde créée).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export/import de la configuration eCasa.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="Exporter config et données dans un zip")
    p_exp.add_argument("output", nargs="?", help="Chemin du zip de sortie")

    p_imp = sub.add_parser("import", help="Importer un zip de config/données")
    p_imp.add_argument("archive", help="Archive zip à importer")

    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if args.cmd == "export":
        out = args.output or f"ecasa-config-{stamp}.zip"
        return export_zip(out)
    if args.cmd == "import":
        bak = f"config-backup-{stamp}"
        return import_zip(args.archive, bak)
    return 1


if __name__ == "__main__":
    sys.exit(main())
