"""Planning de chauffage construit a partir de l'emploi du temps Pronote.

La connexion a Pronote repose sur la bibliotheque facultative `pronotepy`
(https://pronotepy.readthedocs.io). Le reste du planificateur fonctionne sans
elle : seule la generation du planning Pronote est indisponible tant qu'elle
n'est pas installee.

Principe : la nuit est en temperature reduite de l'heure de coucher jusqu'a
l'heure de lever, puis la maison passe en confort. Les journees avec cours,
l'absence (cours + trajets) repasse en reduit ; le matin, elle commence
`temps de trajet` avant le premier cours et, le soir, se termine
`temps de trajet` apres la fin du dernier cours. Les journees sans cours
ne produisent aucun creneau : elles sont preservees telles quelles a
l'import et restent editables a la main.

Pour un eleve externe, le retour a la maison pendant la pause de midi cree un
creneau de confort. Pour un demi-pensionnaire, la journee scolaire est continue :
aucun creneau de confort n'est cree entre deux cours.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

# Deux cours separes par moins de 15 minutes sont fusionnes : inutile de
# recreer un creneau confort pour une recreation.
MERGE_GAP_MINUTES = 15

# En dessous de 15 minutes a la maison entre deux cours, on ne cree pas de
# creneau confort : le trajet aller/retour ne laisse pas le temps de rentrer.
MIN_HOME_MINUTES = 15

# Un demi-pensionnaire reste au etablissement pendant la pause dejeuner, meme
# les jours ou aucun cours n'est prevu l'apres-midi. On considere qu'il rentre
# apres la fin du service de cantine (13 h 30) : le logement ne repasse donc pas
# en confort a midi.
LUNCH_END_MINUTES = 13 * 60 + 30

# Au-dela de cette heure, une journee n'est plus consideree comme « cours le
# matin seulement » : l'import ne reecrit alors que le debut de la journee.
MORNING_END_MINUTES = 13 * 60 + 30

VALID_MODES = ("off", "auto", "cool", "heat", "dry", "fan")

# Statut scolaire : un externe rentre dejeuner chez lui, un demi-pensionnaire
# reste au lycee/college le midi.
STUDENT_STATUS_EXTERNE = "externe"
STUDENT_STATUS_DEMI_PENSIONNAIRE = "demi_pensionnaire"
STUDENT_STATUSES = (STUDENT_STATUS_EXTERNE, STUDENT_STATUS_DEMI_PENSIONNAIRE)

DAY_MINUTES = 24 * 60


class PronoteError(RuntimeError):
    """Erreur lisible a afficher dans l'interface."""


# ------------------------------------------------------- decouverte etablissement

# Annuaire officiel de l'Education nationale (data.education.gouv.fr). Il
# fournit le code UAI de chaque etablissement, qui est justement le
# sous-domaine Pronote : https://<uai>.index-education.net/pronote/...
ANNUAIRE_URL = (
    "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/"
    "fr-en-annuaire-education/records"
)
ANNUAIRE_FIELDS = (
    "identifiant_de_l_etablissement,nom_etablissement,type_etablissement,"
    "code_postal,nom_commune"
)
UAI_PATTERN = re.compile(r"^[0-9]{7}[A-Z]$")

# Les lycees et colleges d'abord : ce sont les etablissements qui utilisent
# Pronote dans l'immense majorite des cas.
TYPE_ORDER = {"Lycée": 0, "Collège": 1}


def normalize_uai(value) -> str:
    """Nettoie un code UAI (7 chiffres + 1 lettre), ou renvoie une chaine vide."""
    text = re.sub(r"[\s.\-]", "", str(value or "")).upper()
    return text if UAI_PATTERN.fullmatch(text) else ""


def url_from_uai(uai, account: str = "student") -> str:
    """Construit l'adresse Pronote d'un etablissement a partir de son UAI."""
    normalized = normalize_uai(uai)
    if not normalized:
        raise PronoteError(
            "Code UAI invalide : 7 chiffres suivis d'une lettre (ex. 0771512A)."
        )
    page = "parent.html" if str(account or "").lower() == "parent" else "eleve.html"
    return f"https://{normalized.lower()}.index-education.net/pronote/{page}"


def _annuaire_escape(value: str) -> str:
    """Echappe une valeur pour la syntaxe de l'API Explore v2."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _annuaire_field_search(field: str, value: str) -> str:
    return f'search({field},"{_annuaire_escape(value)}")'


def _annuaire_where(query: str) -> str:
    uai = normalize_uai(query)
    if uai:
        return f'identifiant_de_l_etablissement="{uai}"'

    fields = ("nom_etablissement", "nom_commune", "code_postal")
    whole = "(" + " or ".join(
        _annuaire_field_search(field, query) for field in fields
    ) + ")"

    # search() exige que tous les mots soient dans le meme champ. Une requete
    # « nom + ville » ou « nom + code postal » ne matche donc rien avec la
    # recherche sur la requete entiere : on ajoute une combinaison par mot,
    # chaque mot etant cherche dans l'un des champs.
    tokens = query.split()
    if len(tokens) <= 1:
        return whole

    token_groups = []
    for token in tokens:
        token_groups.append(
            "(" + " or ".join(
                _annuaire_field_search(field, token) for field in fields
            ) + ")"
        )
    return f"({whole} or (" + " and ".join(token_groups) + "))"


def _school_from_record(record: dict) -> dict | None:
    uai = normalize_uai(record.get("identifiant_de_l_etablissement"))
    if not uai:
        return None
    return {
        "uai": uai,
        "name": str(record.get("nom_etablissement") or "").strip(),
        "type": str(record.get("type_etablissement") or "").strip(),
        "postal_code": str(record.get("code_postal") or "").strip(),
        "city": str(record.get("nom_commune") or "").strip(),
        "url_student": url_from_uai(uai, "student"),
        "url_parent": url_from_uai(uai, "parent"),
    }


def search_schools(query, limit: int = 10) -> list[dict]:
    """Cherche des etablissements par nom, ville, code postal ou UAI.

    Renvoie une liste de dictionnaires avec le code UAI et les deux adresses
    Pronote possibles (eleve et parent).
    """
    text = str(query or "").strip()
    if len(text) < 2:
        raise PronoteError(
            "Saisissez au moins 2 caractères (nom de l'établissement, ville, "
            "code postal ou UAI)."
        )
    if len(text) > 100:
        raise PronoteError("Recherche trop longue (100 caractères maximum).")

    try:
        limit = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        limit = 10

    params = urlencode({
        "where": _annuaire_where(text),
        "limit": limit,
        "select": ANNUAIRE_FIELDS,
    })
    url = f"{ANNUAIRE_URL}?{params}"

    # L'annuaire de l'Education nationale est parfois lent ou renvoie une
    # erreur reseau passagere : une seconde tentative evite de faire echouer
    # la recherche dans l'interface.
    payload = None
    last_error = None
    for attempt in range(2):
        request = Request(
            url,
            headers={
                "User-Agent": "cozytouch-scheduler/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (OSError, ValueError) as err:
            last_error = err
            if attempt == 0:
                time.sleep(0.5)

    if payload is None:
        raise PronoteError(
            "Recherche de l'établissement impossible : "
            f"{last_error or 'réponse invalide de l’annuaire'}"
        )

    schools = [
        school for school in (
            _school_from_record(record)
            for record in (payload.get("results") or [])
            if isinstance(record, dict)
        ) if school
    ]
    schools.sort(key=lambda item: (
        TYPE_ORDER.get(item["type"], 2),
        item["name"].casefold(),
        item["city"].casefold(),
    ))
    return schools[:limit]


# --------------------------------------------------------------------- connexion


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


def _require_pronotepy():
    try:
        import pronotepy  # type: ignore[import-not-found]
    except ImportError as err:
        raise PronoteError(
            "La bibliothèque pronotepy n'est pas installée. Dans le dossier du "
            "projet, lancez : py -m pip install -r requirements.txt"
        ) from err
    return pronotepy


def _monlycee_login(username, password, pronote_url="", **unused):
    """Connexion a l'ENT Monlycee.net (Ile-de-France) via Keycloak.

    Le flux historique de pronotepy ne suit plus la page d'authentification
    Keycloak utilisee par Monlycee.net. On reproduit donc le parcours du
    navigateur : redirection CAS, formulaire Keycloak, puis retour a Pronote.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as err:
        raise PronoteError(
            "La connexion à Monlycée.net nécessite les dépendances de "
            "pronotepy (requests et beautifulsoup4). Réinstallez-le : "
            "py -m pip install -r requirements.txt"
        ) from err

    try:
        with requests.Session() as session:
            session.headers.update(BROWSER_HEADERS)
            first = session.get(pronote_url, allow_redirects=False, timeout=20)
            location = first.headers.get("Location") or ""
            if first.status_code not in (301, 302, 303, 307, 308):
                if first.status_code == 200:
                    return None
                raise PronoteError(
                    f"Le site Pronote a répondu HTTP {first.status_code}."
                )
            if "monlycee" not in location.casefold():
                raise PronoteError(
                    "Le site Pronote redirige vers un service ENT non reconnu. "
                    "Renseignez le service ENT dans les réglages."
                )

            page = session.get(location, timeout=20)
            soup = BeautifulSoup(page.text, "html.parser")
            form = soup.find("form", {"id": "kc-form-login"})
            if form is None:
                raise PronoteError(
                    "Page de connexion Monlycée.net inattendue : formulaire "
                    "Keycloak introuvable."
                )

            action = urljoin(page.url, form.get("action") or page.url)
            payload = {
                str(field.get("name")): field.get("value") or ""
                for field in form.find_all("input")
                if field.get("name")
            }
            payload["username"] = username
            payload["password"] = password
            if form.find("button", {"name": "login"}) is not None:
                payload["login"] = ""

            result = session.post(
                action,
                data=payload,
                headers={"Referer": page.url, "Origin": "https://auth.monlycee.net"},
                timeout=30,
                allow_redirects=True,
            )
            if result.status_code >= 400:
                raise PronoteError(
                    f"Monlycée.net a refusé la connexion (HTTP {result.status_code})."
                )
            if "kc-form-login" in result.text:
                raise PronoteError(
                    "Identifiants Monlycée.net refusés. Vérifiez l'identifiant "
                    "et le mot de passe du compte parent (et non ceux de l'élève)."
                )
            return session.cookies
    except requests.RequestException as err:
        raise PronoteError(f"Connexion à Monlycée.net impossible : {err}") from err


# Certains ENT recents ne sont pas encore exposes par la version installee de
# pronotepy : on les branche directement.
_CUSTOM_ENTS = {
    "monlycee": _monlycee_login,
    "monlycee_net": _monlycee_login,
    "monlycee.net": _monlycee_login,
}


def _ent_callable(pronotepy, name: str):
    name = (name or "").strip()
    if not name:
        return None
    custom = _CUSTOM_ENTS.get(name.casefold())
    if custom is not None:
        return custom
    try:
        import importlib

        ent_module = importlib.import_module("pronotepy.ent")
    except ImportError as err:
        raise PronoteError(
            "La bibliothèque pronotepy est installée mais son module ENT est "
            "indisponible. Réinstallez-la : py -m pip install -r requirements.txt"
        ) from err
    function = getattr(ent_module, name, None)
    if not callable(function):
        raise PronoteError(
            f"Service ENT inconnu : {name}. Laissez le champ vide pour la "
            "détection automatique, ou vérifiez le nom exact dans la "
            "documentation pronotepy (ex. ile_de_france, monbureaunumerique)."
        )
    return function


def _auto_ent(detected: dict):
    """Detecte l'ENT a partir de la redirection de l'adresse Pronote."""

    def login(username, password, pronote_url="", **unused):
        try:
            import requests
        except ImportError as err:
            raise PronoteError(
                "La détection automatique de l'ENT nécessite la bibliothèque "
                "requests. Réinstallez pronotepy : py -m pip install -r "
                "requirements.txt"
            ) from err

        try:
            response = requests.get(
                pronote_url,
                headers=BROWSER_HEADERS,
                allow_redirects=False,
                timeout=20,
            )
        except requests.RequestException as err:
            raise PronoteError(
                f"Connexion au site Pronote impossible : {err}"
            ) from err

        location = response.headers.get("Location") or ""
        host = urlparse(location).hostname or ""
        if response.status_code in (301, 302, 303, 307, 308) and location:
            if "monlycee" in host.casefold() or "monlycee" in location.casefold():
                detected["label"] = "Monlycée.net (ENT Île-de-France)"
                return _monlycee_login(username, password, pronote_url=pronote_url)
            detected["label"] = f"ENT non reconnu ({host or 'redirection'})"
            raise PronoteError(
                "L'adresse Pronote redirige vers un service ENT non reconnu"
                + (f" ({host})" if host else "")
                + ". Renseignez le service ENT dans les réglages."
            )

        if response.status_code >= 400:
            raise PronoteError(
                f"Le site Pronote a répondu HTTP {response.status_code}."
            )
        detected["label"] = "Accès direct Pronote"
        return None

    return login


def connect(config: dict):
    """Ouvre une session Pronote avec la configuration fournie."""
    pronotepy = _require_pronotepy()

    url = str(config.get("pronote_url") or "").strip()
    username = str(config.get("pronote_username") or "").strip()
    password = str(config.get("pronote_password") or "")
    if not url:
        raise PronoteError(
            "Renseignez l'adresse Pronote dans Réglages → Planning Pronote."
        )
    if not username or not password:
        raise PronoteError(
            "Renseignez l'identifiant et le mot de passe Pronote dans "
            "Réglages → Planning Pronote."
        )

    ent_name = str(config.get("pronote_ent") or "").strip()
    detected: dict[str, str] = {}
    # "ile_de_france" pointait autrefois vers l'ancien ENT ; le service actuel
    # est Monlycee.net. On le laisse a la detection automatique pour ne pas
    # casser les configurations enregistrees avec l'ancien nom.
    if ent_name.casefold() in ("", "auto", "automatique", "automatic",
                               "ile_de_france"):
        ent = _auto_ent(detected)
        ent_label = ""
    else:
        ent = _ent_callable(pronotepy, ent_name)
        ent_label = ent_name

    account = str(config.get("pronote_account") or "student").strip().lower()

    try:
        if account == "parent":
            client = pronotepy.ParentClient(
                url, username=username, password=password, ent=ent
            )
        else:
            client = pronotepy.Client(
                url, username=username, password=password, ent=ent
            )
    except PronoteError:
        raise
    except Exception as err:  # noqa: BLE001 - message renvoye tel quel a l'UI
        raise PronoteError(f"Connexion Pronote impossible : {err}") from err

    if not getattr(client, "logged_in", False):
        raise PronoteError(
            "Connexion Pronote refusée : vérifiez l'identifiant, le mot de "
            "passe et, si nécessaire, le service ENT."
        )

    # Conserve le libelle pour l'afficher dans le test de connexion.
    try:
        client._cozytouch_ent = ent_label or detected.get("label", "")
    except AttributeError:
        pass

    child = str(config.get("pronote_child") or "").strip()
    if child and account == "parent":
        children = list(getattr(client, "children", []) or [])
        wanted = child.casefold()
        match = next(
            (
                item for item in children
                if str(getattr(item, "name", "") or "").strip().casefold() == wanted
            ),
            None,
        )
        if match is None:
            names = ", ".join(
                str(getattr(item, "name", "") or "?") for item in children
            )
            raise PronoteError(
                f"Enfant « {child} » introuvable dans le compte parent."
                + (f" Enfants disponibles : {names}." if names else "")
            )
        try:
            client.set_child(match)
        except Exception as err:  # noqa: BLE001
            raise PronoteError(
                f"Impossible de selectionner l'enfant « {child} » : {err}"
            ) from err

    return client


def _connection_error_message(message: str) -> str:
    lower = message.casefold()
    if "page html is different" in lower:
        return message + (
            " Vérifiez que l'adresse pointe directement vers la page Pronote "
            "de l'établissement (…/pronote/parent.html ou …/pronote/eleve.html)."
        )
    if "refus" in lower or "identifiant" in lower or "password" in lower:
        return message + (
            " Pour un compte parent, utilisez les identifiants du parent, pas "
            "ceux de l'élève."
        )
    return message


def test_connection(config: dict) -> dict:
    """Teste la connexion et la lecture de l'emploi du temps, sans rien écrire.

    Renvoie toujours un dictionnaire : l'interface affiche ``message`` tel quel.
    """
    try:
        client = connect(config)
    except PronoteError as err:
        return {
            "ok": False,
            "status": "connection",
            "message": _connection_error_message(str(err)),
        }

    account = str(config.get("pronote_account") or "student").strip().lower()
    user_info = getattr(client, "info", None)
    child_info = getattr(client, "_selected_child", None)
    if account != "parent":
        child_info = None

    def info_value(info, attribute: str) -> str:
        try:
            return str(getattr(info, attribute, "") or "").strip()
        except Exception:  # noqa: BLE001 - propriete pronotepy indisponible
            return ""

    children = []
    for item in list(getattr(client, "children", []) or []):
        name = info_value(item, "name")
        if name:
            children.append(name)

    establishment = (
        info_value(child_info, "establishment")
        or info_value(user_info, "establishment")
    )

    week_start = date.today() - timedelta(days=date.today().weekday())
    week_end = week_start + timedelta(days=6)
    try:
        lessons = client.lessons(week_start, week_end) or []
        lesson_count = len(lessons)
    except Exception as err:  # noqa: BLE001
        return {
            "ok": False,
            "status": "lessons",
            "message": (
                "Connexion Pronote réussie, mais lecture de l'emploi du temps "
                f"impossible : {err}"
            ),
            "establishment": establishment,
            "user": info_value(user_info, "name"),
            "children": children,
            "child": info_value(child_info, "name"),
            "account": account,
            "url": str(config.get("pronote_url") or ""),
            "ent": str(getattr(client, "_cozytouch_ent", "") or ""),
        }

    message = "Connexion Pronote réussie"
    if lesson_count:
        message += f", {lesson_count} cours lus cette semaine."
    else:
        message += " (aucun cours cette semaine)."

    return {
        "ok": True,
        "status": "ok",
        "message": message,
        "establishment": establishment,
        "user": info_value(user_info, "name"),
        "children": children,
        "child": info_value(child_info, "name"),
        "lessons": lesson_count,
        "account": account,
        "url": str(config.get("pronote_url") or ""),
        "ent": str(getattr(client, "_cozytouch_ent", "") or ""),
    }


# ------------------------------------------------------------ emploi du temps


def parse_week_start(value=None) -> date:
    """Renvoie le lundi de la semaine demandee (ou de la semaine courante)."""
    if value:
        try:
            moment = date.fromisoformat(str(value))
        except ValueError as err:
            raise PronoteError("Date de semaine invalide (format AAAA-MM-JJ).") from err
    else:
        moment = date.today()
    return moment - timedelta(days=moment.weekday())


def normalize_lessons(raw, week_start: date, week_end: date):
    """Transforme les objets Lesson de pronotepy en dictionnaires simples."""
    lessons: list[dict] = []
    canceled = 0

    for lesson in raw:
        start = getattr(lesson, "start", None)
        end = getattr(lesson, "end", None)
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            continue
        if end <= start:
            continue
        if start.date() < week_start or start.date() > week_end:
            continue

        if getattr(lesson, "canceled", False):
            canceled += 1
            continue

        start_minutes = start.hour * 60 + start.minute
        # Un cours qui depasse minuit (rarissime) est borne a la fin de journee.
        if end.date() > start.date():
            end_minutes = DAY_MINUTES
        else:
            end_minutes = end.hour * 60 + end.minute
        if end_minutes <= start_minutes:
            continue

        subject = getattr(lesson, "subject", None)
        lessons.append(
            {
                "weekday": start.weekday(),
                "date": start.date().isoformat(),
                "start": start.strftime("%H:%M"),
                "end": (end if end.date() == start.date() else start.replace(
                    hour=23, minute=59
                )).strftime("%H:%M"),
                "start_minutes": start_minutes,
                "end_minutes": end_minutes,
                "subject": str(getattr(subject, "name", "") or ""),
                "teacher": str(getattr(lesson, "teacher_name", "") or ""),
                "room": str(getattr(lesson, "classroom", "") or ""),
            }
        )

    lessons.sort(key=lambda item: (
        item["weekday"], item["start_minutes"], item["end_minutes"]
    ))
    return lessons, canceled


def fetch_week(config: dict, week_start: date):
    """Lit l'emploi du temps Pronote d'une semaine (lundi -> dimanche)."""
    client = connect(config)
    week_end = week_start + timedelta(days=6)
    try:
        raw = client.lessons(week_start, week_end) or []
    except Exception as err:  # noqa: BLE001
        raise PronoteError(
            f"Lecture de l'emploi du temps Pronote impossible : {err}"
        ) from err
    return normalize_lessons(raw, week_start, week_end)


# --------------------------------------------------------------- construction


def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _clamp_temp(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(16.0, min(30.0, number)), 1)


def _clean_mode(value, default: str) -> str:
    mode = str(value or default)
    return mode if mode in VALID_MODES else default


def _clean_student_status(
    value, default: str = STUDENT_STATUS_EXTERNE
) -> str:
    """Normalise le statut scolaire (accepte les variantes avec espaces/tirets)."""
    status = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    if status in ("demi_pensionnaire", "demi_pensionnaires", "dp"):
        return STUDENT_STATUS_DEMI_PENSIONNAIRE
    if status == STUDENT_STATUS_EXTERNE:
        return STUDENT_STATUS_EXTERNE
    return default


def _merge_blocks(lessons: list[dict], gap: int) -> list[tuple[int, int]]:
    blocks: list[list[int]] = []
    for lesson in lessons:
        start = int(lesson["start_minutes"])
        end = int(lesson["end_minutes"])
        if blocks and start <= blocks[-1][1] + gap:
            blocks[-1][1] = max(blocks[-1][1], end)
        else:
            blocks.append([start, end])
    return [(start, end) for start, end in blocks]


def _merge_intervals(intervals: list[tuple[int, int]], gap: int):
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _time_label(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_time_label(value: str) -> int:
    parts = str(value or "00:00").split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return -1


def _day_scope(
    weekday: int,
    slots: list[dict],
    blocks: list[tuple[int, int]],
    occupied: list[tuple[int, int]],
    student_status: str,
    switch_day: str,
    last_lesson_end: int = 0,
    direct_return: bool = False,
) -> tuple[str, int]:
    """Part de la journee que l'import Pronote a le droit de reecrire.

    Renvoie ``(kind, index)`` :

    - ``("full", 0)`` : toute la journee est remplacee (comportement historique) ;
    - ``("prefix", n)`` : seuls les ``n`` premiers creneaux generes sont ecrits,
      la fin de la journee existante est conservee ;
    - ``("suffix", i)`` : seuls les creneaux generes a partir de l'index ``i``
      sont ecrits, le debut de la journee existante est conserve.

    En garde alternee, l'enfant n'est pas la toute la journee : le vendredi on
    n'ecrit que la matinee (jusqu'au depart), le lundi que la fin de journee.
    Une journee qui se termine le matin (mercredi type) ne voit que son debut
    reecrit : le reste est edite a la main.
    """
    if not slots:
        return ("full", 0)

    if weekday == 4 and switch_day == "friday":
        # Vendredi : la journee s'arrete sur le depart (dernier creneau genere).
        return ("prefix", len(slots))

    if weekday == 0 and switch_day == "monday":
        if _clean_student_status(student_status) == STUDENT_STATUS_DEMI_PENSIONNAIRE:
            # Demi-pensionnaire : il quitte apres la fin des cours de la journee.
            boundary = occupied[-1][1] if occupied else 0
        else:
            # Externe : il quitte apres les cours du matin.
            morning = [end for start, end in blocks if start < MORNING_END_MINUTES]
            boundary = morning[-1] if morning else MORNING_END_MINUTES
        index = next(
            (
                position
                for position, slot in enumerate(slots)
                if _parse_time_label(slot.get("time")) >= boundary
            ),
            len(slots),
        )
        return ("suffix", index)

    # Journee terminee le matin : on ne remplace que le debut (nuit, lever,
    # cours, retour a la maison). Le reste de la journee reste celui de
    # l'utilisateur.
    if last_lesson_end and last_lesson_end <= MORNING_END_MINUTES:
        # Retour direct : la journee est geree par Pronote en entier
        # (retour apres le dernier cours + trajet, confort jusqu'au coucher).
        if direct_return:
            return ("full", 0)
        boundary = occupied[-1][1] if occupied else last_lesson_end
        index = next(
            (
                position
                for position, slot in enumerate(slots)
                if _parse_time_label(slot.get("time")) >= boundary
            ),
            len(slots) - 1,
        )
        return ("prefix", index + 1)

    return ("full", 0)


def build_days(
    lessons: list[dict],
    travel_minutes: int,
    comfort_mode: str,
    comfort_temp: float,
    reduced_mode: str,
    reduced_temp: float,
    wake_minutes: int = 7 * 60,
    student_status: str = STUDENT_STATUS_EXTERNE,
    bedtime_minutes: int = 22 * 60,
    switch_day: str = "monday",
    scopes: list | None = None,
    direct_return: bool = False,
    holiday_days: dict | None = None,
) -> list[list[dict]]:
    """Construit les 7 listes de creneaux a partir des cours normalises.

    La nuit est en reduit de ``bedtime_minutes`` (coucher) a ``wake_minutes``
    (lever), puis la maison passe en confort. Chaque absence (cours + trajet
    aller-retour) repasse en reduit ; le trajet ne s'ajoute qu'aux cours, il
    n'y a plus de temps de preparation.

    Pour un eleve externe, chaque bloc de cours est encadre par le trajet : les
    trous assez longs (pause de midi) deviennent du confort a la maison. Pour un
    demi-pensionnaire, tous les cours de la journee sont consideres comme une
    seule absence continue : pas de confort le midi. Si les cours s'arretent
    avant la fin de la pause dejeuner, l'absence est prolongee jusqu'a
    ``LUNCH_END_MINUTES`` : l'eleve dejeune a la cantine avant de rentrer.
    Avec ``direct_return``, le demi-pensionnaire prolonge moins la cantine et
    une journee terminee le matin passe en portee ``"full"`` : Pronote gere
    alors la journee jusqu'au coucher.

    Une journee sans cours renvoie une liste vide : l'import (manuel ou
    automatique) conserve alors les creneaux existants au lieu d'ecraser la
    journee.

    ``switch_day == "friday"`` : le vendredi, l'enfant ne rentre pas le soir
    (garde alternee). La journee s'arrete donc sur le depart en reduit, car
    le week-end bascule sur la semaine suivante.

    ``holiday_days`` permet de remplacer certains jours (vacances scolaires,
    jours feries) par un planning de journee a la maison. La valeur associee
    est un etat ``{"mode", "temp"}`` ou ``{"skip": True}`` pour conserver le
    planning existant. Ces jours sont toujours importes en portee ``"full"``.
    """
    travel = max(0, min(180, int(travel_minutes)))
    wake = max(0, min(DAY_MINUTES, int(wake_minutes or 0)))
    bedtime = max(0, min(DAY_MINUTES, int(bedtime_minutes or (22 * 60))))
    friday_switch = str(switch_day or "").strip().lower() == "friday"
    status = _clean_student_status(student_status)
    comfort = {"mode": comfort_mode, "temp": comfort_temp}
    reduced = {"mode": reduced_mode, "temp": reduced_temp}
    days: list[list[dict]] = []

    holidays: dict[int, dict] = {}
    for raw_weekday, raw_state in (holiday_days or {}).items():
        try:
            weekday = int(raw_weekday)
        except (TypeError, ValueError):
            continue
        if 0 <= weekday < 7 and isinstance(raw_state, dict):
            holidays[weekday] = raw_state

    for weekday in range(7):
        holiday = holidays.get(weekday)
        if holiday is not None:
            # Jour de vacances / ferie : le planning Pronote de l'ecole ne
            # s'applique pas. « skip » conserve la journee existante.
            if holiday.get("skip"):
                days.append([])
                if scopes is not None:
                    scopes.append(("full", 0))
                continue

            mode = _clean_mode(holiday.get("mode", "off"), "off")
            temp = _clamp_temp(holiday.get("temp", reduced_temp), reduced_temp)
            state = {"mode": mode, "temp": temp}
            slots: list[dict] = []

            def add_holiday(minutes: int, value: dict) -> None:
                if minutes < 0 or minutes >= DAY_MINUTES:
                    return
                if slots and slots[-1]["time"] == _time_label(minutes):
                    slots[-1] = {
                        "time": _time_label(minutes),
                        "mode": value["mode"],
                        "temp": value["temp"],
                    }
                    return
                if (
                    slots
                    and slots[-1]["mode"] == value["mode"]
                    and slots[-1]["temp"] == value["temp"]
                ):
                    return
                slots.append({
                    "time": _time_label(minutes),
                    "mode": value["mode"],
                    "temp": value["temp"],
                })

            if mode == "off":
                add_holiday(0, state)
            else:
                add_holiday(0, reduced)
                if 0 < wake < DAY_MINUTES:
                    add_holiday(wake, state)
                if wake < bedtime < DAY_MINUTES:
                    add_holiday(bedtime, reduced)

            days.append(slots)
            if scopes is not None:
                scopes.append(("full", 0))
            continue

        day_lessons = [item for item in lessons if item["weekday"] == weekday]
        blocks = _merge_blocks(day_lessons, MERGE_GAP_MINUTES)

        # Journee sans cours : rien a ecrire, la journee est preservee.
        if not blocks:
            days.append([])
            if scopes is not None:
                scopes.append(("full", 0))
            continue

        # Demi-pensionnaire : la journee scolaire est continue (cours + eventuels
        # trous passes dans l'etablissement). Un externe, lui, rentre chez lui
        # entre deux blocs assez espaces.
        if status == STUDENT_STATUS_DEMI_PENSIONNAIRE:
            first_start = blocks[0][0]
            last_end = blocks[-1][1]
            # Journee qui s'arrete a midi : l'eleve reste dejeuner a la cantine
            # avant de rentrer, donc pas de confort pendant la pause. Avec le
            # retour direct, pas de prolongation jusqu'a la fin de la cantine.
            if first_start < 12 * 60 and last_end < LUNCH_END_MINUTES and not direct_return:
                last_end = LUNCH_END_MINUTES
            blocks = [(first_start, last_end)]

        # Periode d'absence = cours + trajet aller-retour.
        occupied = []
        for start, end in blocks:
            occupied_start = max(0, start - travel)
            occupied_end = min(DAY_MINUTES, end + travel)
            if occupied_end > occupied_start:
                occupied.append((occupied_start, occupied_end))
        occupied = _merge_intervals(occupied, MIN_HOME_MINUTES)

        slots: list[dict] = []

        def add(minutes: int, state: dict) -> None:
            if minutes < 0 or minutes >= DAY_MINUTES:
                return
            if slots and slots[-1]["time"] == _time_label(minutes):
                slots[-1] = {
                    "time": _time_label(minutes),
                    "mode": state["mode"],
                    "temp": state["temp"],
                }
                return
            # Inutile d'empiler deux creneaux identiques a la suite.
            if (
                slots
                and slots[-1]["mode"] == state["mode"]
                and slots[-1]["temp"] == state["temp"]
            ):
                return
            slots.append(
                {
                    "time": _time_label(minutes),
                    "mode": state["mode"],
                    "temp": state["temp"],
                }
            )

        # Nuit : reduit depuis le coucher de la veille jusqu'au lever.
        add(0, reduced)
        cursor = wake

        for start, end in occupied:
            if end <= cursor:
                continue  # absence terminee avant le lever : rien a faire
            if start > cursor and start - cursor >= MIN_HOME_MINUTES:
                add(cursor, comfort)
            add(max(start, cursor), reduced)
            cursor = max(cursor, end)

        if (
            not (weekday == 4 and friday_switch)
            and cursor < DAY_MINUTES
            and DAY_MINUTES - cursor >= MIN_HOME_MINUTES
        ):
            add(cursor, comfort)
            if cursor < bedtime < DAY_MINUTES:
                add(bedtime, reduced)

        days.append(slots)

        if scopes is not None:
            last_end = max(
                (int(item["end_minutes"]) for item in day_lessons), default=0
            )
            scopes.append(
                _day_scope(
                    weekday,
                    slots,
                    blocks,
                    occupied,
                    status,
                    "friday" if friday_switch else "monday",
                    last_end,
                    direct_return=direct_return,
                )
            )

    return days


def friday_departure_minutes(
    days: list[list[dict]],
    wake_minutes: int = 7 * 60,
    travel_minutes: int = 20,
) -> int:
    """Heure de depart du vendredi (minutes apres minuit).

    Avec une bascule le vendredi, la journee du vendredi s'arrete sur le
    creneau reduit du depart : c'est cette heure qui declenche le passage a la
    semaine suivante. A defaut, on retombe sur lever + trajet.
    """
    try:
        wake = int(wake_minutes)
    except (TypeError, ValueError):
        wake = 7 * 60
    try:
        travel = int(travel_minutes)
    except (TypeError, ValueError):
        travel = 20
    fallback = max(0, min(1439, wake + travel))

    friday = days[4] if len(days) > 4 else []
    for index in range(len(friday) - 1, 0, -1):
        slot = friday[index]
        minutes = _parse_time_label(slot.get("time"))
        if minutes <= 0:
            continue
        # Creneau du matin (lever en confort) : ce n'est pas un depart.
        if minutes <= wake:
            continue
        return max(0, min(1439, minutes))
    return fallback


def build_plan(config: dict, week_start: date, options: dict | None = None) -> dict:
    """Recupere Pronote et renvoie le planning complet, pret pour l'apercu."""
    options = options or {}
    lessons, canceled = fetch_week(config, week_start)

    travel = _clamp_int(
        options.get("travel_minutes", config.get("pronote_travel_minutes", 20)),
        20,
        0,
        180,
    )
    wake_minutes = _clamp_int(
        options.get(
            "wake_minutes",
            config.get("pronote_wake_minutes", 7 * 60),
        ),
        7 * 60,
        0,
        24 * 60,
    )
    student_status = _clean_student_status(
        options.get(
            "student_status",
            config.get("pronote_student_status", STUDENT_STATUS_EXTERNE),
        )
    )
    comfort_mode = _clean_mode(
        options.get("comfort_mode", config.get("pronote_comfort_mode", "heat")),
        "heat",
    )
    comfort_temp = _clamp_temp(
        options.get("comfort_temp", config.get("pronote_comfort_temp", 20)),
        20.0,
    )
    reduced_mode = _clean_mode(
        options.get("reduced_mode", config.get("pronote_reduced_mode", "heat")),
        "heat",
    )
    reduced_temp = _clamp_temp(
        options.get("reduced_temp", config.get("pronote_reduced_temp", 16)),
        16.0,
    )
    bedtime_minutes = _clamp_int(
        options.get("bedtime_minutes", config.get("pronote_bedtime_minutes", 22 * 60)),
        22 * 60,
        0,
        24 * 60,
    )
    switch_day = str(
        options.get("switch_day")
        or config.get("pronote_switch_day")
        or "monday"
    ).strip().lower()
    switch_day = "friday" if switch_day == "friday" else "monday"

    raw_direct = options.get("direct_return", config.get("pronote_direct_return", False))
    direct_return = (
        raw_direct if isinstance(raw_direct, bool)
        else str(raw_direct).strip().lower() in ("1", "true", "yes", "on")
    )

    scopes: list = []
    days = build_days(
        lessons,
        travel,
        comfort_mode,
        comfort_temp,
        reduced_mode,
        reduced_temp,
        wake_minutes,
        student_status,
        bedtime_minutes,
        switch_day,
        scopes,
        direct_return=direct_return,
        holiday_days=options.get("holiday_days"),
    )
    friday_switch = friday_departure_minutes(days, wake_minutes, travel)

    dates = [
        (week_start + timedelta(days=offset)).isoformat() for offset in range(7)
    ]
    return {
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "dates": dates,
        "travel_minutes": travel,
        "wake_minutes": wake_minutes,
        "bedtime_minutes": bedtime_minutes,
        "switch_day": switch_day,
        "friday_switch_minutes": friday_switch,
        "student_status": student_status,
        "comfort": {"mode": comfort_mode, "temp": comfort_temp},
        "reduced": {"mode": reduced_mode, "temp": reduced_temp},
        "lessons": lessons,
        "days": days,
        # Jours remplaces par le calendrier scolaire (vacances / feries).
        "holiday_days": options.get("holiday_days") or {},
        "holidays": options.get("holiday_names") or [],
        # Portee de chaque jour : "full" (remplace), "prefix"/"suffix" (partiel).
        "scopes": scopes,
        "stats": {
            "lessons": len(lessons),
            "canceled": canceled,
            "slots": sum(len(day) for day in days),
        },
    }
