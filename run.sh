#!/usr/bin/env sh
# Lance eCasa depuis le dossier du projet (Raspberry Pi, Linux, macOS).
set -eu
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    exec ".venv/bin/python" -m ecasa.main
fi

exec python3 -m ecasa.main
