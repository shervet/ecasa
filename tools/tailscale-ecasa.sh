#!/usr/bin/env bash
# eCasa — actions Tailscale à lancer en root (sudo).
#
# Usage :
#   sudo ./tailscale-ecasa.sh operator [utilisateur]  (défaut : $SUDO_USER)
#   sudo ./tailscale-ecasa.sh install
#   sudo ./tailscale-ecasa.sh uninstall
#   sudo ./tailscale-ecasa.sh up [nom]      (nom par défaut : ecasa)
#   sudo ./tailscale-ecasa.sh serve
#   sudo ./tailscale-ecasa.sh reset
#   ./tailscale-ecasa.sh status
#
# « operator » autorise le compte du service à piloter tailscaled sans root :
# c'est ce qui permet à l'interface eCasa de faire « up » et « serve ».
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
  operator)
    need_root "$@"
    TARGET="${2:-${SUDO_USER:-}}"
    if [ -z "$TARGET" ]; then
      echo "Usage: sudo $0 operator <utilisateur>" >&2
      exit 2
    fi
    tailscale set --operator="$TARGET"
    echo "Compte $TARGET autorisé à piloter Tailscale (up / serve sans sudo)."
    ;;
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
    echo "Usage: sudo $0 {operator [user]|install|uninstall|up [nom]|serve|reset|status}" >&2
    exit 2
    ;;
esac
