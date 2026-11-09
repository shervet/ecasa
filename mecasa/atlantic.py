"""Client HTTP minimal pour l'API cloud Cozytouch (plateforme Magellan).

Aucune dependance externe : uniquement la bibliotheque standard.

Endpoints (documentes par le projet open source gduteil/cozytouch) :

    POST /users/token                              -> access_token (OAuth2 password)
    GET  /magellan/cozytouch/setupviewv2           -> tout le compte (appareils + valeurs)
    POST /magellan/executions/writecapability      -> ecrit une valeur
    GET  /magellan/executions/{id}                 -> etat de l'execution (3 = terminee)

Rappel : il n'existe AUCUNE API locale. Le module Cozytouch est une passerelle
cloud, tout passe par les serveurs Atlantic.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://apis.groupe-atlantic.com"

# Identifiant public de l'application Cozytouch (le meme que pyoverkiz).
CLIENT_ID = (
    "Q3RfMUpWeVRtSUxYOEllZkE3YVVOQmpGblpVYToyRWNORHpfZHkzNDJVSnFvMlo3cFNKTnZVdjBh"
)
USER_PREFIX = "GA-PRIVATEPERSON/"

# Etats d'une execution cote Atlantic.
EXEC_PENDING = 1
EXEC_RUNNING = 2
EXEC_DONE = 3


class AtlanticError(RuntimeError):
    """Erreur generique de l'API."""


class AuthError(AtlanticError):
    """Identifiants refuses par Atlantic."""


class RateLimited(AtlanticError):
    """Trop de requetes (HTTP 429)."""

    def __init__(self, message: str, retry_after: float = 60.0):
        super().__init__(message)
        self.retry_after = retry_after


def _http(method, path, *, data=None, headers=None, timeout=20):
    """Requete brute. Ne leve pas sur les codes HTTP : renvoie (status, body)."""
    request = urllib.request.Request(BASE_URL + path, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except urllib.error.URLError as err:
        raise AtlanticError(f"Reseau injoignable : {err.reason}") from err


class AtlanticClient:
    """Session authentifiee sur le compte Cozytouch."""

    def __init__(self, email: str, password: str, timeout: int = 20):
        self.email = email
        self.password = password
        self.timeout = timeout
        self._token: str | None = None
        self._expires_at: float = 0.0

    # ------------------------------------------------------------------ auth

    def login(self) -> None:
        """Obtient un token OAuth2 (grant_type=password)."""
        body = urllib.parse.urlencode(
            {
                "grant_type": "password",
                "scope": "openid",
                "username": USER_PREFIX + self.email,
                "password": self.password,
            }
        ).encode()

        status, raw = _http(
            "POST",
            "/users/token",
            data=body,
            headers={
                "Authorization": f"Basic {CLIENT_ID}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=self.timeout,
        )

        if status in (400, 401, 403):
            raise AuthError(
                "Identifiants Cozytouch refuses (verifie l'e-mail et le mot de passe)."
            )
        if status != 200:
            raise AtlanticError(f"Connexion impossible : HTTP {status}")

        try:
            payload = json.loads(raw)
            self._token = payload["access_token"]
        except (ValueError, KeyError) as err:
            raise AtlanticError("Reponse de connexion illisible") from err

        # Marge de securite : on renouvelle une minute avant l'expiration.
        expires_in = float(payload.get("expires_in", 3600))
        self._expires_at = time.time() + max(60.0, expires_in - 60.0)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, payload=None):
        """Requete authentifiee, avec un renouvellement de token sur 401."""
        if not self._token or time.time() >= self._expires_at:
            self.login()

        for attempt in (1, 2, 3):
            data = None
            if payload is not None:
                data = json.dumps(payload).encode()

            status, raw = _http(
                method, path, data=data, headers=self._headers(), timeout=self.timeout
            )

            if status == 401 and attempt == 1:
                # Token refuse : on refait un login et on rejoue une fois.
                self._token = None
                self.login()
                continue

            # Atlantic renvoie parfois 403 « ResourceAccessForbidden » avec
            # « access_token » quand une autre session (application mobile,
            # ancien processus) a renouvele le token entre-temps. On reconnecte
            # une fois avant de declarer l'ecriture impossible.
            if (
                status == 403
                and b"access_token" in raw
                and attempt == 1
            ):
                self._token = None
                self.login()
                continue

            if status == 429:
                raise RateLimited("Atlantic limite le nombre de requetes", 60.0)

            # Le service Atlantic renvoie de temps en temps un 5xx isole.
            # Une lecture peut etre rejouee sans risque : on evite ainsi
            # d'afficher une erreur cloud jusqu'au prochain cycle (3 min).
            if (
                method == "GET"
                and status in (500, 502, 503, 504)
                and attempt < 3
            ):
                time.sleep(1.0)
                continue

            return status, raw

        raise AtlanticError("Authentification refusee apres renouvellement")

    # ------------------------------------------------------------------ lecture

    def get_setup(self) -> dict:
        """Lit tout le compte : appareils, valeurs et zones.

        L'API renvoie une liste d'un element ; on renvoie cet element.
        """
        status, raw = self._request("GET", "/magellan/cozytouch/setupviewv2")

        if status != 200:
            raise AtlanticError(f"Lecture du compte impossible : HTTP {status}")

        try:
            data = json.loads(raw)
        except ValueError as err:
            raise AtlanticError("Reponse setupviewv2 illisible") from err

        if not isinstance(data, list) or not data:
            raise AtlanticError("Reponse setupviewv2 vide ou inattendue")

        return data[0]

    # ------------------------------------------------------------------ ecriture

    def write_capability(
        self, device_id: int, capability_id: int, value: str, wait: bool = True
    ) -> bool:
        """Ecrit une valeur et attend la fin de l'execution.

        Leve AtlanticError si Atlantic refuse ou si l'execution echoue.
        """
        status, raw = self._request(
            "POST",
            "/magellan/executions/writecapability",
            {
                "capabilityId": int(capability_id),
                "deviceId": int(device_id),
                "value": str(value),
            },
        )

        if status != 201:
            detail = raw.decode("utf-8", "replace")[:300]
            raise AtlanticError(
                f"Ecriture refusee (HTTP {status}) : {detail or 'sans detail'}"
            )

        try:
            execution_id = json.loads(raw)
        except ValueError:
            return True  # ecriture acceptee, pas d'identifiant a suivre

        if wait:
            self._await_execution(execution_id)

        return True

    def _await_execution(self, execution_id, attempts: int = 8,
                         delay: float = 1.0) -> None:
        for _ in range(attempts):
            status, raw = self._request(
                "GET", f"/magellan/executions/{execution_id}"
            )
            if status != 200:
                raise AtlanticError(f"Suivi d'execution impossible : HTTP {status}")

            try:
                state = json.loads(raw).get("state")
            except (ValueError, AttributeError):
                raise AtlanticError("Suivi d'execution illisible")

            if state == EXEC_DONE:
                return
            if state not in (EXEC_PENDING, EXEC_RUNNING):
                raise AtlanticError(f"Execution terminee en erreur (state={state})")

            time.sleep(delay)

        raise AtlanticError("Execution non terminee apres plusieurs essais")

    def close(self) -> None:
        self._token = None


class MockAtlanticClient:
    """Compte simule : permet de tester l'interface sans identifiants.

    Les identifiants de capacites sont ceux d'une clim Atlantic (passerelle
    Navizone) : 7 = mode, 17 = consigne, 40 = temperature ambiante,
    100801 = soufflerie, 100803 = orientation des volets, 100804 = balayage.

    Quand des appareils existent deja (par exemple apres une migration), le
    compte simule les reprend tels quels : la simulation pilote alors la vraie
    installation au lieu d'ajouter des appareils fictifs.
    """

    def __init__(self, devices: list[dict] | None = None):
        self.devices = []
        self._ambient_caps: dict[int, int] = {}
        self._setpoint_caps: dict[int, int] = {}

        known = [
            device
            for device in (devices or [])
            if device.get("device_type", "atlantic") == "atlantic"
        ]
        if known:
            for device in known:
                device_id = int(device["device_id"])
                mode_cap = int(device.get("cap_mode") or 7)
                setpoint_cap = int(device.get("cap_setpoint") or 17)
                ambient_cap = int(device.get("cap_ambient") or 40)
                fan_cap = int(device.get("cap_fan") or 0) or 100801
                louver_cap = int(device.get("cap_louver") or 0) or 100803
                swing_cap = int(device.get("cap_swing") or 0) or 100804
                name = device.get("name") or f"Appareil {device_id}"
                self.devices.append(
                    {
                        "deviceId": device_id,
                        "customName": name,
                        "isAvailable": bool(device.get("enabled", 1)),
                        "capabilities": {
                            mode_cap: "1",
                            setpoint_cap: "20",
                            ambient_cap: "21.0",
                            fan_cap: "5",
                            louver_cap: "4",
                            swing_cap: "0",
                            154: name,
                        },
                    }
                )
                self._ambient_caps[device_id] = ambient_cap
                self._setpoint_caps[device_id] = setpoint_cap
        else:
            for device_id, name, mode, setpoint, ambient in (
                (1001, "Salon", "3", "23", "24.6"),
                (1002, "Bureau", "0", "21", "22.1"),
                (1003, "Chambre", "0", "19.5", "21.8"),
            ):
                self.devices.append(
                    {
                        "deviceId": device_id,
                        "customName": name,
                        "isAvailable": True,
                        "capabilities": {
                            7: mode,
                            17: setpoint,
                            40: ambient,
                            154: name,
                            100801: "5",
                            100803: "4",
                            100804: "0",
                        },
                    }
                )
                self._ambient_caps[device_id] = 40
                self._setpoint_caps[device_id] = 17

    def get_setup(self) -> dict:
        return {
            "setup": {"id": 1, "name": "Compte simule", "rateLimit": 30},
            "devices": self.devices,
            "zones": [],
        }

    def write_capability(self, device_id: int, capability_id: int, value: str,
                         wait: bool = True) -> bool:
        for device in self.devices:
            if device["deviceId"] == int(device_id):
                device["capabilities"][int(capability_id)] = str(value)
                # Petit effet "la clim repond" : la temperature ambiante derive
                # vers la consigne quand l'appareil tourne.
                if int(capability_id) == self._setpoint_caps.get(int(device_id)):
                    ambient_cap = self._ambient_caps.get(int(device_id))
                    if ambient_cap:
                        device["capabilities"][ambient_cap] = f"{float(value):.1f}"
                return True
        return False

    def close(self) -> None:
        pass
