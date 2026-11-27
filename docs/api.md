# API HTTP

Toutes les routes renvoient du JSON. Le serveur écoute sur `http://<hôte>:<port>`
(8780 par défaut) et sert aussi l'interface statique.

## Sécurité

- Les routes **sensibles** (réglages, Pronote, Tailscale, arrêt/redémarrage, import)
  sont réservées à la machine locale, sauf si `allow_remote_admin` est activé.
- « Machine locale » signifie **un vrai loopback** (127.0.0.0/8, `::1`) ou le
  réseau Tailscale (100.64.0.0/10). Les adresses du réseau local privé
  (192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12) ne comptent **pas** comme locales :
  pour administrer eCasa depuis le Wi‑Fi de la maison, active
  `allow_remote_admin` dans `config.json`, ou passe par Tailscale.
- Les routes en lecture (`/api/state`, `/api/meta`, `/api/energy`,
  `/api/weather`, `/api/system`, `/api/health`) restent accessibles sur le
  réseau local.
- Aucune authentification par jeton : eCasa est prévu pour un réseau de confiance
  (maison) ou un accès via Tailscale.
- Les secrets ne sont jamais renvoyés : seulement des booléens `*_set`.

## État et configuration

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/api/state` | Snapshot complet du moteur (pièces, appareils, plannings, forçages). |
| GET | `/api/meta` | Modes, jours, heure serveur. |
| GET | `/api/system` | Mémoire, disque, uptime, réseau, version. |
| GET | `/api/config` | Configuration publique (sans secrets). |
| PUT | `/api/config` | Enregistre la configuration. |
| POST | `/api/config/test` | Teste le compte Cozytouch. |
| PUT | `/api/settings` | Moteur, alternance A/B, défauts climatisation. |
| GET | `/api/weeks` | Surcharges de semaine A/B. |
| PUT | `/api/weeks` | Enregistre les surcharges. |
| GET | `/api/models` | Liste les modèles de planning (persistés côté serveur). |
| PUT | `/api/models` | Enregistre l'ensemble des modèles (`{"models":[...]}`). |

## Pièces, appareils, planning

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/api/rooms` | Liste des pièces. |
| POST | `/api/rooms` | Crée une pièce. |
| PUT/DELETE | `/api/rooms/{id}` | Modifie ou supprime une pièce. |
| PUT/DELETE | `/api/devices/{id}` | Modifie ou supprime un appareil. |
| PUT | `/api/programs/{id}/{A\|B}` | Planning d'une pièce ou d'un appareil. |
| POST | `/api/override` | Forçage temporaire (`device_id`, `mode`, `temp`, `minutes`). |
| DELETE | `/api/override/{device_id}` | Reprise du planning. |
| POST | `/api/apply` | Applique immédiatement l'état cible. |
| POST | `/api/sync` | Resynchronise les appareils. |

## Pronote

| Méthode | Route | Rôle |
| --- | --- | --- |
| POST | `/api/pronote/search` | Recherche un établissement. |
| POST | `/api/pronote/test` | Teste le compte. |
| POST | `/api/pronote/preview` | Aperçu d'une semaine (`week`, `switch_day`). |
| POST | `/api/pronote/import` | Importe (`room_id`, `weeks`, `days`, `scopes`, `switch_day`). |

`preview` renvoie aussi `scopes` : la portion de chaque jour que l'import a le droit
d'écrire (`full`, `prefix`, `suffix`). Le renvoyer tel quel à `import` garantit un
résultat identique à l'aperçu.

## Calendrier scolaire

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/api/school-calendar?zone=A` | Vacances de la zone, prochaine fermeture, état d'activation. |
| POST | `/api/school-calendar/refresh` | Force le téléchargement (`{zone}`), erreur 502 si l'API est injoignable. |

Le calendrier est mis en cache dans `data/school_calendar.json`. L'import
Pronote remplace les jours de fermeture par le planning de vacances choisi,
uniquement pour les semaines A/B déclarées « avec l'enfant ».

## Wiser

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/api/wiser/find` | Cherche le hub sur le réseau. |
| GET | `/api/wiser/schedules` | Liste les plannings disponibles. |
| GET | `/api/wiser/schedule/{id}` | Planning d'un appareil. |
| POST | `/api/wiser/import` | Importe dans eCasa. |

## Connecteurs (Daikin, Bosch, Netatmo, Mitsubishi)

| Méthode | Route | Rôle |
| --- | --- | --- |
| POST | `/api/connectors/test` | Teste un connecteur (`{"id": "daikin"}`) et renvoie ses appareils. |
| GET | `/api/connectors/{id}/devices` | Liste les appareils du connecteur (mode, consigne, ambiance). |
| POST | `/api/connectors/{id}/apply` | Applique un mode et une consigne (`{"device_id": …, "mode": "heat", "temp": 20.5}`). |
| GET | `/api/discover` | Liste les appareils découverts sur le compte Atlantic. |
| POST | `/api/discover` | Rafraîchit la découverte des appareils. |

Le catalogue (libellés, champs, état) est exposé par `GET /api/meta` (clé
`connectors`) et rendu en panneaux dépliables dans Réglages → Appareils.

## Statistiques, météo, énergie

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/api/stats?range=day\|week\|month&date=…&room_id=…` | Statistiques thermiques. |
| GET | `/api/weather` | Météo et température extérieure. |
| GET | `/api/weather/search?q=…` | Recherche de ville. |
| GET | `/api/energy?view=day\|week\|month\|year&month=…&refresh=1&as_of=1` | Consommation électrique (cache local `data/energy.json`, repli sur les données locales avec `warning` si MED indisponible). Sans `as_of`, la vue `year` additionne les années complètes (jusqu'à fin d'année pour les années passées). Paramètre optionnel `as_of=1` (« À date ») : chaque année s'arrête à la dernière consommation connue de l'année en cours (vues week/month/year ; la vue day est inchangée ; réponse : champs `as_of` et `as_of_limit`). |
| POST | `/api/energy/test` | Teste le jeton et le PDL. |

## Journal, sauvegarde, exploitation

Le journal est inclus dans `GET /api/state` (clé `logs`) : la page Journal de
l'interface le lit depuis l'état temps réel, sans route dédiée.

| Méthode | Route | Rôle |
| --- | --- | --- |
| DELETE | `/api/logs` | Vide le journal. |
| GET | `/api/export` | Sauvegarde complète JSON. |
| POST | `/api/import` | Restaure une sauvegarde. |
| POST | `/api/storage/flush` | Force l'écriture disque. |
| GET | `/api/health` | Sonde de vie (200 = OK). |
| POST | `/api/control/restart` | Redémarre le service et les services externes. |
| POST | `/api/control/stop` | Arrêt propre. |

## Tailscale

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/api/tailscale/status` | Diagnostic complet. |
| POST | `/api/tailscale/up` | Connecte le nœud. |
| POST | `/api/tailscale/serve/enable` | Publie en HTTPS. |
| POST | `/api/tailscale/serve/disable` | Dépublie. |
| POST | `/api/tailscale/install` | Installe / désinstalle le client. |
| POST | `/api/tailscale/uninstall` | Désinstalle le client. |

## Mise à jour

| Méthode | Route | Rôle |
| --- | --- | --- |
| GET | `/api/update/check` | Vérifie si une mise à jour est disponible sur origin (git fetch + comparaison) et rafraîchit le cache quotidien. |
| POST | `/api/update/apply` | Applique la mise à jour (git pull + dépendances) puis redémarre le serveur. |

Le snapshot `/api/state` expose aussi un objet `update`
(`available`, `behind`, `current`, `remote`, `branch`, `checked_at`, `error`)
alimenté par la vérification quotidienne : il permet d'afficher la pastille
sans lancer git à chaque requête.

## Exemples

```bash
# État complet
curl -s http://127.0.0.1:8780/api/state | jq .

# Forcer le salon en confort 21 °C pendant 2 h
curl -s -X POST http://127.0.0.1:8780/api/override \
  -H 'Content-Type: application/json' \
  -d '{"device_id": 1, "mode": "heat", "temp": 21, "minutes": 120}'

# Aperçu Pronote (semaine du 9 novembre 2026, bascule le lundi)
curl -s -X POST http://127.0.0.1:8780/api/pronote/preview \
  -H 'Content-Type: application/json' \
  -d '{"week": "2026-11-09", "switch_day": "monday"}'
```
