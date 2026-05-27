<div align="center">

[![GitHub Release](https://img.shields.io/github/release/xkain/ESPSomfy-RTS-HA.svg?style=for-the-badge)](https://github.com/xkain/ESPSomfy-RTS-HA/releases) [![GitHub Activity](https://img.shields.io/github/last-commit/xkain/ESPSomfy-RTS-HA?style=for-the-badge)](https://github.com/xkain/ESPSomfy-RTS-HA/commits/main) [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
<br />
[![License](https://img.shields.io/github/license/xkain/ESPSomfy-RTS-HA.svg?style=for-the-badge)](LICENSE) [![Project Maintenance](https://img.shields.io/badge/maintainer-xkain-blue.svg?style=for-the-badge)](https://github.com/xkain) [![GitHub stars](https://img.shields.io/github/stars/xkain/ESPSomfy-RTS-HA?style=for-the-badge&logo=github&color=blue)](https://github.com/xkain/ESPSomfy-RTS-HA/stargazers)

<br />

<img src="https://github.com/xkain/ESPSomfy-RTS/blob/main/images/banniereRTS-ha.png" alt="ESPSomfy-RTS-HA Banner" width="100%">

<br />
<br />

<a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=xkain&repository=ESPSomfy-RTS-HA">
  <img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.">
</a>

<br />
<br />

Une intégration Home Assistant personnalisée (Fork) permettant de contrôler et de suivre précisément vos volets roulants, stores, porte de garage, portail et autres équipements utilisant le protocole RTS 433 MHz.

### [Explorer la documentation »](https://github.com/xkain/ESPSomfy-RTS-HA/wiki)

**[Signaler un Bug](https://github.com/xkain/ESPSomfy-RTS-HA/issues) · [Request Feature](https://github.com/xkain/ESPSomfy-RTS-HA/pulls)**

</div>

<br />

## Présentation

**ESPSomfy-RTS-HA** vous permet de piloter tous vos équipements dans Home Assistant et de définir précisément leur position. L'intégration s'appuie sur un microcontrôleur ESP32 couplé à un module émetteur-récepteur CC1101 piloté par le firmware [ESPSomfy-RTS](https://github.com/xkain/ESPSomfy-RTS).

## Prérequis

Cette intégration nécessite une passerelle matérielle basée sur un **ESP32** et un **CC1101**. 
Pour assembler et configurer ce matériel, veuillez vous référer au wiki du dépot : [components](https://github.com/rstrouse/ESPSomfy-RTS). Le protocole radio doit être configuré pour vos équipements avant d'utiliser cette intégration.

---

## Installation

### Méthode 1 : Via HACS (Recommandée)
L'installation la plus simple se fait en ajoutant ce dépôt comme [Dépôt Personnalisé (Custom Repository)](https://hacs.xyz/docs/faq/custom_repositories/) dans HACS :

1. Allez dans **HACS** → **Intégrations**.
2. Cliquez sur les **3 points** en haut à droite et sélectionnez **Dépôts personnalisés**.
3. Ajoutez l'URL suivante : `https://github.com/xkain/ESPSomfy-RTS-HA`
4. Sélectionnez **Intégration** comme catégorie, puis cliquez sur **Ajouter**.
5. Téléchargez et installez l'intégration.

### Méthode 2 : Installation Manuelle
Copiez simplement le contenu du dossier `custom_components/espsomfy_rts/` et collez-le dans le répertoire `config/custom_components/espsomfy_rts/` de votre instance Home Assistant.

### Configuration initiale
Une fois installée et Home Assistant redémarré, l'intégration **détectera automatiquement** vos modules radio présents sur le réseau local. Rendez-vous simplement dans **Paramètres** → **Appareils et services** : votre équipement ESPSomfy-RTS y apparaîtra prêt à être configuré.

---

## Fonctionnalités

* **Contrôle total** : Ouvrez, fermez et définissez des positions intermédiaires (pourcentage) directement depuis Home Assistant.
* **Synchronisation d'état** : L'intégration suit en temps réel la position réelle du volet, même si celui-ci est actionné par une télécommande physique Somfy Telis ou via l'interface web de l'ESP32.
* **Mises à jour simplifiées** : Vous recevez une notification dès qu'une nouvelle version de l'intégration est disponible. De plus, depuis la v2.2.1 du firmware ESPSomfy RTS, une entité `update` dédiée vous permet de mettre à jour le micrologiciel de vos ESP32 directement depuis l'interface de Home Assistant.
* **Automatisations** : Tous les volets sont exposés comme des entités `cover` classiques, prêtes à être intégrées à vos tableaux de bord et à vos automatisations via les services natifs.

<img width="1006" height="470" alt="Capture d&#39;écran_20260527_113106" src="https://github.com/user-attachments/assets/46870a36-816e-406b-bb55-51aa91e7f178" />

---

## Gestion des Événements (Events)

L'intégration émet des événements sur le bus de Home Assistant pour chaque commande interceptée (qu'elle vienne de HA, d'une télécommande physique ou de l'interface web de l'ESP). Ces événements utilisent le type `espsomfy-rts_event`.

### Structure du Payload de l'événement

| Clé | Description |
| :--- | :--- |
| `entity_id` | L'identifiant de l'entité cible dans Home Assistant (ex: `cover.volet_salon`). |
| `event_key` | Le déclencheur de l'événement (actuellement toujours positionné sur `shadeCommand`). |
| `name` | Le nom convivial attribué à l'équipement. |
| `remote_address` | L'adresse radio définie pour le moteur dans ESPSomfy RTS. |
| `source_address` | L'adresse de l'appareil source (adresse du canal de la télécommande ou du groupe). |

### Valeurs de `source` (Origine de la commande)
* `remote` : L'utilisateur a appuyé sur un bouton d'une télécommande physique.
* `internal` : La commande a été initiée depuis l'interface interne de l'ESPSomfy RTS.
* `group` : La commande fait partie d'une action groupée.

### Valeurs de `command` (Actions interceptées)
* `Up` / `Down` / `My` : Commandes standards (Monter, Descendre, Stop/Position Favorite).
* `StepUp` / `StepDown` : Variations pas-à-pas.
* `Prog` : Appui sur le bouton de programmation.
* `My+Up` / `My+Down` / `Up+Down` / `My+Up+Down` : Combinaisons de touches physiques simultanées.

---

##  Automatisations et Services

De nombreux services spécifiques sont mis à votre disposition pour enrichir vos automatisations. Consultez les exemples d'utilisation directement dans la section [Services du Wiki](https://github.com/rstrouse/ESPSomfy-RTS-HA/wiki/Services).
