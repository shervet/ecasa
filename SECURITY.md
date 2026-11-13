# Sécurité

## Versions supportées

| Version | Support |
| --- | --- |
| 1.0.x | ✅ |

## Signaler une vulnérabilité

Merci de **ne pas ouvrir d'issue publique** pour une faille de sécurité.

Écris à l'auteur via la page GitHub du dépôt (« Security advisory » privé) ou en
mentionnant le problème dans une discussion privée.

Elements à préciser :

- version de eCasa (`GET /api/system` → `app.version`) et plateforme ;
- étapes de reproduction ;
- impact attendu ;
- journal anonymisé si utile (sans identifiant ni secret).

Tu recevras un accusé de réception sous quelques jours et une indication de la
correction prévue. La validation publique se fait avec une **GitHub Security
Advisory**.

## Périmètre de sécurité

eCasa est un serveur **local** : pas de compte, pas de cloud, pas de télémétrie.

- Les routes d'administration (`/api/config`, `/api/settings`, `/api/pronote/*`,
  `/api/control/*`, `/api/tailscale/*`, import/export) sont **refusées depuis le
  réseau** par défaut (`allow_remote_admin: false`).
- Les mots de passe, clés Wiser, jetons MyElectricalData et clés d'authentification
  ne sont **jamais** renvoyés au navigateur : seuls des booléens `*_set` le sont.
- CSP stricte, aucun script inline, aucun CDN, aucune ressource externe.
- Le stockage est en RAM avec écritures groupées ; `data/` contient l'état et
  l'historique (à protéger comme une donnée personnelle).

## Bonnes pratiques côté déploiement

- Ne pas exposer eCasa directement sur Internet : passer par **Tailscale** ou un
  reverse proxy avec authentification.
- Garder `allow_remote_admin` à `false` sauf réseau de confiance.
- Mettre à jour régulièrement le système hôte et Python.
- Sauvegarder `config.json` et `data/` (ils contiennent des secrets et l'historique).
