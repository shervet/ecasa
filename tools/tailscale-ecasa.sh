#!/usr/bin/env bash
# eCasa — actions Tailscale à lancer en root (sudo).
#
# Usage :
#   sudo ./tailscale-ecasa.sh install
#   sudo ./tailscale-ecasa.sh uninstall
#   sudo ./tailscale-ecasa.sh up [nom]      (nom par défaut : ecasa)
#   sudo ./tailscale-ecasa.sh serve
#   sudo ./tailscale-ecasa.sh reset
#   ./tailscale-ecasa.sh status
#
# Le service eCasa tourne volontairement sans privilèges (NoNewPrivileges) :
# ces actions ne peuvent pas être exécutées depuis l'interface web.
set -euo pipefail

ACTION="${1:-status}"
HOSTNAME="${2:-ecasa}"
PORT="${ECASA_PORT:-8780}"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Cette action a besoin de root : sudo $0 $*" >&2
    exit 1
  fi
}

case "$ACTION" in
  install)
    need_root "$@"
    curl -fsSL https://tailscale.com/install.sh | sh
    ;;
  uninstall)
    need_root "$@"
    apt-get purge -y tailscale 2>/dev/null || dnf remove -y tailscale || true
    rm -rf /var/lib/tailscale
    echo "Tailscale désinstallé et état supprimé."
    ;;
  up)
    need_root "$@"
    tailscale up --hostname="$HOSTNAME" --accept-routes=false
    ;;
  serve)
    need_root "$@"
    tailscale serve --bg --https=443 --set-path=/ "http://127.0.0.1:$PORT"
    ;;
  reset)
    need_root "$@"
    tailscale serve reset
    ;;
  status)
    tailscale status || true
    echo "--- serve ---"
    tailscale serve status || true
    ;;
  *)
    echo "Usage: sudo $0 {install|uninstall|up [nom]|serve|reset|status}" >&2
    exit 2
    ;;
esac
