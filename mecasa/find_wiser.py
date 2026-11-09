"""Recherche automatique du Hub Schneider Wiser sur le réseau local."""

import concurrent.futures
import json
import re
import socket
import subprocess
import urllib.error
import urllib.request


def get_arp_table() -> list[dict]:
    entries = []
    try:
        output = subprocess.check_output(["arp", "-a"], text=True, stderr=subprocess.DEVNULL)
        for line in output.splitlines():
            # Ex: 192.168.8.50    00-50-c2-xx-xx-xx    dynamique
            match = re.search(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]+)", line)
            if match:
                entries.append({"ip": match.group(1), "mac": match.group(2).lower()})
    except Exception:
        pass
    return entries


def probe_ip(ip: str) -> dict | None:
    # 1. Test rapide ouverture port 80
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.35)
    try:
        res = sock.connect_ex((ip, 80))
        sock.close()
        if res != 0:
            return None
    except Exception:
        return None

    # 2. Test HTTP sur le port 80
    info = {"ip": ip, "is_wiser": False, "details": ""}
    url = f"http://{ip}/data/domain/"
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "CozytouchWiserFinder/1.0")

    try:
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            # Si le hub répond 200 (certains hubs sans auth ou mode setup)
            data = resp.read(500).decode("utf-8", "ignore")
            if "Room" in data or "System" in data or "Wiser" in data:
                info["is_wiser"] = True
                info["details"] = "Réponse domaine Wiser (200 OK)"
                return info
    except urllib.error.HTTPError as err:
        # Un Wiser Hub renvoie 401 ou 403 quand la clé 'SECRET' manque !
        server = err.headers.get("Server", "")
        body = err.read(500).decode("utf-8", "ignore")
        if err.code in (401, 403, 405):
            if "wiser" in server.lower() or "wiser" in body.lower() or "secret" in body.lower():
                info["is_wiser"] = True
                info["details"] = f"Wiser authentifié (HTTP {err.code})"
                return info
            # Même sans mention explicite, un 401/403 sur /data/domain/ est un candidat très fort
            info["details"] = f"HTTP {err.code} sur /data/domain/ (candidat probable)"
            info["is_wiser"] = True
            return info
    except Exception:
        pass

    # 3. Test endpoint /secret (mode appairage Wiser)
    try:
        with urllib.request.urlopen(f"http://{ip}/secret", timeout=0.8) as resp:
            content = resp.read(100).decode("utf-8", "ignore").strip()
            if content and len(content) > 10:
                info["is_wiser"] = True
                info["secret"] = content
                info["details"] = "Hub Wiser en mode appairage Setup (/secret accessible)"
                return info
    except Exception:
        pass

    return None


def scan_network() -> list[dict]:
    """Scanne le /24 local et renvoie les Hubs Wiser detectes (sans affichage)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except OSError:
        local_ip = "192.168.1.1"
    finally:
        s.close()

    prefix = ".".join(local_ip.split(".")[:3])
    ips_to_test = [f"{prefix}.{i}" for i in range(1, 255) if f"{prefix}.{i}" != local_ip]

    candidates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=60) as executor:
        futures = {executor.submit(probe_ip, ip): ip for ip in ips_to_test}
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
            except Exception:
                continue
            if res and res.get("is_wiser"):
                candidates.append(res)
    return candidates


def main():
    print("[*] Analyse du réseau local...")
    candidates = scan_network()
    for res in candidates:
        print(f"[+] Hub Wiser détecté : {res['ip']} ({res['details']})")
        if "secret" in res:
            print(f"    -> Clé secrète : {res['secret']}")
    if not candidates:
        print("[-] Aucun Hub Wiser détecté automatiquement sur le port HTTP.")
    return candidates


if __name__ == "__main__":
    main()
