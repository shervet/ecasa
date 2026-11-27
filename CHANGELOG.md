# Journal des modifications

Toutes les modifications notables d'eCasa sont consignées ici. Le format
s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.0.0/) et le
projet suit le [versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié] — revue complète de l'application (26–27 novembre 2026)

Revue complète du serveur, du moteur et de l'interface, suivie de la correction
des anomalies confirmées. Analyse menée par modules (moteur, API, connecteurs,
météo, énergie, statistiques, frontend), chaque anomalie reproduite avant
correction et vérifiée après.

### Corrigé — Sécurité

- **Garde d'administration inopérante sur le réseau local.** `_loopback()`
  traitait toute adresse privée RFC1918/CGNAT comme « locale », donc n'importe
  quelle machine du réseau atteignait `/api/config` (écriture),
  `/api/control/stop`, `/api/config/restore` et `/api/pronote/*` sans
  identifiant, `allow_remote_admin` n'avait aucun effet. Le vrai loopback
  (127.0.0.0/8, `::1`) et la plage Tailscale CGNAT (100.64.0.0/10) seuls sont
  considérés locaux ; les adresses LAN privées exigent `allow_remote_admin`
  (`api.md` est mis à jour en conséquence).

### Corrigé — Pertes de données et corruption

- **`config.json` écrit de façon non atomique.** Une écriture interrompue
  laissait un JSON tronqué et le démarrage suivant s'arrêtait sur
  « config.json illisible ». L'écriture passe désormais par un fichier
  temporaire unique puis `os.replace` (atomique) avec `fsync` ; un
  `config.json` illisible est renommé `config.json.broken-<horodatage>` et le
  serveur démarre sur les valeurs par défaut au lieu de s'arrêter.
- **Effacement de secrets non désarmé.** Après un enregistrement, seul
  `med_token_clear` était décoché ; les cinq autres cases « Effacer … »
  restaient armées et `collect()` les renvoyait à chaque enregistrement
  automatique suivant — avec le champ secret laissé vide (comportement
  documenté « garder le secret enregistré »), ce qui effaçait le token, le mot
  de passe ou la clé. Toutes les cases `*_clear` sont maintenant désarmées
  après un enregistrement réussi ; l'effacement ponctuel fonctionne toujours.
- **`import_data` fusionnait au lieu de remplacer.** Les appareils absents de
  l'archive importée survivaient, les plannings anciens restaient en place et
  une charge sans `temp` levait une `KeyError` non attrapée (HTTP 500).
  L'import vide maintenant appareils, forçages, créneaux, alternances et
  liaisons avant d'appliquer la sauvegarde, et lève `ValueError` (400 lisible)
  pour une pièce ou un créneau invalide.
- **`update_device` échangeait silencieusement `temp_min`/`temp_max`** — et la
  borne rejetée restait appliquée. La plage est validée *avant* affectation et
  une plage incohérente est refusée avec un message explicite.

### Corrigé — Robustesse réseau et moteur

- **Erreurs de transport non enveloppées.** `_http()` et `_request()`
  n'attrapaient que `HTTPError` et `URLError` : un délai dépassé à la lecture
  du corps (`TimeoutError`) ou une déconnexion en cours de réponse
  (`RemoteDisconnected`) s'échappaient en exception brute. Le moteur
  n'appliquait plus son repli documenté (60 s / 30 s) et
  `_apply()` interrompait les écritures restantes d'une séquence. Ces erreurs
  sont maintenant converties en `AtlanticError` / `WiserError`.
- **`next_change()` ignorait les créneaux délégués.** Une climatisation qui
  applique le planning d'une pièce dont l'actionnaire Wiser ne sait pas
  changer de mode renvoyait `None` : l'interface affichait un état commandé
  sans prochain changement et le préchauffage ne se déclenchait jamais. Le
  calcul partage maintenant `_control_slots()` avec `desired_for()`.
- **Disponibilité Wiser par appareil.** Tout appareil Wiser était marqué
  disponible dès que le Hub répondait, alors que le chemin Atlantic utilise la
  vue par appareil. Un appareil n'est disponible que si sa pièce a été vue à la
  dernière lecture.
- **Avertissement répété.** Le message « Mode … non pris en charge » était
  réécrit à chaque lecture (`tick()`, `_record_samples()`, `snapshot()`), ce
  qui noyait le journal d'événements sous 800 entrées identiques. Il n'est
  plus émis qu'au premier changement par appareil.
- **429 pendant l'authentification.** Un refus trop de requêtes à la connexion
  perdait son `RateLimited` (et son `retry_after`), donc le repli du moteur.
  `RateLimited` est maintenant levé avec le délai fourni.
- **Fuite mémoire du suivi de disponibilité.** `_missing` conservait les
  compteurs des appareils supprimés par l'utilisateur ; ils sont purgés à
  chaque lecture.
- **`_outside_temp_at` renvoyait la prévision du jour** pour une date passée
  (valeur du mauvais jour) ; il renvoie désormais `None`.

### Corrigé — Météo et courbe de chauffe

- **Trou de 25 jours dans la météo.** Le seuil de bascule vers l'endpoint
  Météo-France était de 88 jours alors que l'horizon réel d'AROME est d'environ
  63 jours (vérifié le 26/11/2026 : 93 horodatages renvoyés dont 28 jours
  entièrement vides). Toute plage située entre 63 et 88 jours en arrière
  perdait ses données extérieures — statistiques, courbe d'énergie et
  apprentissage de la courbe de chauffe (120 jours). Le seuil passe à 55 jours
  et les jours de tête vides sont redemandés à l'endpoint archive au lieu
  d'être abandonnés.
- **Modèle de préchauffage incohérent.** `advance_minutes()` calculait
  `τ·60·ln((consigne − ambiance)/eps)` alors que `fit_tau()` calibre
  `τ = τ₀ + τ₁·t_ext`, une équation différente qui ignorait la température
  extérieure. Le préchauffage démarrait jusqu'à ~3× trop tôt et la table de
  courbe de chauffe affichée était plate (13 lignes identiques à 120 min).
  `advance_minutes()` utilise maintenant le modèle calibré
  `τ·60·ln((consigne − eps − t_ext)/(ambiance − t_ext))` quand la température
  extérieure est connue, et conserve l'ancienne branche sans météo.

### Corrigé — Interface et statistiques

- **Fuites d'écouteurs.** `updateChrome()` posait de nouveaux écouteurs
  `click`/`keydown` sur `#statusDot` à chaque appel — une paire par seconde
  (~28 000 par jour) et chaque clic les déclenchait toutes. Ils sont posés une
  seule fois.
- **Titre d'onglet en retard.** `document.title` était assigné après
  `await mount()` : l'onglet gardait le titre de la page précédente pendant
  toute la durée du montage des pages lentes (réglages, santé). Il est défini
  avant l'attente.
- **« Aucun Hub détecté » jamais affiché.** Une erreur de précédence
  (`a + b ? x : y`) faisait que la concaténation toujours vraie choisissait
  toujours le second message ; l'état vide de la recherche Wiser affichait
  l'astuce de récupération de clé à la place du message d'erreur.
- **Modifications non enregistrées perdues.** Changer l'appareil actif ou
  lier une pièce à un modèle reconstruisait tous les plannings sans la
  confirmation déjà présente au changement de semaine ; les éditions en cours
  étaient effacées silencieusement.
- **Granularité des statistiques.** La vue jour affiche 96 tranches de 15 min,
  la vue semaine 168 tranches horaires et la vue mois 24 × jours du mois. Le
  stockage agrégeait déjà par tranche de 15 minutes ; les statistiques ne
  l'exposaient pas.
- **Ordonnancement du journal d'historique.** `flush_history()` émettait les
  lignes JSONL dans l'ordre non déterministe d'un `set` ; elles sont
  désormais triées `(jour, tranche, appareil)`.
- **Graphique des températures.** Les trois séries (ambiance, consigne,
  extérieur) partageaient deux axes Y ; elles ont maintenant un axe unique
  basé sur le min/max de toutes les séries. Les points isolés sont rendus en
  courts segments horizontaux plutôt qu'en pastilles, pour éviter un
  graphique en pointillés en vue mois.
- **`temperature_series`** comparait la couverture à 31 × 24 heures même pour
  un mois court ou une fenêtre tronquée ; le seuil se base sur les heures
  réellement présentes.
- **`build_stats`** signalait silencieusement l'absence de consigne lorsque
  l'actionnaire enregistré n'était pas un appareil de la pièce ; un
  avertissement est maintenant écrit.

### Corrigé — Validation des entrées (HTTP 500 → 400)

- `set_slots()` levait `TypeError`/`AttributeError` (non attrapés par les
  routes, donc 500) sur une `temp` explicitement nulle, un jour qui n'est pas
  une liste ou un créneau qui n'est pas un dictionnaire. Tous les cas lèvent
  désormais `ValueError` ; une température absente ou nulle retombe sur la
  valeur par défaut de 20 °C.
- `set_slots()` acceptait un `device_id`/`room_id` inexistant et créait des
  plannings fantômes.
- `/api/override` bornait mal `minutes` : une valeur énorme provoquait un
  `OverflowError` non attrapé (500) et une grande valeur créait un forçage
  quasi permanent. La durée est bornée à 1–1440 minutes.
- `consoapi_url`, `consoapi_pdl` et `consoapi_start` étaient enregistrés sans
  passer par `validate_consoapi_values()`, contrairement aux champs `med_*`.
- `pronote_wake_minutes` / `pronote_bedtime_minutes` : l'enregistrement
  acceptait 24:00 que la lecture ramenait ensuite à 23:59 (décalage d'une
  minute sans retour). Les deux bornes sont alignées à 23:59.

### Corrigé — Énergie

- `daily()` avec `refresh` ne demandait que les 7 derniers jours : un cache
  disque partiel n'était jamais complété sur ce chemin. La fenêtre demandée
  couvre maintenant les jours manquants **et** la fenêtre de revalidation.
- `_merge_days()` n'écrasait jamais un jour déjà présent en cache disque : une
  valeur révisée par Enedis restait éternellement périmée. Les jours de la
  fenêtre de revalidation sont désormais réécrits.
- L'unité d'une grandeur (`Wh` / `kWh`) est lue explicitement au lieu d'être
  supposée en Wh.
- Suppression de `_year_chunks()` (code mort).

### Corrigé — Fiabilité du stockage

- **Course d'écriture sur `state.json.tmp`.** Le drapeau `_structural_dirty`
  était remis à zéro sous verrou *avant* l'écriture sur disque hors verrou :
  deux écritures concurrentes (fil de fond toutes les 2 s + `flush(force=True)`
  des routes) pouvaient entrelacer deux `json.dump` sur le même chemin et
  laisser un état tronqué ou mélangé, que le démarrage suivant rejetait.
  Un verrou dédié couvre maintenant toute la séquence
  « construction → écriture → `os.replace` », avec repli du drapeau en cas
  d'échec.
- `delete_room()` laissait les créneaux de la pièce supprimée (données
  orphelines réécrites à chaque instantané).

### Tests

Suite complète : **523 tests automatisés** (+ tests JS et vérification de
syntaxe de tous les modules), dont 9 nouveaux tests de régression couvrant la
délégation de `next_change`, le franchissement de semaine, la déduplication de
l'avertissement, la disponibilité Wiser par appareil et le comportement de
`_outside_temp_at` sur dates passées / journée courante.

### Commits

| Référence | Objet |
| --- | --- |
| `7bbaab7` | Sondage ≥ 5 min (`MIN_POLL_SECONDS` 30 → 300) et axe Y unique pour les températures |
| `91d6b72` | Granularité des statistiques : 15 min / heure / 24 × jours |
| `a41db6b` | Points isolés rendus en courts segments (courbe continue en vue mois) |
| `2452ca1` | Import `config_backup` indépendant du répertoire courant + test de granularité |
| `17cfcd6` | Stockage : format d'historique 15 min confirmé et documenté |
| `1b726ba` | Garde LAN, validation ConsoAPI, écriture atomique, durcissement du stockage |
| `fb0f19e` | Enveloppe des erreurs de transport, capacités du mock, identifiants Wiser, annuaire Pronote |
| `7fa2df8` | Correctifs frontend (désarmement des effacements, fuite d'écouteurs, titre, astuce Hub, éditions non enregistrées) |
| `4863a16` | Horizon météo Météo-France + modèle de préchauffage calibré |
| `b4e9762` | Moteur : `next_change` et délégation, avertissements uniques, repli disque de l'énergie, unité |