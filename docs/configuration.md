# Configuration

Tout se configure depuis l'interface (**Réglages**). Le fichier `config.json` sert de
source de vérité au démarrage et reste modifiable à la main si besoin.

> `config.json` contient des mots de passe et des jetons. Il est ignoré par git et ne
> doit jamais être publié ni partagé.

## Fichiers

| Chemin | Rôle | Versionné |
| --- | --- | --- |
| `config.json` | Réglages et secrets | Non |
| `config.example.json` | Modèle sans secrets | Oui |
| `data/state.json` | Snapshot de l'état (pièces, appareils, plannings, journal) | Non |
| `data/history/` | Historique horaire append-only | Non |
| `data/sync.json` | État de la synchro quotidienne | Non |
| `data/energy.json` | Cache local de la consommation électrique (MyElectricalData) | Non |

## Clés de `config.json`

### Compte Atlantic (Cozytouch)

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `email`, `password` | vide | Identifiants Cozytouch. Vides = simulation. |
| `mock` | `false` | Force la simulation même avec des identifiants. |

### Réseau et sécurité

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `host` | `0.0.0.0` | Interface d'écoute. `127.0.0.1` = local uniquement. |
| `port` | `8780` | Port HTTP. |
| `allow_remote_admin` | `false` | Autorise les routes sensibles depuis le réseau. |
| `cache_static` | `true` | Charge les fichiers web en RAM au démarrage. |
| `gzip_enabled` | `true` | Compression des réponses. |

### Wiser

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `wiser_enabled` | `false` | Active la passerelle Schneider Wiser. |
| `wiser_host` | vide | Adresse du hub sur le réseau local. |
| `wiser_secret` | vide | Clé locale du hub. |

### Pronote

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `pronote_url` | vide | Adresse de l'établissement. |
| `pronote_username`, `pronote_password` | vide | Identifiants Pronote. |
| `pronote_ent` | vide | Identifiant ENT (optionnel). |
| `pronote_account` | `student` | `student` ou `parent`. |
| `pronote_child` | vide | Prénom/nom de l'enfant si plusieurs. |
| `pronote_travel_minutes` | `20` | Trajet domicile ↔ établissement. |
| `pronote_wake_minutes` | `420` (07:00) | Heure de lever (passage en confort). |
| `pronote_bedtime_minutes` | `1320` (22:00) | Heure de coucher (passage en réduit). |
| `pronote_student_status` | `externe` | `externe` ou `demi_pensionnaire`. |
| `pronote_comfort_mode`, `pronote_comfort_temp` | `heat`, `20` | Consigne de confort. |
| `pronote_reduced_mode`, `pronote_reduced_temp` | `heat`, `16` | Consigne réduite. |
| `pronote_room_ids` | `[]` | Pièces liées au planning. |
| `pronote_weeks` | `[]` | Semaines gérées (`["A"]`, `["B"]` ou les deux). |
| `pronote_switch_day` | `monday` | Jour d'alternance : `monday` ou `friday`. |
| `pronote_room_overrides` | `{}` | Réglages confort/réduit par pièce (`{"1": {"comfort_mode": "cool", "comfort_temp": 24}}`). Les clés absentes héritent des réglages globaux. |
| `pronote_auto_enabled` | `true` | Mise à jour automatique quotidienne. |
| `pronote_sync_minutes` | `180` (03:00) | Heure de la mise à jour automatique. |

`pronote_room_id` (valeur unique) reste accepté pour la compatibilité avec les anciennes
configurations.

### Météo

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `weather_enabled` | `true` | Affiche la météo et la température extérieure. |
| `weather_city` | vide | Libellé affiché. |
| `weather_latitude`, `weather_longitude` | Paris | Coordonnées Open-Meteo (sans clé). |

### Consommation électrique (MyElectricalData / Enedis)

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `med_enabled` | `false` | Active la récupération Enedis. |
| `med_url` | `https://www.myelectricaldata.fr` | Service MyElectricalData. |
| `med_token` | vide | Jeton personnel (validité un an). |
| `med_pdl` | vide | Numéro de PDL (14 chiffres). |
| `med_start` | vide | Début d'historique (`AAAA-MM-JJ`), vide = 3 ans. |
| `energy_file` | `data/energy.json` | Cache local des mesures journalières. |

### Connecteurs d'autres systèmes

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `connectors` | `{}` | Réglages Daikin, Bosch, Netatmo et Mitsubishi (`{"daikin": {"host": "192.168.1.50"}, …}`). Champs détaillés dans [equipements.md](equipements.md). |

### Tailscale
| Clé | Défaut | Rôle |
| --- | --- | --- |
| `tailscale_enabled` | `false` | Connecte le nœud au démarrage. |
| `tailscale_auto_serve` | `false` | Publie le service HTTPS au démarrage. |
| `tailscale_hostname` | `ecasa` | Nom du nœud dans le tailnet. |
| `tailscale_bin` | vide | Chemin du binaire si absent du `PATH`. |
| `tailscale_authkey` | vide | Clé d'authentification (optionnelle). |

### Stockage basse consommation

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `state_file` | `data/state.json` | Snapshot de l'état. |
| `structural_delay` | `3` | Regroupement des écritures d'état (secondes). |
| `history_dir` | `data/history` | Historique horaire. |
| `history_interval` | `900` | Écriture historique au plus toutes les 15 min. |
| `history_days` | `180` | Conservation de l'historique. |

## Réglages de l'interface

| Section | Contenu |
| --- | --- |
| **Apparence** | Thème clair / sombre / auto. |
| **Moteur** | Activation, intervalle de rafraîchissement, état. |
| **Semaines A/B** | Mode d'alternance (parité ISO ou aucune), inversion A/B. |
| **Pièces** | Création, appareils associés, position. |
| **Appareils** | Atlantic et Wiser, capacités, défauts climatisation. |
| **Pronote** | Compte, statut scolaire, pièces liées, semaines, jour d'alternance, mise à jour automatique. |
| **Serveur** | Compte, réseau, port, administration distante. |
| **Tailscale** | Diagnostic, connexion, publication HTTPS. |
| **Stockage** | Export/import, état des écritures. |
| **Électricité** | MyElectricalData, test, historique. |
| **Soutenir** | Liens projet et don. |

## Secrets

Les secrets ne sont **jamais** renvoyés au navigateur. `GET /api/config` expose
uniquement des booléens de présence : `password_set`, `wiser_secret_set`,
`pronote_password_set`, `tailscale_authkey_set`, `med_token_set`.

Pour effacer un secret, utiliser la case dédiée dans l'interface (ou
`*_clear: true` dans le corps de `PUT /api/config`).

