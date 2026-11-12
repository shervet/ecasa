# Contribuer à MeCasa

Merci ! Les contributions sont bienvenues : corrections de bugs, documentation,
traductions, tests sur matériel réel, nouvelles ressources.

## Avant de commencer

- Chercher dans les issues si le problème est déjà connu.
- Pour une idée ou une question : ouvrir une **discussion** plutôt qu'une issue.
- Pour un bug : remplir le modèle d'issue (version, plateforme, étapes, journal
  **anonymisé** — aucun mot de passe, aucun identifiant).

## Ce projet est écrit avec l'aide d'une IA

Le code, les tests et la documentation de MeCasa sont produits avec l'aide d'une
intelligence artificielle, sous la supervision d'un humain. Les contributions sont
relues et testées comme n'importe quelle autre : **lisibilité, tests et simplicité
d'usage** sont les trois critères de validation.

## Proposer une évolution

Les idées les plus attendues : d'autres systèmes et appareils (Daikin, Bosch,
Netatmo, Zigbee, Z-Wave…), d'autres intégrations (MQTT, Home Assistant via son API
REST), l'export iCal/ICS du planning, l'application Android d'accès direct, ou
encore plus de simplicité (assistant de configuration, modèles prêts à l'emploi).

Ouvre une discussion : la proposition est étudiée avant d'être codée.

## Environnement

```bash
git clone https://github.com/shervet/mecasa.git
cd mecasa
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m mecasa.main
```

Sans identifiants Atlantic, MeCasa démarre en simulation : pas besoin de matériel
pour développer l'interface.

## Tests

Tous les tests doivent passer avant toute pull request :

```bash
cd tests
PYTHONPATH=.. python -m unittest discover -s . -p "test_*.py"
# ou, depuis la racine :
python -m unittest discover -s tests -p "test_*.py"
```

Ajouter un test dans `tests/test_*.py` pour tout correctif de bug : c'est la
meilleure façon de montrer qu'il est corrigé.

## Conventions

- **Backend** : Python, bibliothèque standard uniquement. `pronotepy` reste optionnel.
  Docstrings et commentaires en français, code et noms de variables en anglais.
  Une fonction = une responsabilité ; les helpers privés sont préfixés `_`.
- **Frontend** : HTML/CSS/JS natifs, modules ES, **sans build ni npm**, sans CDN.
  Pas de script inline (CSP stricte). Styles dans `web/css/style.css`.
- **Interface** : en français, mobile-first, avec le même ton partout.
- **Aucune donnée personnelle ni secret** dans le dépôt : `config.json` et `data/`
  sont ignorés par git.

## Processus

1. Créer une branche : `git checkout -b ma-fonction`.
2. Commits petits et explicites : `Pronote : import partiel le lundi`.
3. Lancer les tests.
4. Ouvrir une pull request en décrivant le **comportement** avant/après.

## Bonnes idées de contributions

- Support d'autres équipements Atlantic (ventilation, PAC).
- Export du planning en ICS (agenda).
- Amélioration du moteur de zones (pièces voisines, portes ouvertes).
- Traductions de l'interface.
- Documentation et captures d'écran.

## Licence

En contribuant, tu acceptes que ta contribution soit publiée sous la licence
[MIT](LICENSE) du projet.
