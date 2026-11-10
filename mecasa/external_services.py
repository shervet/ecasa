"""Relance des services externes au redémarrage de MeCasa.

MeCasa ne sait pas ce qui tourne à côté de lui. Au redémarrage, on tente donc
de relancer, sans jamais bloquer ni faire échouer MeCasa :

- les processus gérés par pm2 (``pm2 restart all``) ;
- les unités systemd dont la commande est un binaire Node ;
- un conteneur Docker MyElectricalData auto-hébergé, si l'adresse configurée
  pointe vers la machine locale.

Chaque tentative renvoie un résultat lisible, journalisé dans l'interface.
Les erreurs (droits, binaire absent, service déjà arrêté) sont tolérées.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

# Un binaire Node dans une ligne ExecStart : « node », « nodejs », « node-red ».
_NODE_BINARY_NAMES = {"node", "nodejs", "node-red", "nodered"}
_MED_CONTAINER_KEYS = ("electricaldata", "my-electrical-data")


def _run(args: list[str], timeout: int = 90) -> tuple[int, str]:
    """Exécute une commande sans lever d'exception."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return 1, str(err)
    detail = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode, detail


def _which(name: str) -> str | None:
    return shutil.which(name)


def _pm2_binary() -> str | None:
    found = _which("pm2")
    if found:
        return found
    candidates = [
        Path.home() / ".npm-global" / "bin" / "pm2",
        Path("/usr/local/bin/pm2"),
        Path("/usr/bin/pm2"),
    ]
    if os.name == "nt":
        candidates.append(Path(os.environ.get("APPDATA", "")) / "npm" / "pm2.cmd")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def restart_pm2() -> dict:
    binary = _pm2_binary()
    if not binary:
        return {"name": "pm2", "attempted": False, "ok": True, "detail": "pm2 absent"}
    code, detail = _run([binary, "restart", "all", "--update-env"])
    return {
        "name": "pm2",
        "attempted": True,
        "ok": code == 0,
        "detail": detail or ("redémarré" if code == 0 else f"code {code}"),
    }


def decide_node_exec(command: str) -> bool:
    """Vrai si une ligne ``ExecStart=`` lance un binaire Node."""
    command = command.strip().lstrip("-@+!:")
    for token in re.split(r"\s+", command):
        token = token.strip("\"'")
        if not token or token.startswith(("-", "$", "%")):
            continue
        base = Path(token).name.lower()
        if base.endswith(".exe"):
            base = base[:-4]
        if base in _NODE_BINARY_NAMES:
            return True
    return False


def _systemd_unit_dirs() -> list[Path]:
    home = Path.home()
    return [
        Path("/etc/systemd/system"),
        Path("/run/systemd/system"),
        Path("/lib/systemd/system"),
        Path("/usr/lib/systemd/system"),
        home / ".config" / "systemd" / "user",
    ]


def find_node_units(dirs: list[Path] | None = None) -> list[dict]:
    """Liste les unités systemd Node actives trouvées dans les dossiers usuels."""
    units: dict[str, dict] = {}
    user_dir = Path.home() / ".config" / "systemd" / "user"
    for base in dirs or _systemd_unit_dirs():
        try:
            files = list(base.glob("*.service"))
        except OSError:
            continue
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            is_node = any(
                line.strip().startswith("ExecStart=")
                and decide_node_exec(line.split("=", 1)[1])
                for line in text.splitlines()
            )
            if not is_node:
                continue
            name = path.name
            if name == "mecasa.service" or name in units:
                continue
            units[name] = {"unit": name, "user": base == user_dir}
    return list(units.values())


def restart_node_systemd_units() -> list[dict]:
    if os.name == "nt" or not _which("systemctl"):
        return []
    results = []
    for item in find_node_units()[:30]:
        unit = item["unit"]
        base = ["systemctl", "--user"] if item["user"] else ["systemctl"]
        active, _ = _run([*base, "is-active", "--quiet", unit], timeout=10)
        if active != 0:
            continue
        code, detail = _run([*base, "restart", "--no-ask-password", unit])
        results.append({
            "name": f"systemd {unit}",
            "attempted": True,
            "ok": code == 0,
            "detail": detail or ("redémarré" if code == 0 else f"code {code}"),
        })
    return results


def med_is_local(config: dict) -> bool:
    raw = str(config.get("med_url") or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").strip()
    except ValueError:
        return False
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Nom d'hôte sans point : probablement une machine du réseau local.
        return "." not in host
    return address.is_loopback or address.is_private


def matching_med_containers(names: list[str]) -> list[str]:
    matches = []
    for name in names:
        low = name.lower()
        if any(key in low for key in _MED_CONTAINER_KEYS):
            matches.append(name)
        elif low == "med" or low.startswith("med-"):
            matches.append(name)
    return matches


def restart_med_container(config: dict) -> dict | None:
    if not config.get("med_enabled") or not med_is_local(config):
        return None
    if not _which("docker"):
        return None
    code, output = _run(
        ["docker", "ps", "--format", "{{.Names}}"],
        timeout=20,
    )
    if code != 0:
        return {
            "name": "MyElectricalData",
            "attempted": True,
            "ok": False,
            "detail": output or "docker ps a échoué",
        }
    containers = matching_med_containers(
        [line.strip() for line in output.splitlines() if line.strip()]
    )
    if not containers:
        return {
            "name": "MyElectricalData",
            "attempted": True,
            "ok": False,
            "detail": "aucun conteneur local trouvé",
        }
    errors = []
    for container in containers:
        code, detail = _run(["docker", "restart", container], timeout=90)
        if code != 0:
            errors.append(f"{container} : {detail or code}")
    return {
        "name": "MyElectricalData",
        "attempted": True,
        "ok": not errors,
        "detail": "; ".join(errors) or f"conteneur(s) redémarré(s) : {', '.join(containers)}",
    }


def restart_external_services(config: dict | None = None) -> list[dict]:
    """Relance les services externes connus. Ne lève jamais d'exception."""
    config = config or {}
    results = [restart_pm2()]
    try:
        results.extend(restart_node_systemd_units())
    except Exception as err:  # noqa: BLE001 - un service externe ne doit pas bloquer
        results.append({
            "name": "systemd (Node)",
            "attempted": True,
            "ok": False,
            "detail": str(err),
        })
    try:
        med = restart_med_container(config)
    except Exception as err:  # noqa: BLE001
        med = {
            "name": "MyElectricalData",
            "attempted": True,
            "ok": False,
            "detail": str(err),
        }
    if med:
        results.append(med)
    return results
