"""ESPSomfy parent entity class."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, VERSION
from .controller import ESPSomfyController


def resolve_schedule_target(
    controller: ESPSomfyController, target_type: str, target_id: int
) -> dict:
    """Find the shade/group a schedule targets, so its entities attach to that device.

    Falls back to a minimal placeholder if the target can't be found (e.g. deleted since
    the schedule was created) rather than raising - the entities still need somewhere to
    attach.
    """
    if target_type == "group":
        for group in controller.api.groups:
            if group.get("groupId") == target_id:
                return group
        return {"groupId": target_id, "name": f"Group {target_id}"}
    for shade in controller.api.shades:
        if shade.get("shadeId") == target_id:
            return shade
    return {"shadeId": target_id, "name": f"Shade {target_id}"}


async def async_get_schedule_time_labels(hass: HomeAssistant) -> dict[str, str]:
    """Fetch the "Sunrise"/"Sunset" words used by schedule_time_label(), once per platform.

    Pulled from the "exceptions" translation category (the same free-text bucket used for
    the release-notes banner) since entity.sensor/switch schemas don't support arbitrary
    prose - only {placeholder} substitution into a fixed template.
    """
    prefix = f"component.{DOMAIN}.exceptions.schedule_time_"
    translations = await async_get_translations(
        hass, hass.config.language, "exceptions", integrations=[DOMAIN]
    )
    if f"{prefix}sunrise.message" not in translations:
        translations = await async_get_translations(
            hass, "en", "exceptions", integrations=[DOMAIN]
        )
    return {
        "sunrise": translations.get(f"{prefix}sunrise.message", "Sunrise"),
        "sunset": translations.get(f"{prefix}sunset.message", "Sunset"),
    }


def schedule_time_label(schedule: dict, sun_labels: dict[str, str]) -> str:
    """Disambiguating time anchor for a schedule's entity display name.

    Firmware schedules sharing the same target+days can also share the same free-text
    "name" (see the firmware UI's "multi-horaires" grouping) - a clock entry and a
    sunrise/sunset entry for the same target then collide on
    translation_placeholders["name"], producing identical friendly names and
    _2-suffixed entity_ids with no way to tell them apart. This gives each one a
    distinct suffix instead: "09:00" for a clock entry, the translated "Sunrise"/
    "Sunset" word for the other two.
    """
    time_ref = schedule.get("timeRef")
    if time_ref == "clock":
        hour, minute = schedule.get("hour"), schedule.get("minute")
        if hour is not None and minute is not None:
            return f"{hour:02d}:{minute:02d}"
        return ""
    return sun_labels.get(time_ref, "")


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
        # Groupe
        if self._data and "groupId" in self._data:
            group_id = self._data["groupId"]
            return DeviceInfo(
                identifiers={(DOMAIN, f"group_{self.controller.unique_id}_{group_id}")},
                name=self._data.get("name", f"Group {group_id}"),
                manufacturer=MANUFACTURER,
                model="ESPSomfy-RTS Group",
                via_device_id=self._via_device_id,
            )

        # Équipement
        if self._data and "shadeId" in self._data:
            shade_id = self._data["shadeId"]
            return DeviceInfo(
                identifiers={(DOMAIN, f"shade_{self.controller.unique_id}_{shade_id}")},
                name=self._data.get("name", f"Shade {shade_id}"),
                manufacturer=MANUFACTURER,
                model="ESPSomfy-RTS Device",
                via_device_id=self._via_device_id,
            )

        # Hub, par défaut
        return DeviceInfo(
            configuration_url=self.controller.api.get_config_url(),
            identifiers={(DOMAIN, self.controller.unique_id)},
            name=self.controller.device_name,
            manufacturer=MANUFACTURER,
            model=f"ESPSomfy-RTS Enhanced Integration {VERSION}",
            sw_version=self.controller.version,
            hw_version=None,
        )
