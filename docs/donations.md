# Dons et financement

eCasa est gratuit, sans publicité, sans compte en ligne et sans télémétrie. Si l'outil
te rend service, un don ponctuel ou récurrent finance le matériel de test (Atlantic,
Wiser, Pi) et le temps de développement.

## Où apparaît le bouton de don

| Emplacement | Fichier | À faire |
| --- | --- | --- |
| Interface → Réglages → Soutenir | `web/js/site.js` | Renseigner `donateUrl`. |
| GitHub → bouton Sponsor | `.github/FUNDING.yml` | Renseigner la plateforme choisie. |
| README → section Soutenir | `README.md` | Lien vers la plateforme. |

`donateUrl` vide = le bouton « Faire un don » est masqué (le bloc reste visible avec
les liens projet). Aucun code n'est à modifier ailleurs.

## Quel plateforme choisir (frais réels)

| Plateforme | Frais de plateforme | Frais de paiement | Part encaissée | Dons récurrents | Reçu fiscal |
| --- | --- | --- | --- | --- | --- |
| **GitHub Sponsors** | **0 %** (dons de comptes personnels) | Pris en charge par GitHub (virement bancaire possible) | **≈ 100 %** | Oui | Non (particulier) |
| **Liberapay** | **0 %** | Stripe/PayPal (≈ 1,4 % + 0,25 € en UE ; 2,9 % + 0,30 $ hors UE) | ≈ 98 % | Oui | Non |
| **Stripe (Payment Link)** | **0 %** | Stripe uniquement (≈ 1,5 % + 0,25 € pour une carte UE) | ≈ 98 % | Non (option possible) | Non |
| **HelloAsso** | **0 %** pour l'association | Pris en charge (le donateur peut ajouter un pourboire) | ≈ 100 % | Non | **Oui** (association) |
| Ko-fi | 0 % sur les dons | PayPal/Stripe (≈ 5 % au total) | ≈ 95 % | Oui | Non |
| Moneroo | 0 % | ≈ 3–5 % | ≈ 95 % | Non | Non |
| PayPal.me | ≈ 2–3 % | Frais PayPal | ≈ 95 % | Non | Non |
| Open Collective | 5–10 % | ≈ 3 % | ≈ 87 % | Oui | Selon l'hôte fiscal |
| Buy Me a Coffee / Tipeee | 5 % / 8 % | Frais de paiement | ≈ 92 % / 90 % | Oui | Non |

### Recommandation

1. **GitHub Sponsors en plateforme principale** : c'est la seule option qui combine
   **0 % de frais de plateforme**, un badge natif sur le dépôt, des dons récurrents et
   un virement bancaire. Les dons de comptes personnels sont **sans frais pour
   l'auteur** (GitHub ne prélève rien et prend en charge le traitement).
2. **Liberapay en complément** : 0 % de plateforme, très bonne intégration GitHub,
   pratique pour les dons récurrents en euros. Seuls les frais Stripe/PayPal
   s'appliquent.
3. **HelloAsso** uniquement si tu crées une association loi 1901 : c'est la seule
   option qui donne un **reçu fiscal** aux donateurs (et donc un taux de réduction
   d'impôt). L'association est alors le bénéficiaire des dons.
4. **Stripe en paiement direct** : le plus simple si tu as déjà un compte Stripe
   (auto-entrepreneur, association). Un Payment Link, aucun site à maintenir, 0 % de
   frais de plateforme.

> ⚠️ **Fiscalité** : un don à un particulier est un revenu imposable (BNC) et le
> donateur n'obtient **aucun reçu fiscal**. Si tu veux que les donateurs puissent
> déduire leurs dons, passe par une association (HelloAsso) et distribue les dons via
> son compte bancaire.

## Mise en place pas à pas (GitHub Sponsors)

1. **Compte GitHub** : activer la double authentification (obligatoire pour recevoir).
2. **Profil → Sponsors** : commander GitHub Sponsors (carte bancaire demandée pour le
   compte GitHub, sans frais de la part de GitHub).
3. **Identité et coordonnées bancaires** : GitHub utilise **Stripe Connect**.
   - Renseigner l'identité (nom, date de naissance, adresse).
   - Renseigner un IBAN (virement) — c'est la méthode la moins chère.
   - Compléter les informations fiscales du pays (France).
   - ⚠️ Ne jamais créer un compte Stripe « entreprise » avec de fausses informations :
     la vérification peut être demandée à tout moment.
4. **Délai** : l'activation demande souvent quelques jours (vérification d'identité et
   de compte bancaire).
5. **Brancher le dépôt** : créer `.github/FUNDING.yml` :

   ```yaml
   github: shervet
   # Alternatives (une seule ligne active) :
   # liberapay: shervet
   # custom: ["https://donate.stripe.com/xxxxxx"]
   ```

6. **Recharger l'application** : `web/js/site.js` → `donateUrl` :
   ```js
   donateUrl: "https://github.com/sponsors/shervet",
   ```
   Le bouton « Faire un don » apparaît alors dans **Réglages → Soutenir**.

## Mise en place pas à pas (Liberapay)

1. Créer un compte sur [liberapay.com](https://liberapay.com) (IBAN requis).
2. Créer le compte du projet et choisir la devise EUR.
3. Copier l'URL de don (`https://liberapay.com/<utilisateur>/donate`).
4. La reporter dans `donateUrl` et dans `.github/FUNDING.yml` (`liberapay:`).

## Mise en place pas à pas (Stripe)

1. Créer un compte Stripe (France : auto-entrepreneur, association ou société).
2. Créer un **Payment Link** (montant libre ou fixe, paiement en une fois).
3. Reporter l'URL dans `donateUrl`.
4. Les frais sont ceux de Stripe (≈ 1,5 % + 0,25 € pour une carte UE) : aucun
   pourcentage prélevé par eCasa.

## Transparence

- Annonce ce que les dons financent (matériel, impression 3D, nom de domaine, hébergement
  du dépôt, app Android).
- Publie les comptes si tu les connais.
- N'impose jamais un don : toutes les fonctions de eCasa restent gratuites.
- Un don ne donne aucun accès privilégié à une fonctionnalité.

## Fiscalité et obligations (France)

- **Particulier** : les dons sont imposables ; il n'y a pas de reçu fiscal.
- **Auto-entrepreneur / société** : declare les recettes selon ton régime ; facture si
  le donateur le demande (à partir d'un certain seuil, une facture est obligatoire).
- **Association loi 1901** : les dons sont exonérés dans la limite du objet associatif ;
  les donateurs obtiennent un reçu fiscal via HelloAsso (ou la plateforme de ton choix).
- Garde une trace des dons (date, montant, plateforme) : cela simplifie les questions
  du fisc comme du comptable.

## Reçu et remerciement

eCasa n'émet pas de reçu. Le reçu est généré par la plateforme (HelloAsso pour une
association, sinon aucun reçu n'est fourni). Pour Thank You Pages (Ko-fi,
Buy Me a Coffee), l'emoji de remerciement peut être personnalisé pour rappeler le
projet.
