# Installation

MeCasa est une application Python sans dépendance obligatoire. Trois étapes :
installer Python, récupérer le code, lancer le serveur.

> **À propos** : MeCasa est un projet open source **développé avec l'aide d'une
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
git clone https://github.com/shervet/mecasa.git
cd mecasa
python -m mecasa.main
```

Ouvrir <http://127.0.0.1:8780>.

Pour un lancement en un clic : double-cliquer `run.bat`. Pour arrêter : `stop.bat`.

> Si `python` n'est pas reconnu, installer Python depuis python.org en cochant
> « Add python.exe to PATH ».

## Linux (général)

```bash
git clone https://github.com/shervet/mecasa.git
cd mecasa
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # facultatif : Pronote
.venv/bin/python -m mecasa.main
```

## Raspberry Pi (service systemd)

```bash
git clone https://github.com/shervet/mecasa.git
cd mecasa
sudo ./install-pi.sh
sudo systemctl status mecasa
sudo journalctl -u mecasa -f
```

`install-pi.sh` crée un environnement virtuel, installe l'unité `systemd` et active
le démarrage automatique. Le service survit aux coupures de courant et redémarre seul.

Après une mise à jour du code :

```bash
cd ~/mecasa
git pull
sudo systemctl restart mecasa
```

## Premier lancement

1. MeCasa crée `config.json` à partir des valeurs par défaut.
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

Ouvrir MeCasa dans le navigateur du téléphone, puis :

- **Android / Chrome** : menu ⋮ → « Installer l'application ».
- **iPhone / Safari** : Partager → « Sur l'écran d'accueil ».

L'application fonctionne alors en plein écran, avec l'icône MeCasa.

## Accès distant avec Tailscale

Le guide pas à pas (nom du nœud, MagicDNS, HTTPS, `tailscale serve`) est dans
**Réglages → Tailscale → Ouvrir l'aide**. En résumé :

```bash
tailscale up --hostname=mecasa
tailscale serve --bg --set-path=/ http://127.0.0.1:8780
```

Puis ouvrir `https://mecasa.<tailnet>.ts.net/`.

## Sauvegarde et restauration

- **Réglages → Stockage** : export JSON complet (état + réglages), import pour restaurer.
- À sauvegarder également : `config.json` et le dossier `data/` (état + historique).

Une sauvegarde régulière du dossier complet suffit à tout restaurer.

## Mise à jour

```bash
cd mecasa
git pull
# Linux/Pi
sudo systemctl restart mecasa
# Windows
stop.bat && run.bat
```

Les migrations de configuration sont automatiques : les clés absentes prennent leur
valeur par défaut.

## Désinstallation

```bash
sudo systemctl disable --now mecasa
sudo rm /etc/systemd/system/mecasa.service
rm -rf ~/mecasa
```

Aucune donnée n'est envoyée ailleurs : supprimer le dossier supprime tout.
