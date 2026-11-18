# Pronote

eCasa importe le planning Pronote de l'enfant et le transforme en consignes de
chauffage : confort quand l'enfant est là, réduit quand il est en cours ou absent.

## Mise en place

1. **Réglages → Pronote → Serveur Pronote** : rechercher l'établissement (nom, ville
   ou code postal) puis renseigner l'identifiant, le mot de passe et, si besoin,
   l'identifiant ENT.
2. Choisir le **type de compte** (`Élève` ou `Parent`) et le prénom de l'**enfant**
   si le compte en gère plusieurs.
3. Renseigner le **statut scolaire** : `Externe` (rentre déjeuner) ou
   `Demi-pensionnaire` (reste au lycée).
4. Pour un demi-pensionnaire, activer si besoin **Retour direct** (voir
   « Journée qui se termine le matin »).
5. Régler le **trajet** domicile ↔ établissement, l'**heure de lever** et l'**heure
   de coucher**.
6. Choisir les **pièces liées** et les **semaines** (A, B ou les deux).
7. Choisir le **jour d'alternance** (voir ci-dessous).
8. Cliquer sur **Tester la connexion**, puis **Enregistrer**.

L'import nécessite le paquet optionnel `pronotepy` :

```bash
.venv/bin/pip install -r requirements.txt
```

## Ce que eCasa génère

Pour chaque journée avec cours :

- **nuit** en réduit de l'heure de coucher à l'heure de lever ;
- **confort** à partir du lever ;
- **réduit** pendant les cours, en ajoutant le temps de trajet avant et après ;
- **confort** à la maison entre deux blocs (élève externe) ;
- **réduit** de nouveau au coucher.

Cas particuliers :

- **Demi-pensionnaire** : la journée scolaire est continue (pas de confort le midi).
  Si les cours s'arrêtent avant la fin de la pause déjeuner, l'absence est prolongée
  jusqu'à 13:30 (l'élève mange à la cantine avant de rentrer), sauf avec l'option
  **Retour direct** (voir ci-dessous).
- **Journée sans cours** : rien n'est écrit, le planning existant est conservé tel
  quel et reste modifiable.
- **Cours annulés** : Pronote les signale, eCasa les ignore.

## Réglages par pièce (confort / réduit)

Les réglages globaux s'appliquent à toutes les pièces liées. Une pièce peut avoir
les siens, dans **Réglages → Pronote → Réglages par pièce** : mode et consigne de
confort, mode et consigne de réduit. Une valeur vide hérite du réglage global.

Exemple typique : la pièce équipée d'une **climatisation** en `Froid` (24 °C en
confort, 26 °C en réduit) et les chambres Wiser en `Chaud`.

À l'import, chaque pièce reçoit son propre planning ; l'aperçu de la page Planning
reste calculé avec les réglages globaux (il est indicatif).

## Garde alternée

Le **jour d'alternance** indique le jour où l'enfant change de parent :

| Choix | Sens | Ce que l'import écrit |
| --- | --- | --- |
| **Lundi** | L'enfant **arrive** après l'école | Seule la **fin** de journée est gérée : à partir du retour pour le repas (externe) ou de la fin des cours (demi-pensionnaire). Le matin reste à toi. |
| **Vendredi** | L'enfant **part** après l'école | Seule la **matinée** est gérée, jusqu'au passage en réduit du départ. Le reste du vendredi reste à toi. |

Les autres jours sont importés normalement. Les créneaux non gérés par Pronote
restent **modifiables à la main** dans le Planning : ils ne sont jamais verrouillés.

### Journée qui se termine le matin

Un jour dont le dernier cours se termine avant **13:30** (mercredi type) est
particulier : eCasa n'écrit par défaut que le **début** de la journée (nuit, lever,
cours, retour à la maison) et laisse l'après-midi à ton planning. La portée de
l'import est alors partielle (`prefix`) : seule la première partie du jour est
remplacée.

L'option `pronote_direct_return` (**Réglages → Pronote → Retour direct**, désactivée
par défaut) change ce comportement pour un **demi-pensionnaire** :

- **désactivée** : l'absence est prolongée jusqu'à **13:30 + trajet** (l'élève mange
  à la cantine avant de rentrer), puis la pièce repasse en confort ; l'après-midi
  reste ton planning ;
- **activée** : pas de prolongation repas/cantine, l'enfant rentre après le
  **dernier cours + trajet**, la pièce repasse en confort **jusqu'au coucher** et
  toute la journée est gérée par Pronote (portée `full`).

Pour un **externe**, l'option ne change rien : il rentre déjà déjeuner, il n'y a pas
de pause cantine à prolonger. Le **vendredi** et le **lundi** de garde alternée
gardent leur logique propre, quelle que soit l'option.

## Mise à jour automatique

Dans **Réglages → Pronote** :

- **Mise à jour automatique** : activée par défaut.
- **Heure de la mise à jour** : une heure pleine (03:00 par défaut).

Chaque jour, eCasa tente **une seule fois** l'import à l'heure choisie. Un échec est
journalisé et attend le lendemain, ou un import manuel. La consommation électrique,
elle, reste synchronisée à 3 h.

Désactiver la mise à jour automatique laisse l'import **manuel** disponible depuis le
Planning, sans aucune écriture automatique.

## Import manuel (page Planning)

1. Ouvrir **Planning** → bouton d'import Pronote.
2. Vérifier les semaines et le jour d'alternance (modifiables ici).
3. Choisir la **semaine à analyser** puis **Charger l'aperçu** : rien n'est écrit.
4. Cliquer sur **Importer** : eCasa enregistre les réglages s'ils ont changé (une
   seule fois), puis écrit le planning dans les pièces liées.

L'aperçu affiche les créneaux générés jour par jour. Les jours sans cours sont
indiqués « aucun cours » et restent inchangés.

## Semaines A/B

- **Semaines liées** : seules ces semaines sont gérées et resynchronisées
  automatiquement. Les autres restent libres.
- **Mode d'alternance** (**Réglages → Semaines A/B**) : parité ISO ou aucune
  alternance. En mode « Pas d'alternance », tout est traité comme la semaine A.

## Dépannage

| Symptôme | Cause probable | Solution |
| --- | --- | --- |
| « pronotepy introuvable » | Paquet non installé | `pip install -r requirements.txt` |
| « Identifiants refusés » | Compte élève/parent ou ENT | Vérifier le type de compte et l'identifiant ENT. |
| « Aucune semaine B trouvée » | Mode d'alternance non configuré | Réglages → Semaines A/B → parité ISO. |
| Rien n'est importé | Aucune pièce liée | Cocher au moins une pièce dans Réglages → Pronote. |
| Le planning change trop | Jour d'alternance inversé | Vérifier lundi/vendredi dans Réglages → Pronote. |
| L'import échoue chaque nuit | Pronote indisponible ou mot de passe changé | Relancer l'import manuel et lire le Journal. |

Les messages détaillés sont dans la page **Journal** (filtre `avertissement` pour les
confirmations non reçues).
