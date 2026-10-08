"""Switches related to ESPSomfy-RTS-HA."""

from __future__ import annotations

from typing import Any

import aiohttp

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    EVT_CONNECTED,
    EVT_GROUPSTATE,
    EVT_SCHEDULEREMOVED,
    EVT_SCHEDULESTATE,
    EVT_SHADESTATE,
)
from .controller import ESPSomfyController
from .entity import (
    ESPSomfyEntity,
    async_get_schedule_time_labels,
    resolve_schedule_target,
    schedule_time_label,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up shades for the shade controller."""
    controller = hass.data[DOMAIN][config_entry.entry_id]
    new_entities = []
    data = controller.api.get_config()
    if "serverId" in data:
        for shade in controller.api.shades:
            try:
                if "shadeType" in shade and (
                    int(shade["shadeType"]) == 9 or int(shade["shadeType"]) == 10
                ):
                    new_entities.append(
                        ESPSomfyBinarySwitch(controller=controller, data=shade)
                    )
                elif "sunSensor" in shade:
                    if shade["sunSensor"] is True:
                        new_entities.append(
                            ESPSomfySunSwitch(controller=controller, data=shade)
                        )
                elif "shadeType" in shade:
                    match shade["shadeType"]:
                        case 3:
                            new_entities.append(
                                ESPSomfySunSwitch(controller=controller, data=shade)
                            )
            except KeyError:
                pass

        for group in controller.api.groups:
            try:
                if "sunSensor" in group:
                    if group["sunSensor"] is True:
                        new_entities.append(
                            ESPSomfySunSwitch(controller=controller, data=group)
                        )

            except KeyError:
                pass

        # A transient failure here must not take down shade/group switches above.
        try:
            schedules = await controller.api.load_schedules() or []
        except aiohttp.ClientError:
            schedules = []
        if schedules:
            sun_labels = await async_get_schedule_time_labels(hass)
            for schedule in schedules:
                target = resolve_schedule_target(
                    controller, schedule.get("targetType"), schedule.get("targetId")
                )
                new_entities.append(
                    ESPSomfyScheduleSwitch(
                        controller=controller,
                        schedule=schedule,
                        target=target,
                        time_label=schedule_time_label(schedule, sun_labels),
                    )
                )

    if new_entities:
        async_add_entities(new_entities)


class ESPSomfySunSwitch(ESPSomfyEntity, SwitchEntity):
    """A sun flag switch for toggling sun mode."""

    def __init__(self, controller: ESPSomfyController, data) -> None:
        """Initialize a new SunSwitch."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._shade_id = None
        self._group_id = None
        self._attr_icon = "mdi:white-balance-sunny"

        # Normes de nommage et traduction HA
        self._attr_has_entity_name = True
        self._attr_translation_key = "sun_mode_switch"
        self._attr_entity_category = EntityCategory.CONFIG

        self._sunswitch_type = None
        self._available = True
        if "groupId" in data:
            self._group_id = data["groupId"]
            self._attr_unique_id = (
                f"sunswitch_group_{controller.unique_id}_{self._group_id}"
            )
            self._sunswitch_type = "group"
        else:
            self._shade_id = data["shadeId"]
            self._attr_unique_id = f"sunswitch_{controller.unique_id}_{self._shade_id}"
            self._sunswitch_type = "motor"

        if "flags" in data:
            self._attr_is_on = bool((int(data["flags"]) & 0x01) == 0x01)
        else:
            self._attr_is_on = False

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if (
            self._controller.data["event"] == EVT_CONNECTED
            and "connected" in self._controller.data
        ):
            self._available = bool(self._controller.data["connected"])
            self.async_write_ha_state()
        elif (
            self._sunswitch_type == "motor"
            and "shadeId" in self._controller.data
            and self._controller.data["shadeId"] == self._shade_id
        ):
            if (
                self._controller.data["event"] == EVT_SHADESTATE
                and "flags" in self._controller.data
            ):
                self._attr_is_on = bool(
                    (int(self._controller.data["flags"]) & 0x01) == 0x01
                )
                self.async_write_ha_state()
        elif (
            self._sunswitch_type == "group"
            and "groupId" in self._controller.data
            and self._controller.data["groupId"] == self._group_id
        ):
            if (
                self._controller.data["event"] == EVT_GROUPSTATE
                and "flags" in self._controller.data
            ):
                self._attr_is_on = bool(
                    (int(self._controller.data["flags"]) & 0x01) == 0x01
                )
                self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        if self._sunswitch_type == "motor":
            await self.coordinator.api.sun_flag_on(self._shade_id)
            return
        await self.coordinator.api.sun_flag_group_on(self._group_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        if self._sunswitch_type == "motor":
            await self.coordinator.api.sun_flag_off(self._shade_id)
            return
        await self.coordinator.api.sun_flag_group_off(self._group_id)

    @property
    def available(self) -> bool:
        """Indicates whether the shade is available."""
        return self._available


class ESPSomfyBinarySwitch(ESPSomfyEntity, SwitchEntity):
    """A binary switch for toggling a dry contact."""

    def __init__(self, controller: ESPSomfyController, data) -> None:
        """Initialize a new BinarySwitch."""
        super().__init__(controller=controller, data=data)
        self._controller = controller
        self._shade_id = None
        self._group_id = None

        # Normes de nommage et traduction HA
        self._attr_has_entity_name = True
        self._attr_translation_key = "dry_contact_switch"

        self._binaryswitch_type = data["shadeType"]
        self._shade_id = data["shadeId"]
        self._available = True
        self._attr_unique_id = f"binaryswitch_{controller.unique_id}_{self._shade_id}"
        self._flip_commands = False
        if "flipCommands" in data:
            self._flip_commands = bool(data["flipCommands"])
        if "position" in data:
            self._attr_is_on = bool((int(data["position"])) > 0)
        else:
            self._attr_is_on = False

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.registry_entry.disabled:
            return
        if (
            self._controller.data["event"] == EVT_CONNECTED
            and "connected" in self._controller.data
        ):
            self._available = bool(self._controller.data["connected"])
            self.async_write_ha_state()
        elif (
            "position" in self._controller.data
            and self._controller.data["shadeId"] == self._shade_id
        ):
            self._attr_is_on = bool((int(self._controller.data["position"])) > 0)
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Indicates whether the shade is available."""
        return self._available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        if self._binaryswitch_type == 10:
            if self._flip_commands:
                await self.coordinator.api.close_shade(self._shade_id)
            else:
                await self.coordinator.api.open_shade(self._shade_id)
        else:
            await self.coordinator.api.toggle_shade(self._shade_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        if self._binaryswitch_type == 10:
            if self._flip_commands:
                await self.coordinator.api.open_shade(self._shade_id)
            else:
                await self.coordinator.api.close_shade(self._shade_id)
        else:
            await self.coordinator.api.toggle_shade(self._shade_id)


class ESPSomfyScheduleSwitch(ESPSomfyEntity, SwitchEntity):
    """Enables/disables one schedule - the HA-side twin of MQTT's schedules/<id>/enabled/set.

    Attached to the shade/group the schedule targets (resolve_schedule_target()), not the
    hub, so it shows up alongside that equipment's other entities. No live push exists for a
    schedule created/deleted after startup (same limitation as shades: see controller.py's
    ws_onpacket comment) - only enabled/nextTime update live, via scheduleState.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "schedule_enabled"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = False

    def __init__(
        self,
        *,
        controller: ESPSomfyController,
        schedule: any,
        target: any,
        time_label: str = "",
    ) -> None:
        """Initialize a new schedule switch."""
        super().__init__(data=target, controller=controller)
        self._controller = controller
        self._schedule_id = schedule["id"]
        self._attr_unique_id = (
            f"{controller.unique_id}_schedule_{self._schedule_id}_enabled"
        )
        self._attr_translation_placeholders = {
            "name": schedule.get("name") or str(self._schedule_id),
            "time": time_label,
        }
        self._available = True
        self._attr_is_on = bool(schedule.get("enabled"))

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        d = self._controller.data
        evt = d.get("event", "")
        if evt == EVT_CONNECTED:
            if "connected" in d and self._available != bool(d["connected"]):
                self._available = bool(d["connected"])
                self.async_write_ha_state()
            return
        if evt not in (EVT_SCHEDULESTATE, EVT_SCHEDULEREMOVED) or d.get("id") != self._schedule_id:
            return
        if evt == EVT_SCHEDULESTATE:
            is_on = bool(d.get("enabled"))
            if is_on != self._attr_is_on:
                self._attr_is_on = is_on
                self.async_write_ha_state()
        else:
            self._available = False
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self.coordinator.api.set_schedule_enabled(self._schedule_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self.coordinator.api.set_schedule_enabled(self._schedule_id, False)

    @property
    def available(self) -> bool:
        """Indicates whether the schedule switch is available."""
        return self._available
