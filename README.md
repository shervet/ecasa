# MeCasa

Pilotage local du chauffage et de la climatisation, pensé pour tourner des années
sur un Raspberry Pi sans user sa carte SD. MeCasa reprend les fonctions de
`cozytouch-scheduler` (Atlantic/Cozytouch, Schneider Wiser, Pronote, météo,
statistiques, semaines A/B, forçages, journal, sauvegarde, Tailscale) avec une
interface mobile-first réellement utilisable au quotidien.

- Backend Python, bibliothèque standard uniquement (`pronotepy` reste optionnel).
- État complet en mémoire, écriture disque par lots.
- Interface sans build ni npm : HTML, CSS et modules ES natifs.
- PWA installable sur Android, iPhone et ordinateur.
- Port par défaut : **8780**.

## Démarrage rapide

### Windows

```bat
cd "C:\Users\Seb\Documents\Projet par défaut\MeCasa"
python -m mecasa.main
```

Puis ouvrir <http://127.0.0.1:8780>. Sans identifiants Atlantic, MeCasa démarre en
**simulation** avec des appareils fictifs : pratique pour découvrir l'interface.

### Raspberry Pi / Linux

```bash
cd ~/MeCasa
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # facultatif : Pronote
.venv/bin/python -m mecasa.main
```

Installation en service systemd :

```bash
sudo ./install-pi.sh
sudo systemctl status mecasa
sudo journalctl -u mecasa -f
```

Le service redémarre tout seul et survit aux coupures de courant.

Arrêt manuel : ./stop.sh (Linux/macOS) ou stop.bat (Windows) arrête MeCasa,
pm2, les unités systemd Node et le conteneur MyElectricalData local.

## Configuration

Au premier lancement, `config.json` est créé depuis les valeurs par défaut.
`config.example.json` sert de modèle. Les réglages sont aussi modifiables depuis
**Réglages** dans l'interface.

| Clé | Rôle |
| --- | --- |
| `email`, `password` | Compte Cozytouch. Absents = simulation. |
| `host`, `port` | Écoute HTTP (`0.0.0.0` pour le réseau local, `127.0.0.1` pour local seul). |
| `allow_remote_admin` | Autorise les réglages depuis une autre machine du réseau. |
| `mock` | Force la simulation, même avec des identifiants. |
| `wiser_enabled`, `wiser_host`, `wiser_secret` | Passerelle Schneider Wiser. |
| `pronote_*` | Compte, établissement, trajets, consignes confort/réduit. |
| `weather_*` | Coordonnées Open-Meteo (sans clé). |
| `med_enabled`, `med_url`, `med_token`, `med_pdl`, `med_start` | Consommation Enedis via MyElectricalData. Token local, jamais renvoyé. |
| `tailscale_*` | Nœud Tailscale (`tailscale_hostname`, `tailscale_bin`) et publication `tailscale serve`. |
| `state_file` | Snapshot JSON de l'état complet. |
| `history_dir`, `history_interval`, `history_days` | Mesures horaires append-only et conservation. |
| `structural_delay` | Regroupement des écritures d'état (secondes). |
| `cache_static`, `gzip_enabled` | Fichiers web en RAM et compression. |

Les changements de compte, réseau ou stockage demandent un redémarrage :
l'interface propose le bouton directement.

### Secrets

Les mots de passe et clés ne sont **jamais** renvoyés au navigateur.
`GET /api/config` expose seulement `password_set`, `wiser_secret_set`,
`pronote_password_set`, `tailscale_authkey_set` et `med_token_set`.

## Accès distant et app Android (Tailscale)

L'app **MeCasa App** charge `https://mecasa.<tailnet>.ts.net/` (port 443). Côté
serveur, tout est vérifiable dans **Réglages → Tailscale** : un diagnostic
indique précisément ce qui manque (client, connexion, MagicDNS, HTTPS, nom du
nœud, publication).

L'app utilise l'**application Tailscale officielle** installée sur le téléphone
(pas de VPN embarqué : Android n'autorise qu'un seul VPN actif). Si le tailnet
n'est pas joignable, elle affiche **« Ouvrir Tailscale »** et **« Configurer »** :
l'adresse du serveur y est modifiable, testable et enregistrée.

APK : [release v0.2.0](https://github.com/shervet/mecasa-app/releases/tag/v0.2.0)
(`mecasa-app-debug.apk`, toutes architectures, signature debug).

Configuration locale de Tailscale, étape par étape. Chaque étape indique quoi
vérifier avant de passer à la suivante. Le même guide est disponible dans
**Réglages → Tailscale → Ouvrir l'aide**.

### 1. Installer Tailscale sur la machine MeCasa

```bash
# Windows (PowerShell)
winget install -e --id Tailscale.Tailscale
# macOS
brew install --cask tailscale
# Linux / Raspberry Pi
curl -fsSL https://tailscale.com/install.sh | sh
```

**Vérifier** : `tailscale version` affiche un numéro de version. Sur Windows,
MeCasa détecte aussi `C:\Program Files\Tailscale\tailscale.exe` hors PATH.

### 2. Connecter la machine avec le nom `mecasa`

```bash
sudo tailscale up --hostname=mecasa   # Windows : sans sudo
```

**Vérifier** : `tailscale status` montre une ligne `mecasa` avec une adresse
`100.x.y.z`, et `tailscale ip -4` renvoie cette adresse. Si le nœud portait un
autre nom, la commande le renomme.

### 3. Activer MagicDNS et HTTPS Certificates

Dans la [console Tailscale → DNS](https://login.tailscale.com/admin/dns) :
**MagicDNS → Enable MagicDNS**, puis **HTTPS Certificates → Enable HTTPS…**.
C'est un réglage du tailnet, à faire une seule fois.

**Vérifier** : dans MeCasa, **Réglages → Tailscale**, les lignes *MagicDNS* et
*HTTPS Certificates* passent au vert après **Actualiser** (attendre ~30 s
l'émission du certificat si besoin).

### 4. Publier MeCasa en HTTPS sur le port 443

```bash
sudo tailscale serve --bg --set-path=/ http://127.0.0.1:8780
```

**Vérifier** : `tailscale serve status` affiche
`https://mecasa.<tailnet>.ts.net (tailnet only)` puis
`/ proxy http://127.0.0.1:8780`.

### 5. Tester depuis le téléphone

Le téléphone doit avoir Tailscale installé et connecté au même compte, puis
ouvrir `https://mecasa.<tailnet>.ts.net/` (sans `:8780`).

**Vérifier** : la page MeCasa se charge avec un cadenas HTTPS valide. Si le nom
ne résout pas, tester d'abord `https://100.x.y.z/`.

### 6. Rendre la configuration durable

`--bg` conserve la publication après un redémarrage du service. Dans MeCasa,
activer **Activer Tailscale** et **Publier automatiquement**, puis
**Enregistrer** : au démarrage, MeCasa reconnecte le nœud et republie le service.
Une clé d'authentification est optionnelle (console → Settings → Keys).

**Vérifier** : après redémarrage de la machine, `tailscale serve status` et
`https://mecasa.<tailnet>.ts.net/` fonctionnent encore.

### Dépannage

| Message | Cause | Solution |
| --- | --- | --- |
| `Tailscale introuvable` | Binaire hors PATH | Renseigner **Chemin de tailscale** (`C:\Program Files\Tailscale\tailscale.exe` sur Windows). |
| `HTTPS is not enabled in your tailnet` | Étape 3 incomplète | Activer HTTPS Certificates, attendre 30 s, Actualiser puis Publier. |
| `502` / `connection refused` | MeCasa n'écoute pas sur l'adresse attendue | `host` = `0.0.0.0` ou `127.0.0.1`, puis republier avec le bon port. |
| `404` / `Not Found` | Publication sur un sous-chemin | Republier avec `--set-path=/`. |
| Le nom MagicDNS ne résout pas | MagicDNS désactivé ou téléphone hors tailnet | Utiliser `https://100.x.y.z/` en attendant. |
| Mauvais nom de nœud | Nœud renommé | `tailscale up --hostname=mecasa` ou console → Machines → Renommer. |
| Le nœud s'appelle `mecasa-1` | Le nom `mecasa` est réservé (ancien nœud ou **Service Tailscale**) | Supprimer le Service dans console → Services (ou l'ancien nœud), puis relancer `tailscale up --hostname=mecasa`. |
| Retirer l'accès distant | — | `sudo tailscale serve reset` (le nœud reste connecté). |

MeCasa automatise tout ou partie :

- `tailscale_enabled` : au démarrage, connecte le nœud (clé d'authentification
  si nécessaire) avec le nom `tailscale_hostname` (défaut `mecasa`) ;
- `tailscale_auto_serve` : au démarrage, exécute l'équivalent de
  `tailscale serve --bg --set-path=/ http://127.0.0.1:<port>` ;
- les boutons **Connecter / Publier / Dépublier** appliquent les mêmes commandes
  à chaud.

Le binaire est cherché dans le `PATH`, puis aux emplacements habituels
(`C:\Program Files\Tailscale\tailscale.exe` sur Windows, `/usr/bin/tailscale`
sur Linux, etc.). Si besoin, indique son chemin dans **Réglages → Tailscale →
Chemin de tailscale** (ou via `tailscale_bin`). Le bouton **Connecter** ne
demande une clé que si le nœud n'est pas déjà autorisé : sinon MeCasa affiche
le lien de connexion Tailscale (`AuthURL`) directement dans le diagnostic.

Pour que `tailscale serve` atteigne MeCasa, `host` doit rester `0.0.0.0` ou
`127.0.0.1` (pas une adresse LAN précise). Le diagnostic le signale.

## Stockage basse consommation

Le point sensible sur Raspberry Pi est l'usure de la carte SD. MeCasa applique
trois règles :

1. **Tout l'état vit en RAM** : pièces, appareils, plannings, forçages, journal.
2. **Un snapshot JSON débouncé** (`data/state.json`) : au plus une écriture toutes
   les `structural_delay` secondes, et seulement si quelque chose a changé.
3. **Un historique append-only par mois** (`data/history/history-AAAA-MM.jsonl`) :
   les mesures sont agrégées par heure en mémoire puis ajoutées par lots, au plus
   une fois toutes les `history_interval` secondes (900 s par défaut).

Résultat : quelques écritures par heure au lieu d'une par minute, et aucune
lecture disque par requête HTTP (les fichiers web sont chargés en RAM au
démarrage, servis avec gzip et ETag).

`data/` et `config.json` ne doivent pas être versionnés.

## Interface

| Page | Contenu |
| --- | --- |
| **Accueil** | Ambiance par pièce, consignes, prochain changement, forçage rapide. |
| **Planning** | Agenda tactile 24 h, éditeur de créneaux, modèles, copie A↔B, imports Pronote et Wiser. |
| **Stats** | Courbes ambiance / consigne / extérieur, consommation électrique multi-années, KPI, détail horaire ou journalier. |
| **Journal** | Derniers événements, filtres, recherche, export JSON. |
| **Réglages** | Apparence, moteur, semaines A/B, pièces, appareils, clim, connecteurs, électricité, stockage, serveur, Tailscale. |

Navigation par hash (`#/home`, `#/planning`, `#/stats`, `#/logs`, `#/settings`),
thème clair/sombre/auto, bottom bar sur téléphone, sidebar sur ordinateur.

### Défauts de la climatisation Atlantic

La section **Clim** des réglages fixe ce que le moteur applique à chaque cycle
sur les appareils Atlantic qui exposent ces capacités :

- **Soufflerie** (`atlantic_fan_default`) : `""` (inchangé), `low`, `med`,
  `high` ou `auto` → capacité `100801` (1/2/3/5).
- **Orientation du flux** (`atlantic_airflow_default`) : `""` (inchangé),
  `swing` (balayage) ou `pos1` … `pos6` (position fixe) → capacités `100804`
  puis `100803`.

Une valeur vide laisse le réglage fait sur la télécommande. Aucune écriture
soufflerie/orientation n'est faite quand l'appareil est à l'arrêt (`mode 0`),
et les deux capacités sont réécrites après un changement de mode, car certaines
télécommandes les réinitialisent. La feuille d'un appareil affiche l'état courant
(« Soufflerie … », « Orientation … »).

### Consommation électrique (MyElectricalData)

La carte **Consommation électrique** des statistiques compare les mêmes périodes
d'une année sur l'autre : jour, semaine ISO, mois ou année, avec une série par
année. Les données viennent de l'API Enedis exposée par
[MyElectricalData](https://www.myelectricaldata.fr).

Mise en place, dans **Réglages → Électricité** :

1. Créer un compte MyElectricalData et connecter le compteur Linky (Enedis).
2. Générer un token dans **Compte → Token** (validité un an).
3. Renseigner l'adresse du service, le **PDL** (14 chiffres) et le token.
4. Laisser « Début de l'historique » vide pour récupérer les 3 dernières
   années (limite Enedis de 1095 jours), ou choisir une date précise
   (`AAAA-MM-JJ`).

Le token reste local : `GET /api/config` expose seulement `med_token_set`.
Les réponses Enedis (Wh) sont converties en kWh. Les appels sont mis en cache
**en mémoire** jusqu'à la prochaine synchronisation automatique (3 h le
lendemain, aucune écriture disque) et découpés par année, ce qui reste sous le
quota MyElectricalData de 50 appels/jour/PDL. Le bouton **Tester la connexion**
interroge les 7 derniers jours sans enregistrer la configuration. Le bouton
**Actualiser** de la carte force un nouvel appel immédiat.

**Synchronisation automatique** : une fois par jour vers 3 h, MeCasa récupère la
consommation électrique et le planning Pronote (voir ci-dessous). Chaque tâche
n'est tentée qu'une fois par jour : un échec est journalisé et attend le
lendemain, ou une synchro manuelle. La synchro manuelle (bouton Actualiser,
import Pronote) reste toujours possible et ne réinitialise pas le compteur
quotidien.

### Planning Pronote et pièce liée

Dans **Réglages → Pronote**, renseigner le compte et sélectionner la **pièce
liée au planning**. Chaque nuit vers 3 h, MeCasa récupère les semaines A et B
depuis Pronote et les applique à cette pièce sans validation, en conservant les
créneaux du week-end déjà saisis à la main. L'import manuel depuis la page
**Planning** cible la même pièce (ou l'appareil choisi) et n'écrase pas les
week-ends.

### Redémarrage des services externes

`POST /api/control/restart` (bouton **Redémarrer** de l'interface) relance
également, quand ils sont présents, **pm2**, les unités **systemd** Node et le
conteneur Docker **MyElectricalData** local. Ces relances sont tolérantes aux
absences (journalisées, jamais bloquantes).

## API

Toutes les routes renvoient du JSON. Les routes sensibles sont réservées à la
machine locale, sauf si `allow_remote_admin` est activé.

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/api/state` | Snapshot complet du moteur. |
| GET | `/api/meta` | Modes, jours, heure serveur. |
| GET | `/api/system` | Mémoire, disque, uptime, réseau. |
| GET | `/api/config` | Configuration publique (sans secrets). |
| PUT | `/api/config` | Enregistre la configuration. |
| POST | `/api/config/test` | Teste le compte Cozytouch. |
| PUT | `/api/settings` | Moteur, alternance A/B et défauts clim. |
| GET | `/api/rooms`, POST/PUT/DELETE `/api/rooms/{id}` | Pièces. |
| PUT/DELETE | `/api/devices/{id}` | Appareils. |
| PUT | `/api/programs/{id}/{A\|B}` | Planning d'une pièce ou d'un appareil. |
| POST | `/api/override` | Forçage temporaire. |
| DELETE | `/api/override/{device_id}` | Reprise du planning. |
| GET/PUT | `/api/weeks` | Surcharges de semaine A/B. |
| GET | `/api/stats?range=day\|week\|month&date=…&room_id=…` | Statistiques. |
| GET | `/api/energy?view=day\|week\|month\|year&month=…&refresh=1` | Consommation électrique (`refresh=1` force l'appel réseau). |
| POST | `/api/energy/test` | Teste le token et le PDL MyElectricalData. |
| POST | `/api/apply`, `/api/sync` | Applique ou resynchronise. |
| POST | `/api/pronote/search\|test\|preview\|import` | Pronote. |
| GET | `/api/wiser/schedule/{id}`, POST `/api/wiser/import` | Wiser. |
| GET | `/api/tailscale/status`, POST `/api/tailscale/…` | Tailscale. |
| GET | `/api/export`, POST `/api/import` | Sauvegarde complète. |
| POST | `/api/storage/flush` | Force l'écriture disque. |
| DELETE | `/api/logs` | Vide le journal (mémoire et snapshot). |
| POST | `/api/control/restart\|stop` | Redémarrage ou arrêt propre. |

## Migration depuis cozytouch-scheduler

```bash
python tools/migrate_from_cozytouch.py \
  --db "../cozytouch-scheduler/data/cozytouch.db"
```

La migration reprend réglages, pièces, appareils, plannings et surcharges A/B.
Les anciennes mesures et le journal ne sont pas copiés : MeCasa repart sur son
historique horaire, beaucoup plus économe en écritures.

## Tests

```bash
python -m unittest discover -s tests -v
```

Les tests couvrent le stockage (persistance, rechargement, forçages, créneaux,
journal), les règles de semaine, l'agrégation des statistiques, les défauts de
climatisation Atlantic (détection des capacités, application soufflerie /
orientation, non-réécriture, mode Arrêt) et la consommation électrique
(parsing Enedis, cache mémoire, agrégation multi-années) et la synchronisation
quotidienne (synchro unique par jour, import Pronote sur pièce, services
externes).

## Sécurité

- Aucune dépendance réseau sortante en dehors d'Atlantic, Wiser, Open-Meteo,
  Pronote, Tailscale et MyElectricalData.
- CSP stricte, pas de script inline, pas de CDN.
- Les routes d'administration sont bloquées depuis le réseau par défaut.
- Pour l'accès à distance, préférer Tailscale à l'ouverture d'un port sur la box.

## Structure

```
mecasa/            backend Python (serveur, moteur, stockage, connecteurs)
web/               interface statique (HTML/CSS/JS, PWA)
tools/             migration
tests/             tests unitaires
systemd/           unité de service
data/              état et historique (non versionnés)
config.json        configuration locale (non versionnée)
```

## Dépannage

- **« Serveur injoignable »** : vérifier que `python -m mecasa.main` tourne et que
  le port 8780 n'est pas utilisé (`netstat -ano | findstr 8780` sous Windows).
- **Mode simulation inattendu** : renseigner `email` et `password` dans Réglages →
  Atlantic, puis redémarrer.
- **Pronote indisponible** : `pip install pronotepy` (facultatif).
- **Wiser non détecté** : vérifier que le Hub est sur le même réseau, puis
  Réglages → Wiser → « Chercher sur le réseau ».
- **Carte SD** : ne pas baisser `history_interval` sous 300 s sans raison.
