<div align="center">

[![GitHub Release](https://img.shields.io/github/release/xkain/ESPSomfy-RTS-HA.svg?style=for-the-badge)](https://github.com/xkain/ESPSomfy-RTS-HA/releases) [![GitHub Activity](https://img.shields.io/github/last-commit/xkain/ESPSomfy-RTS-HA/main?style=for-the-badge)](https://github.com/xkain/ESPSomfy-RTS-HA/commits/main) [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
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

A custom Home Assistant integration (Fork) to precisely control and monitor your equipment (roller shutters, shades, garage doors, gates) using the RTS 433 MHz protocol.

### [README complet en français 🇫🇷 »](https://github.com/xkain/ESPSomfy-RTS-HA/blob/main/README_fr.md)
### [Explore the documentation »](https://github.com/xkain/ESPSomfy-RTS-HA/wiki)

**[Report Bug](https://github.com/xkain/ESPSomfy-RTS-HA/issues) · [Request Feature](https://github.com/xkain/ESPSomfy-RTS-HA/pulls)**

</div>

<br />

## Overview

**ESPSomfy-RTS-HA** allows you to control all your devices in Home Assistant and accurately set their position. This integration relies on an ESP32 microcontroller coupled with a cheap CC1101 transceiver module, driven by the [ESPSomfy-RTS](https://github.com/xkain/ESPSomfy-RTS) firmware.

## Requirements

This integration requires hardware running on an **ESP32** and a **CC1101** transceiver.
To assemble and configure this hardware, please refer to the repository wiki: [components](https://github.com/rstrouse/ESPSomfy-RTS). The radio protocol must be configured for your devices before using this integration.

---

## Installation

### Method 1: Via HACS (Recommended)
The easiest way to get started is to add this repository as a [Custom Repository](https://hacs.xyz/docs/faq/custom_repositories/) in HACS:

1. Go to **HACS** → **Integrations**.
2. Click the **3 dots** in the top right corner and select **Custom repositories**.
3. Add the following URL: `https://github.com/xkain/ESPSomfy-RTS-HA`
4. Select **Integration** as the category, then click **Add**.
5. Download and install the integration.

### Method 2: Manual Installation
Simply copy the contents of the `custom_components/espsomfy_rts/` folder and paste it into the `config/custom_components/espsomfy_rts/` directory of your Home Assistant instance.

### Initial Setup
Once installed and Home Assistant is restarted, the integration will **automatically discover** your radio modules on the local network. Simply navigate to **Settings** → **Devices & Services**: your ESPSomfy-RTS device will show up, ready to be configured.

---

## Features

* **Full Control**: Open, close, and set intermediate positions (percentages) directly from Home Assistant.
* **State Synchronization**: The integration tracks the real-time position of the cover, even if it is operated by a physical Somfy Telis remote or via the ESP32 web interface.
* **Simplified Updates**: You will receive a notification whenever a new version of the integration is available. Additionally, since v2.2.1 of the ESPSomfy RTS firmware, a dedicated `update` entity allows you to update your ESP32 firmware directly from the Home Assistant UI.
* **Automations**: All shades are exposed as standard `cover` entities, ready to be added to your dashboards and used in automations via native services.


<img width="1006" height="470" alt="Capture d&#39;écran_20260527_113106" src="https://github.com/user-attachments/assets/46870a36-816e-406b-bb55-51aa91e7f178" />

---

## Event Management (Events)

The integration emits events on the Home Assistant event bus for every intercepted command (whether it originates from HA, a physical remote control, or the ESP web interface). These events use the `espsomfy-rts_event` type.

### Event Payload Structure

| Key | Description |
| :--- | :--- |
| `entity_id` | The target entity ID in Home Assistant (e.g., `cover.living_room_shade`). |
| `event_key` | The event trigger (currently always set to `shadeCommand`). |
| `name` | The friendly name assigned to the device. |
| `remote_address` | The radio address defined for the motor in ESPSomfy RTS. |
| `source_address` | The source device address (remote control channel address or group address). |

### Values for `source` (Command Origin)
* `remote`: The user pressed a button on a physical remote control.
* `internal`: The command was initiated from the ESPSomfy RTS internal interface.
* `group`: The command was part of a grouped action.

### Values for `command` (Intercepted Actions)
* `Up` / `Down` / `My`: Standard commands (Up, Down, Stop/Favorite Position).
* `StepUp` / `StepDown`: Step-by-step micro-adjustments.
* `Prog`: Program button press.
* `My+Up` / `My+Down` / `Up+Down` / `My+Up+Down`: Simultaneous physical key combinations.

---

## Automations and Services

Many specific services are available to enrich your automations. Check out the usage examples directly in the [Services section of the Wiki](https://github.com/rstrouse/ESPSomfy-RTS-HA/wiki/Services).
