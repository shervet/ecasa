# Dépannage

## Démarrage et réseau

| Symptôme | Cause | Solution |
| --- | --- | --- |
| « Serveur injoignable » | Le processus ne tourne pas | `python -m ecasa.main` et vérifier le port : `netstat -ano \| findstr 8780` (Windows), `ss -ltnp \| grep 8780` (Linux). |
| Page blanche / interface absente | Fichiers web manquants | Vérifier que le dossier `web/` est présent à côté de `ecasa/`. |
| Inaccessible depuis le téléphone | `host` sur `127.0.0.1` | Passer `host` à `0.0.0.0` dans Réglages → Serveur, puis redémarrer. |
| 403 sur les routes de réglage depuis le réseau | Administration distante désactivée | Activer `allow_remote_admin` (réseau de confiance) ou passer par Tailscale. |
| Port déjà utilisé | Autre service | Changer `port` et mettre à jour la publication Tailscale. |

## Comptes et équipements

| Symptôme | Cause | Solution |
| --- | --- | --- |
| Mode simulation inattendu | Aucun identifiant Atlantic | Réglages → Serveur : saisir l'email et le mot de passe, puis redémarrer. |
| « Authentification refusée » | Identifiants Atlantic | Vérifier sur l'application Atlantic officielle avant de réessayer. |
| Appareil sans capabilities | Télécommande limitée | L'appareil n'expose pas la capacité : eCasa ne force rien. |
| Wiser introuvable | Hub hors réseau | Vérifier que le hub est joignable, puis « Chercher sur le réseau ». |
| Wiser en erreur de clé | Clé locale expirée | Reprendre la clé dans l'application Wiser et la ressaisir. |

## Planning et moteur

| Symptôme | Cause | Solution |
| --- | --- | --- |
| Le chauffage ne suit pas le planning | Moteur en pause | Réglages → Moteur → activer, puis « Appliquer ». |
| Consigne non appliquée | Appareil en forçage | Annuler le forçage (il expire tout seul). |
| Créneaux verrouillés dans le Planning | Jour géré par Pronote | Import partiel : seuls les créneaux Pronote sont verrouillés ; le reste est modifiable. |
| La semaine A ne change pas | Mode d'alternance | Réglages → Semaines A/B : parité ISO ou « Pas d'alternance ». |
| Modèles disparus après nettoyage navigateur | Anciens modèles stockés localement | Les modèles sont désormais enregistrés côté serveur (`data/state.json`) via le bouton « Enregistrer » de la page Modèles ; ils sont migrés automatiquement au premier chargement si présents sur l'appareil. |
| « La consigne n'a pas été confirmée » | Ordre Atlantic lent | C'est un **avertissement**, pas une erreur : l'ordre est généralement appliqué avec du retard. |

## Pronote

| Symptôme | Cause | Solution |
| --- | --- | --- |
| « pronotepy introuvable » | Paquet optionnel absent | `pip install -r requirements.txt` puis redémarrer. |
| « Identifiants refusés » | Mauvais type de compte | Vérifier `Élève` / `Parent` et l'identifiant ENT. |
| « Aucune semaine B trouvée » | Mode d'alternance | Réglages → Semaines A/B → parité ISO, ou choisir une autre semaine source. |
| Rien n'est importé | Aucune pièce liée | Cocher au moins une pièce dans Réglages → Pronote. |
| Le créneau du vendredi disparaît | Jour d'alternance = vendredi | Normal : seule la matinée du vendredi est gérée. |
| Le lundi n'est importé qu'en fin de journée | Jour d'alternance = lundi | Normal : l'enfant arrive après l'école. |
| Échec chaque nuit | Pronote indisponible ou mot de passe changé | Relancer l'import manuel et lire le Journal. |

## Stockage et carte SD

| Symptôme | Cause | Solution |
| --- | --- | --- |
| La carte SD s'use | `history_interval` trop bas | Ne pas descendre sous 300 s ; `history_days` limite la conservation. |
| `data/` grossit | Historique long | Régler `history_days` (défaut 180) ou purger l'historique. |
| État perdu au redémarrage | Fichier d'état introuvable | Vérifier `state_file` et les droits d'écriture du dossier `data/`. |
| Écritures groupées suspectes | `structural_delay` élevé | Valeur par défaut : 3 s. |

## Tailscale

| Message | Cause | Solution |
| --- | --- | --- |
| `Tailscale introuvable` | Binaire hors PATH | Renseigner le chemin (`C:\Program Files\Tailscale\tailscale.exe` sur Windows). |
| `HTTPS is not enabled in your tailnet` | Étape MagicDNS/HTTPS incomplète | Activer HTTPS Certificates, attendre 30 s, Actualiser, Publier. |
| `502` / `connection refused` | Mauvaise adresse d'écoute | `host` = `0.0.0.0` ou `127.0.0.1`, republier avec le bon port. |
| Le nom MagicDNS ne résout pas | MagicDNS désactivé | Activer MagicDNS dans la console Tailscale. |
| `ecasa-1` au lieu de `ecasa` | Nom réservé | Supprimer l'ancien nœud ou le Service Tailscale, puis `tailscale up --hostname=ecasa`. |
| Ancienne adresse `https://mecasa.<tailnet>.ts.net` encore accessible | Ancien nom de nœud publié par `tailscale serve` | `tailscale serve reset` puis `tailscale serve --bg --set-path=/ http://127.0.0.1:8780` (ou Réglages → Tailscale → Publier). |
| Page ouverte en `http://192.168.x.x:8780` | Accès direct au réseau local | Contexte non sécurisé : pas de service worker, donc pas de PWA hors ligne ni de rafraîchissement complet. Utiliser l'adresse HTTPS `https://<nœud>.<tailnet>.ts.net/`. |
| Avertissement « non sécurisé » / `ERR_CERT_*` | Accès par IP ou certificat auto-signé | Passer par `https://<nœud>.<tailnet>.ts.net/` (certificat Tailscale reconnu par tous les navigateurs). |

## Consommation électrique

| Symptôme | Cause | Solution |
| --- | --- | --- |
| Aucune donnée Enedis | Jeton ou PDL invalide | Réglages → Électricité → « Tester la connexion ». |
| Quota dépassé / erreur MyElectricalData | Quota ou indisponibilité amont | Les mesures sont mises en cache localement (`data/energy.json`) et seules les dates manquantes/la semaine récente sont redemandées. En cas d'erreur, eCasa affiche les données locales connues avec un avertissement. |
| Historique vide au début | Date de début | Laisser « Début de l'historique » vide pour 3 ans. |

## Obtenir de l'aide

1. Consulter la page **Journal** de eCasa et exporter les événements (`/api/logs`).
2. Vérifier la version : `GET /api/system` → `app.version`.
3. Ouvrir une issue avec la version, la plateforme (Windows/Linux/Pi), l'extrait de
   journal **anonymisé** (aucun mot de passe, aucun identifiant).
