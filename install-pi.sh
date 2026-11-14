#!/usr/bin/env bash
# Installe eCasa comme service systemd sur un Raspberry Pi (Debian/Raspbian).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-${USER}}"
SERVICE_NAME="ecasa"

if [ "$(id -u)" -ne 0 ]; then
    echo "Ce script a besoin de sudo : sudo ./install-pi.sh" >&2
    exit 1
fi

echo "→ Installation dans $APP_DIR pour l'utilisateur $RUN_USER"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 est introuvable. Installe-le d'abord." >&2
    exit 1
fi

apt-get update
apt-get install -y python3-venv python3-pip

# Tailscale (facultatif) : installe le client si absent, sans bloquer l'installation.
# On le fait ici (root) car le service eCasa tourne sans privilèges : le bouton
# « Installer » de l'interface ne peut pas passer les droits sur un Pi.
if ! command -v tailscale >/dev/null 2>&1; then
    if curl -fsSL https://tailscale.com/install.sh | sh; then
        echo "→ Tailscale installé."
    else
        echo "⚠ Tailscale non installé (réseau indisponible ?). Réessaie plus tard avec :"
        echo "  curl -fsSL https://tailscale.com/install.sh | sh"
    fi
fi

if [ ! -d "$APP_DIR/.venv" ]; then
    sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/.venv"
fi

sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip

# pronotepy reste facultatif : l'absence de réseau ne doit pas bloquer l'install.
if [ -f "$APP_DIR/requirements.txt" ]; then
    sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" \
        || echo "⚠ Dépendances facultatives non installées (Pronote indisponible)."
fi

if [ ! -f "$APP_DIR/config.json" ]; then
    sudo -u "$RUN_USER" cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
    echo "→ config.json créé depuis l'exemple (mode simulation)."
fi

sed -e "s|__USER__|$RUN_USER|g" -e "s|__DIR__|$APP_DIR|g" \
    "$APP_DIR/systemd/ecasa.service" > "/etc/systemd/system/$SERVICE_NAME.service"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo ""
echo "✓ eCasa est démarré."
echo "  Journal    : sudo journalctl -u $SERVICE_NAME -f"
echo "  Réglages   : http://$(hostname -I | awk '{print $1}'):8780"
echo "  Config     : $APP_DIR/config.json"
echo "  Arrêt      : sudo systemctl stop $SERVICE_NAME"
