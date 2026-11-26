# Résumé des corrections de bugs dans `ecasa/main.py`

## BUG 1 — `_loopback()` : garde admin inefficace sur LAN
**Fichier** : `ecasa/main.py` lignes ~3715-3740

**Problème** : Toute IP privée RFC1918/CGNAT (192.168.x.x, 10.x.x.x, 172.16-31.x.x) était considérée comme "locale", rendant le paramètre `allow_remote_admin` inopérant sur un réseau local. N'importe quelle machine du LAN pouvait accéder aux routes sensibles (`/api/config` PUT, `/api/control/stop`, `/api/config/restore`, `/api/pronote/*`) sans authentification.

**Correction** : `_loopback()` ne retourne `True` que pour :
- Vrai loopback IPv4 (127.0.0.0/8) et IPv6 (::1)
- Plage Tailscale CGNAT (100.64.0.0/10)

Les adresses LAN privées ne sont **plus** traitées comme locales par défaut. Le contrôle existant dans `_api()` (ligne ~3795) applique correctement le flag `allow_remote_admin` pour ces adresses.

**Test** : Vérifié que 127.0.0.1, ::1, 100.64.x.x → True ; 192.168.x.x, 10.x.x.x, 172.16.x.x → False.

---

## BUG 2 — Validation ConsoAPI bypassée dans `_save_config_locked`
**Fichier** : `ecasa/main.py` lignes ~954-968

**Problème** : Les champs `consoapi_url`, `consoapi_pdl`, `consoapi_start` étaient écrits bruts sans validation, alors que la fonction `validate_consoapi_values()` existe et est utilisée par `/api/energy/test`. Incohérence avec le traitement des champs `med_*` (ligne 946 : `updated.update(validate_med_values(body))`).

**Correction** : Ajout de `updated.update(validate_consoapi_values(body))` après le traitement de `consoapi_enabled`, même pattern que pour `med_*`. Suppression de la validation manuelle redondante.

**Test** : Vérifié que URLs invalides (ftp://), PDL invalides (< 14 chiffres), dates futures, dates trop anciennes (> 3 ans) sont rejetées avec messages français appropriés.

---

## BUG 3 — `/api/override` minutes non bornées
**Fichier** : `ecasa/main.py` lignes ~5197-5224

**Problème** : Seul `max(1, minutes)` était appliqué. Valeurs énormes (ex. 10¹²) causaient `OverflowError` dans `timedelta()` non attrapé (seuls KeyError/TypeError/ValueError l'étaient) → HTTP 500. Grandes valeurs valides créaient un forçage quasi permanent.

**Correction** :
- Bornage strict à 1..1440 minutes (24h max) avec erreur 400 : "Durée de forçage invalide (1 à 1440 minutes)"
- Garde `try/except OverflowError` autour de la construction du `timedelta`

**Test** : 0, 1441, 10¹² → erreur 400 ; 1, 60, 1440 → OK.

---

## BUG 4 — Écriture non-atomique de `config.json`
**Fichier** : `ecasa/main.py` lignes ~559-566 (`_write_config_locked`) et ~227-256 (`load_config`)

**Problème** : `CONFIG_PATH.write_text()` direct : crash en cours d'écriture laisse JSON tronqué. Au redémarrage suivant, `SystemExit('config.json illisible')` sans fallback, serveur ne démarre pas.

**Correction** :
1. **Écriture atomique** : fichier temporaire unique (`config.json.tmp-<pid>-<timestamp>`) + `os.replace()` (atomique sur Windows via `MoveFileEx`) + `fsync()` avant replace.
2. **Résilience lecture** : dans `load_config()`, si JSON illisible → renomme le fichier corrompu en `config.json.broken-<YYYYMMDD-HHMMSS>` et repart sur `DEFAULT_CONFIG` (erreur loggée). Le serveur démarre toujours.

**Test** : Écriture atomique vérifiée ; config corrompue → renommée + defaults chargés ; config manquante → defaults créés.

---

## BUG 5 — Incohérence bornes `pronote_wake_minutes` / `pronote_bedtime_minutes`
**Fichier** : `ecasa/main.py` lignes ~754-768, ~846-860, et `_minutes_of_day` ~280-292

**Problème** : `_save_config_locked` acceptait 0 ≤ minutes ≤ 24×60 (1440 inclus) mais `_minutes_of_day` et `public_config` clampent à 24×60-1 (1439). Sauvegarde de 1440 (24:00) → lecture retourne 1439 (23:59), décalage silencieux d'une minute.

**Correction** : `_save_config_locked` rejette maintenant les valeurs > 1439 avec erreur 400 : "Heure de lever/coucher invalide (0 à 23:59)". Aligne la validation à la lecture.

**Test** : 1439 et "23:59" → acceptés ; 1440 et "24:00" → rejetés ; `_minutes_of_day(1440)` → 1439 (clamp existant préservé).

---

## Vérification des tests

```bash
python -m pytest tests/test_web_api.py -q
# 11 passed
```

Tous les tests existants de `test_web_api.py` passent. Un test préexistant flaky dans `test_storage.py::HistoryTests::test_history_file_written_and_reloaded` échoue (ordre non-déterministe d'un `set` dans `flush_history`), sans lien avec ces corrections.