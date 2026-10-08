"""The ESPSomfy RTS integration."""

from __future__ import annotations

from enum import IntFlag

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
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

    # Configuration initiale du boîtier
    await api.get_initial()
    if not api.is_configured:
        if api.bad_credentials:
            # Refusés : aucune reprise n'aboutira, et chacune use le quota anti-force-brute.
            raise ConfigEntryAuthFailed(
                f"ESPSomfy-RTS at {api.get_api_url()} rejected the stored credentials"
            )
        if api.needs_credentials:
            # Manquants : NotReady bouclerait sans fin. AuthFailed demande à l'utilisateur.
            raise ConfigEntryAuthFailed(
                f"Authentication required for {api.get_api_url()} but no credentials are stored"
            )
        raise ConfigEntryNotReady(
            f"Could not find ESPSomfy-RTS device with address {api.get_api_url()}"
        )

    hass.config_entries.async_update_entry(entry, title=api.deviceName)

    # Hub créé avant les plateformes : via_device_id le référence, et elles démarrent
    # en parallèle.
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

    # Fermeture propre du socket à l'arrêt
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_ws_close)
    )

    # Pas d'add_update_listener : cette ligne n'est pas atteinte quand la configuration
    # échoue, donc les flux rechargent eux-mêmes.

    # Le socket chargera les plateformes au bon moment
    await controller.ws_connect()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    controller: ESPSomfyController = hass.data[DOMAIN].get(entry.entry_id)
    if controller is not None:
        await controller.ws_close()
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
