"""ESPSomfy parent entity class."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, VERSION
from .controller import ESPSomfyController


class ESPSomfyEntity(CoordinatorEntity[ESPSomfyController], Entity):
    """Base entity for the ESPSomfy controller."""

    def __init__(self, *, data: any, controller: ESPSomfyController) -> None:
        """Initialize the entity."""
        super().__init__(coordinator=controller)
        self.controller = controller
        self._data = data # Stockage des données (contient shadeId ou groupId le cas échéant)

    @property
    def should_poll(self) -> bool:
        """Indicates that the entity should not poll."""
        return False

    @property
    def _via_device_id(self) -> str | None:
        """Resolve the hub device's registry id for via_device_id.

        via_device_id wants the hub's registry id, not its identifiers tuple,
        and the hub device is created eagerly in async_setup_entry() so it
        already exists by the time shade/group entities are added.
        """
        if self.hass is None:
            return None
        hub_device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, self.controller.unique_id), self.controller.config_entry_id
        )
        return hub_device.id if hub_device is not None else None

    @property
    def device_info(self) -> DeviceInfo | None:
        """Device info."""
        # L'entité est liée à un Groupe spécifique
        if self._data and "groupId" in self._data:
            group_id = self._data["groupId"]
            return DeviceInfo(
                identifiers={(DOMAIN, f"group_{self.controller.unique_id}_{group_id}")},
                name=self._data.get("name", f"Group {group_id}"),
                manufacturer=MANUFACTURER,
                model="ESPSomfy-RTS Group",
                via_device_id=self._via_device_id,
            )

        # L'entité est liée à un Volet/Store (Shade) spécifique
        if self._data and "shadeId" in self._data:
            shade_id = self._data["shadeId"]
            return DeviceInfo(
                identifiers={(DOMAIN, f"shade_{self.controller.unique_id}_{shade_id}")},
                name=self._data.get("name", f"Shade {shade_id}"),
                manufacturer=MANUFACTURER,
                model="ESPSomfy-RTS Device",
                via_device_id=self._via_device_id,
            )

        # (Par défaut) : L'entité est liée à la passerelle/hub globale
        return DeviceInfo(
            configuration_url=self.controller.api.get_config_url(),
            identifiers={(DOMAIN, self.controller.unique_id)},
            name=self.controller.device_name,
            manufacturer=MANUFACTURER,
            model=f"ESPSomfy-RTS Enhanced Integration {VERSION}",
            sw_version=self.controller.version,
            hw_version=None,
        )
