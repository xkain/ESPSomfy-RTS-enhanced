"""The ESPSomfy RTS integration."""

from __future__ import annotations

from enum import IntFlag

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN, MANUFACTURER, PLATFORMS, VERSION
from .controller import ESPSomfyAPI, ESPSomfyController


class ESPSomfyRTSEntityFeature(IntFlag):
    """Supported features of ESPSomfy-RTS Entities."""

    REBOOT = 1
    BACKUP = 2


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ESPSomfy-RTS from a config entry."""
    api = ESPSomfyAPI(hass, entry.entry_id, entry.data)
    controller = ESPSomfyController(entry.entry_id, hass, api)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    # 1. On récupère la configuration initiale du boîtier
    await api.get_initial()
    if not api.is_configured:
        raise ConfigEntryNotReady(
            f"Could not find ESPSomfy-RTS device with address {api.get_api_url()}"
        )

    hass.config_entries.async_update_entry(entry, title=api.deviceName)

    # Crée le hub explicitement avant les plateformes : les entités Volet/Groupe
    # référencent son id via via_device_id, et les plateformes sont configurées
    # en parallèle (leur ordre dans PLATFORMS ne garantit donc rien).
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, controller.unique_id)},
        configuration_url=api.get_config_url(),
        name=controller.device_name,
        manufacturer=MANUFACTURER,
        model=f"ESPSomfy-RTS Enhanced Integration {VERSION}",
        sw_version=controller.version,
    )

    async def _async_ws_close(_: Event) -> None:
        await controller.ws_close()

    # Si Home Assistant s'arrête proprement, on ferme le socket
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_ws_close)
    )

    # 3. On lance la connexion WebSocket (qui chargera les plateformes au moment opportun)
    await controller.ws_connect()

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    controller: ESPSomfyController = hass.data[DOMAIN].get(entry.entry_id)
    if controller is not None:
        await controller.ws_close()
        #Simplification conforme aux normes récentes de HA pour le déchargement
        if unload_ok := await hass.config_entries.async_unload_platforms(
            entry, PLATFORMS
        ):
            hass.data[DOMAIN].pop(entry.entry_id)
        return unload_ok
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    return True
