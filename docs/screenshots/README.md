# Captures d'écran

Images utilisées par le README, prises sur une instance de **simulation**
(eCasa lancé sans identifiants Atlantic) : les pièces, l'historique et la
consommation sont fictifs, aucune donnée personnelle n'apparaît.

| Fichier | Contenu | Taille |
| --- | --- | --- |
| `home.png` | Accueil : ambiance des pièces, consigne moyenne, prochain changement | 1440 × 900 |
| `home-calendar.png` | Accueil : feuille « Planning · Pièce », calendrier hebdomadaire (blocs fusionnés sans trous) | 1440 × 900 |
| `planning.png` | Planning : agenda 24 h d'une pièce sur la semaine B | 1440 × 900 |
| `stats.png` | Statistiques : KPI et courbes ambiance / consigne / extérieur | 1440 × 900 |
| `stats-consommation.png` | Carte « Consommation électrique » : vues Jour / Semaine / Mois / Année et interrupteur « À date » | 1128 × 934 |
| `settings-pronote.png` | Réglages → Pronote (section complète, dont l'interrupteur « Retour direct ») | 928 × 1492 |
| `mobile-home.png` | Vue mobile (PWA) | 480 × 900 |

## Refaire les captures

Automatisé : `node docs/screenshots/capture.mjs` (Edge/Chrome headless, port 9333).
Le script suppose eCasa lancé en simulation (`python -m ecasa.main`) et écrit
les sept captures dans ce dossier. Les tailles sont celles de l'élément capturé.

> ⚠️ **Sécurité — ne jamais lancer `python -m ecasa.main` depuis un clone qui contient
> un `config.json` réel** (identifiants Atlantic/Pronote, PDL, jeton MyElectricalData…).
> Avant tout lancement, vérifier que `config.json` est absent ou contient `"mock": true`
> et aucun identifiant. La simulation est **obligatoire** pour obtenir des captures
> anonymes : une capture prise sur une instance réelle contient prénom, adresse,
> météo et consommation.

Manuellement (si le script ne convient pas) :

1. Lancer eCasa en simulation : `python -m ecasa.main` (sans identifiants Atlantic).
2. Vérifier que les données affichées ne contiennent rien de personnel.
3. Capturer avec un navigateur (Chrome/Edge) en 1440 × 900, thème clair ou sombre.
4. Recadrer si besoin (les images ci-dessus sont recadrées proprement).

## Bonnes pratiques

- Masquer les noms de pièces, adresses et identifiants.
- Éviter les écrans d'erreur : une capture qui montre une panne décourage.
- Préférer le thème sombre pour les images dans un README sombre.
