# Calendrier scolaire, vacances et préchauffage

Trois réglages complémentaires, tous configurables depuis **Réglages**.

## Calendrier scolaire

Réglages → **Calendrier scolaire**.

| Réglage | Valeurs | Effet |
| --- | --- | --- |
| Zone scolaire | Désactivé, A, B, C | Zone utilisée pour télécharger les vacances officielles. |
| Suivre les vacances et jours fériés | oui / non | Active l'application du calendrier à l'import Pronote. |
| Planning des vacances | Confort, Réduit, Hors gel, Ne pas modifier | Remplace le planning de l'école les jours de fermeture. |
| Semaines avec l'enfant | Toutes, A, B, Aucune | Alternance : seules les semaines où l'enfant est là sont ajustées. |

Les vacances viennent de l'API officielle `data.education.gouv.fr`
(dataset `fr-en-calendrier-scolaire`) et sont mises en cache dans
`data/school_calendar.json` pendant 30 jours. Les jours fériés sont calculés
localement (algorithme de Pâques), sans appel réseau. En cas d'échec réseau,
le cache existant reste utilisé et aucune nouvelle tentative n'est faite
pendant une heure.

Le bouton **Actualiser le calendrier** force un téléchargement immédiat.

### Alternance

Le planning Pronote n'est modifié que pour les semaines déclarées « avec
l'enfant ». Cela évite de chauffer comme si l'enfant était à la maison une
semaine où il est chez l'autre parent : ces semaines conservent exactement
leur planning existant.

### Import Pronote

Pendant une fermeture, chaque jour concerné reçoit un planning de journée à la
maison (lever → confort, coucher → réduit) selon le mode choisi. Le mode
**Ne pas modifier** conserve le planning déjà en place. Les jours fériés
isolés (pont de l'Ascension, par exemple) sont traités comme des journées
seules.

## Mode vacances / absence

Depuis l'accueil : bouton **Vacances** → choisir une date de début et une date
de fin (incluse). La maison passe hors gel pendant toute la fenêtre, puis
revient automatiquement au planning normal le lendemain de la fin.

- Une fenêtre programmée ne change pas `presence_mode` : elle s'y ajoute.
- Une absence manuelle reste active même après la fin d'une fenêtre.
- **Présent** ou **Annuler les vacances** efface la fenêtre et repasse la
  maison en présence normale.

L'état effectif est exposé dans le snapshot (`presence`) et affiché sur
l'accueil : `reason` vaut `vacation`, `manual` ou `present`.

## Préchauffage intelligent

Réglages → **Pièces** → *Préchauffage intelligent*, et **Moteur de planning**
pour le plafond global.

- Anticipe la prochaine montée de consigne en calculant l'avance nécessaire.
- Utilise la **courbe de chauffe** (modèle appris) si elle est activée, sinon
  un modèle par défaut (τ = 3 h).
- Température extérieure prévue : météo horaire pour aujourd'hui, puis
  interpolation des prévisions journalières (min vers 5 h, max vers 15 h).
- **Avance maximale du préchauffage** : 0 à 360 minutes (120 par défaut). `0`
  désactive complètement l'anticipation.

Le préchauffage ne s'applique jamais pendant une absence : l'absence domine
le planning.
