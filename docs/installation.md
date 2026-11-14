# Installation

eCasa est une application Python sans dépendance obligatoire. Trois étapes :
installer Python, récupérer le code, lancer le serveur.

> **À propos** : eCasa est un projet open source **développé avec l'aide d'une
> intelligence artificielle**, sous supervision humaine. Le code, les tests et la
> documentation sont produits par l'IA ; les choix techniques et les essais sur
> matériel réel restent humains. Les suggestions d'évolution (nouveaux systèmes,
> nouvelles intégrations, application Android) sont les bienvenues — voir
> [CONTRIBUTING.md](../CONTRIBUTING.md).

## Prérequis

| Élément | Minimum | Recommandé |
| --- | --- | --- |
| Python | 3.10 | 3.11 ou 3.12 |
| RAM | 64 Mo libres | 256 Mo |
| Système | Windows 10/11, Linux, macOS | Raspberry Pi OS Lite |
| Réseau | Accès HTTPS sortant vers Atlantic/Pronote | + Tailscale pour l'accès distant |

`pronotepy` est le seul paquet optionnel : il n'est utile que pour l'import Pronote.

## Windows

```bat
git clone https://github.com/shervet/ecasa.git
cd ecasa
run.bat
```

`run.bat` lance eCasa (avec `.venv\Scripts\python.exe` s'il existe, sinon le Python
du système) et garde la fenêtre ouverte en cas d'erreur. Pour arrêter proprement :
double-cliquer sur **`stop.bat`** (arrête aussi pm2, les unités systemd Node et le
conteneur MyElectricalData local s'ils tournent).

Ouvrir ensuite <http://127.0.0.1:8780>.

> Si `run.bat` affiche « python n'est pas reconnu », installer Python depuis
> python.org en cochant « Add python.exe to PATH ».

## Linux (général)

```bash
git clone https://github.com/shervet/ecasa.git
cd ecasa
./run.sh
```

`run.sh` utilise `.venv/bin/python` s'il existe, sinon `python3`.

Pour Pronote, créer l'environnement une fois avant :

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Pour arrêter : `./stop.sh` (service systemd, processus résiduels, pm2 et
MyElectricalData local).

## Raspberry Pi (service systemd)

```bash
git clone https://github.com/shervet/ecasa.git
cd ecasa
sudo ./install-pi.sh
sudo systemctl status ecasa
sudo journalctl -u ecasa -f
```

`install-pi.sh` crée un environnement virtuel, installe l'unité `systemd` et active
le démarrage automatique. Le service survit aux coupures de courant et redémarre seul.

Après une mise à jour du code :

```bash
cd ~/ecasa
git pull
sudo systemctl restart ecasa
```

## Premier lancement

1. eCasa crée `config.json` à partir des valeurs par défaut.
2. Sans identifiants Atlantic, il démarre en **simulation** (appareils fictifs).
3. Ouvrir **Réglages** pour saisir les accès, puis redémarrer si demandé.

Les réglages sont aussi modifiables à la main dans `config.json`
(voir [configuration.md](configuration.md)). Ce fichier contient des secrets :
il ne doit **jamais** être versionné ni partagé.

## Port et accès réseau

- Port par défaut : **8780**.
- `host: "127.0.0.1"` : accessible uniquement depuis la machine.
- `host: "0.0.0.0"` : accessible depuis le réseau local (recommandé pour téléphone/tablette).
- `allow_remote_admin: false` : les routes sensibles (réglages, Pronote, arrêt) restent
  réservées à la machine locale. À n'activer que sur un réseau de confiance.

## Installer la PWA

Ouvrir eCasa dans le navigateur du téléphone, puis :

- **Android / Chrome** : menu ⋮ → « Installer l'application ».
- **iPhone / Safari** : Partager → « Sur l'écran d'accueil ».

L'application fonctionne alors en plein écran, avec l'icône eCasa.

## Accès distant avec Tailscale

Le guide pas à pas (nom du nœud, MagicDNS, HTTPS, `tailscale serve`) est dans
**Réglages → Tailscale → Ouvrir l'aide**. En résumé :

```bash
tailscale up --hostname=ecasa
tailscale serve --bg --set-path=/ http://127.0.0.1:8780
```

Puis ouvrir `https://ecasa.<tailnet>.ts.net/`.

## Sauvegarde et restauration

- **Réglages → Stockage** : export/import JSON complet (état + réglages) depuis l'interface.
- **Scripts en ligne de commande** (sauvegarde de `config.json`, `data/state.json`, `data/history/` et `data/energy.json` dans un zip) :
  - Linux : `./export-config.sh [archive.zip]` / `./import-config.sh <archive.zip>`
  - Windows : `export-config.bat [archive.zip]` / `import-config.bat <archive.zip>`
  - L'import sauvegarde les fichiers remplacés dans `config-backup-YYYYMMDD-HHMMSS/`.
- À sauvegarder également, au besoin, l'ensemble du dossier `data/` (état, historique, synchronisation).

Une sauvegarde régulière suffit à tout restaurer (y compris les modèles de planning et le cache de consommation).

## Mise à jour

```bash
cd ecasa
git pull
```

Puis relancer :

```bat
rem Windows
stop.bat
run.bat
```

```bash
# Linux / Raspberry Pi (script)
./stop.sh
./run.sh

# Linux / Raspberry Pi (service systemd)
sudo systemctl restart ecasa
```

Les migrations de configuration sont automatiques : les clés absentes prennent leur
valeur par défaut. Pense à recharger l'onglet (les fichiers web sont mis en cache en
RAM au démarrage) et à vider le cache du service worker si l'icône ou le style
semble ancien.

## Désinstallation

```bat
rem Windows
stop.bat
```

```bash
# Linux / Raspberry Pi (service installé)
sudo systemctl disable --now ecasa
sudo rm /etc/systemd/system/ecasa.service

# puis supprimer le dossier du projet
rm -rf ~/ecasa
```

Aucune donnée n'est envoyée ailleurs : supprimer le dossier supprime tout.
