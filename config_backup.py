#!/usr/bin/env python3
import argparse
import io
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


def safe_name(raw: str) -> str:
    """Nom d'archive autorisé, ou chaîne vide (traversée de dossier refusée)."""
    text = str(raw or "").replace("\\", "/")
    if not text or text.startswith("/"):
        return ""
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return ""
    if len(parts[0]) == 2 and parts[0][1] == ":":
        return ""
    if text in ALLOWED or text.startswith(ALLOWED_PREFIX):
        return text
    return ""


def _zip_items(zf: zipfile.ZipFile, root: str) -> list[str]:
    present = []
    for src, arc in ITEMS:
        path = os.path.join(root, src)
        if os.path.isfile(path):
            zf.write(path, arc)
            present.append(arc)
        elif os.path.isdir(path):
            for dirpath, _, files in os.walk(path):
                for name in files:
                    full = os.path.join(dirpath, name)
                    rel = os.path.relpath(full, root).replace(os.sep, "/")
                    if not safe_name(rel):
                        continue
                    zf.write(full, rel)
                    present.append(rel)
    return present


def export_bytes(root: str = ".") -> bytes | None:
    """Contenu du zip d'export, ou None si aucun fichier n'existe."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        present = _zip_items(zf, root)
    if not present:
        return None
    return buffer.getvalue()


def export_zip(out_path: str, root: str = ".") -> int:
    present = []
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        present = _zip_items(zf, root)
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


def import_zip(zip_path: str, backup_dir: str, root: str = ".") -> int:
    with zipfile.ZipFile(zip_path) as zf:
        entries = []
        seen = set()
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = safe_name(info.filename)
            if not name or name in seen:
                continue
            seen.add(name)
            entries.append((info, name))
        if not entries:
            print(
                "Archive invalide : aucun fichier attendu (config.json, data/state.json, data/energy.json, data/history/*).",
                file=sys.stderr,
            )
            return 2

        root_path = os.path.abspath(root)
        backed = []
        for _, name in entries:
            dest = os.path.join(root_path, name)
            if os.path.exists(dest):
                backup = os.path.join(root_path, backup_dir, name)
                os.makedirs(os.path.dirname(backup) or ".", exist_ok=True)
                shutil.copy2(dest, backup)
                backed.append(name)

        for info, name in entries:
            dest = os.path.join(root_path, name)
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

    print(f"Import terminé depuis : {zip_path}")
    for _, n in entries:
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
