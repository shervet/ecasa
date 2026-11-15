#!/usr/bin/env bash
# Installe eCasa comme service systemd sur un Raspberry Pi (Debian/Raspbian).
# À lancer depuis le dossier du projet cloné :
#   sudo ./install-pi.sh
# Idempotent : peut être relancé sans risque (mise à jour, redémarrage du service).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="${SUDO_USER:-${USER}}"
SERVICE_NAME="ecasa"

if [ "$(id -u)" -ne 0 ]; then
    echo "Ce script a besoin de sudo : sudo ./install-pi.sh" >&2
    exit 1
fi

echo "→ Installation dans $APP_DIR pour l'utilisateur $RUN_USER"

# ---------------------------------------------------------------- dépendances
if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 est introuvable : sudo apt-get install -y python3" >&2
    exit 1
fi

apt-get update
apt-get install -y python3-venv python3-pip curl

# ------------------------------------------------------------------ tailscale
# Installe le client si absent, sans bloquer l'installation : le service eCasa
# tourne sans privilèges, seul ce script (root) peut l'installer.
if ! command -v tailscale >/dev/null 2>&1; then
    if curl -fsSL https://tailscale.com/install.sh | sh; then
        echo "→ Tailscale installé."
    else
        echo "⚠ Tailscale non installé (réseau indisponible ?). Réessaie plus tard :"
        echo "  curl -fsSL https://tailscale.com/install.sh | sh"
    fi
fi

# Autorise le compte du service à piloter tailscaled sans root : sans cela
# l'interface ne peut exécuter ni « tailscale up » ni « tailscale serve »
# (le service tourne avec NoNewPrivileges, sudo y est impossible).
if command -v tailscale >/dev/null 2>&1; then
    if tailscale set --operator="$RUN_USER" 2>/dev/null; then
        echo "→ Compte $RUN_USER autorisé à piloter Tailscale (operator)."
    else
        echo "⚠ Autorisation Tailscale non appliquée (tailscaled arrêté ?). Lance :"
        echo "  sudo tailscale set --operator=$RUN_USER"
    fi
fi

# ------------------------------------------------------- environnement python
if [ ! -d "$APP_DIR/.venv" ]; then
    sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/.venv"
fi

sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip

# pronotepy reste facultatif : l'absence de réseau ne doit pas bloquer l'install.
if [ -f "$APP_DIR/requirements.txt" ]; then
    sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" \
        || echo "⚠ Dépendances facultatives non installées (Pronote indisponible)."
fi

# ------------------------------------------------------------------ config
if [ ! -f "$APP_DIR/config.json" ]; then
    sudo -u "$RUN_USER" cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
    echo "→ config.json créé depuis l'exemple (mode simulation)."
fi

# ------------------------------------------------------------------ service
sed -e "s|__USER__|$RUN_USER|g" -e "s|__DIR__|$APP_DIR|g" \
    "$APP_DIR/systemd/ecasa.service" > "/etc/systemd/system/$SERVICE_NAME.service"

chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

if systemctl is-active --quiet "$SERVICE_NAME"; then
    systemctl restart "$SERVICE_NAME"
    echo "→ Service eCasa redémarré."
else
    systemctl start "$SERVICE_NAME"
    echo "→ Service eCasa démarré."
fi

IP="$(hostname -I | awk '{print $1}')"
echo ""
echo "✓ eCasa est installé et démarré."
echo "  Interface    : http://$IP:8780"
echo "  Journal      : sudo journalctl -u $SERVICE_NAME -f"
echo "  Config       : $APP_DIR/config.json"
echo "  Arrêt        : sudo systemctl stop $SERVICE_NAME"
echo ""
echo "Tailscale (accès distant) :"
echo "  sudo tailscale set --operator=$RUN_USER   # une seule fois (fait ci-dessus)"
echo "  puis dans l'interface : Réglages → Tailscale → Connecter, puis Publier."
