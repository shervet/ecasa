#!/usr/bin/env sh
# Arrete MeCasa et les services lies (Raspberry Pi, Linux, macOS).
set -u
cd "$(dirname "$0")"

echo "Arret de MeCasa..."
# Service systemd
if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl stop mecasa 2>/dev/null || true
fi

# Processus python mecasa.main restants
pkill -f "mecasa.main" 2>/dev/null || true
pkill -f "python.*mecasa" 2>/dev/null || true

echo "Arret de pm2..."
if command -v pm2 >/dev/null 2>&1; then
    pm2 stop all 2>/dev/null || true
fi

echo "Arret des unites systemd Node actives..."
if command -v systemctl >/dev/null 2>&1; then
    for unit in $(systemctl list-units --type=service --state=active --no-legend 2>/dev/null | awk '{print $1}'); do
        case "$unit" in
            *node*|*nodejs*)
                systemctl stop "$unit" 2>/dev/null || true
                ;;
        esac
    done
fi

echo "Arret des conteneurs MyElectricalData locaux..."
if command -v docker >/dev/null 2>&1; then
    for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE 'electricaldata|(^|[-_])med([-_]|$)'); do
        docker stop "$c" 2>/dev/null || true
    done
fi

echo "Arret termine."