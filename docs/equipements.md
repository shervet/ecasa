# Équipements

eCasa pilote deux familles d'appareils, regroupés en **pièces** :

- **Atlantic / Cozytouch** : chauffage et climatisation via le cloud Atlantic.
- **Schneider Wiser** : thermostats via la passerelle locale.

Une pièce associe un thermostat et un actionneur ; le planning s'applique à la pièce
entière. Plusieurs appareils peuvent être associés à la même pièce.

## Atlantic / Cozytouch

### Connexion

Renseigner dans **Réglages → Serveur** (ou `config.json`) l'email et le mot de passe
du compte Cozytouch, puis **Tester la connexion**.

Sans identifiants, eCasa démarre en **simulation** : appareils fictifs, interface
complète, aucune écriture réelle. Pratique pour découvrir l'outil ou développer.

### Modes et consignes

Selon les capacités exposées par l'appareil :

| Mode | Usage |
| --- | --- |
| `off` | Arrêt. |
| `heat` | Chauffage, consigne de confort/réduit. |
| `cool` | Climatisation, consigne de froid. |
| `dry` | Déshumidification. |
| `fan` | Ventilation seule. |
| `auto` | Régulation automatique si l'appareil le permet. |

eCasa applique la consigne du créneau de planning en cours (confort ou réduit) et
respecte les capacités réelles de l'appareil : une consigne hors plage est bornée.

### Climatisation : soufflerie et orientation

Dans **Réglages → Appareils → Clim**, deux défauts sont appliqués à chaque cycle sur
les appareils qui exposent ces capacités :

- **Soufflerie** (`atlantic_fan_default`) : vide (inchangé), `low`, `med`, `high`, `auto`.
- **Orientation du flux** (`atlantic_airflow_default`) : vide, `swing` (balayage) ou
  `pos1` … `pos6` (position fixe).

Une valeur vide laisse le réglage fait sur la télécommande. Aucune écriture n'est faite
quand l'appareil est à l'arrêt, et les deux capacités sont réécrites après un changement
de mode (certaines télécommandes les réinitialisent).

### Forçages temporaires

Depuis l'accueil ou la feuille d'un appareil : choisir un mode et une consigne, puis
une durée. eCasa applique le forçage immédiatement et **reprend le planning
automatiquement** à l'échéance. Un forçage peut être annulé à tout moment.

### Confirmation des ordres

Les ordres Atlantic passent par le cloud : la confirmation peut prendre de quelques
secondes à une minute. Si elle n'arrive pas dans la fenêtre d'attente, eCasa conserve
l'état demandé et journalise un **avertissement** (niveau `avertissement`, pas une
erreur) : l'ordre est généralement appliqué avec du retard.

## Schneider Wiser
### Découverte

1. **Réglages → Appareils → Wiser** : activer la passerelle.
2. Renseigner l'adresse du hub (ou cliquer sur **Chercher sur le réseau**).
3. Saisir la clé locale du hub, puis **Tester**.

### Import des plannings

eCasa lit les plannings Wiser (par pièce et par jour) et peut les importer dans son
propre planning. L'import convertit les points de consigne Wiser en créneaux eCasa et
**remplace le planning de l'appareil pour les semaines choisies** (A, B ou les deux).

## Autres systèmes (Daikin, Bosch, Netatmo, Mitsubishi)

Chaque système a son panneau dépliable dans **Réglages → Appareils** : on saisit
les accès, on enregistre, puis **Synchroniser** liste les appareils détectés avec
leur mode, leur consigne et l'ambiance. Une ligne = un appareil : choisir le mode,
la consigne, puis **Appliquer** (commande envoyée immédiatement).

| Système | Type | État |
| --- | --- | --- |
| **Daikin** | Local (boîtier Wi-Fi, API HTTP) | **Opérationnel** : lecture (mode, consigne, ambiances) et écriture (`/aircon/set_control_info`). |
| **Netatmo** | Cloud (OAuth2, compte développeur) | **Opérationnel** : lecture des pièces et écriture des consignes manuelles. |
| **Mitsubishi** | Cloud (MELCloud) | **Opérationnel** : lecture (ListDevices) et écriture (SetAta). |
| **Bosch** | Local (contrôleur) | **Appairage matériel requis** : bouton sur le contrôleur + certificat client, impossible à automatiser sans l'appareil. |

Boutons disponibles par panneau :

- **Tester** : lit réellement les appareils et affiche le diagnostic ;
- **Synchroniser** : liste les appareils et permet d'envoyer une consigne ;
- **Enregistrer** : sauvegarde les accès (les secrets ne sont jamais renvoyés).

Côté API : `GET /api/connectors/{id}/devices` et `POST /api/connectors/{id}/apply`
(`{"device_id": …, "mode": "heat", "temp": 20.5}`). Les réglages vivent dans
`config.json` sous `connectors` ; `GET /api/config` les expose sans secrets
(booléens `*_set`).

> L'intégration au planning automatique (semaines A/B, Pronote) reste à brancher :
> aujourd'hui un connecteur se pilote depuis son panneau ou par l'API.

## Planning

Le planning est un agenda 24 h par pièce, sur deux semaines (**A** et **B**) :

- créer, déplacer, supprimer un créneau ;
- dupliquer un jour ou une semaine ;
- copier la semaine A vers B (et inversement) ;
- enregistrer des **modèles** de semaine réutilisables ;
- appliquer des **surcharges** ponctuelles sans modifier le planning de base.

Un créneau définit une heure de début, un mode et une consigne. Le créneau s'applique
jusqu'au suivant.

## Semaines A/B

Deux modes :

- **Parité ISO** : A et B alternent une semaine sur deux.
- **Pas d'alternance** : toutes les semaines sont traitées comme la semaine A.

L'option **Inverser A et B** décale la parité sans changer les plannings.

Le **jour d'alternance** (lundi ou vendredi) est réglé dans la section Pronote : il
détermine quand la semaine bascule et quelle portion du vendredi/lundi appartient à
Pronote.
