#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ "${1:-}" = "" ]; then
  echo "Usage : $0 <archive.zip>" >&2
  exit 1
fi

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Erreur : python3/python introuvable." >&2
  exit 1
fi

exec "$PYTHON_BIN" config_backup.py import "$1"
