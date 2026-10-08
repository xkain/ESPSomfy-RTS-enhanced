"""Sensors related to ESPSomfy-RTS-HA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import statistics
import time

import aiohttp

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfDataRate,
    UnitOfInformation,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVT_CONNECTED,
    EVT_ETHERNET,
    EVT_MEMSTATUS,
    EVT_SCHEDULEREMOVED,
    EVT_SCHEDULESTATE,
    EVT_SHADEADDED,
    EVT_SHADECOMMAND,
    EVT_SHADESTATE,
    EVT_WIFISTRENGTH,
)
from .controller import ESPSomfyController
from .entity import (
    ESPSomfyEntity,
    async_get_schedule_time_labels,
    resolve_schedule_target,
    schedule_time_label,
)


@dataclass
class ESPSomfyDiagSensorDescription(SensorEntityDescription):
    """A base class descriptor for a sensor entity."""

    id: str | None = None
    events: dict | None = None
    native_value: any | None = None
    min_interval: int = 0
    value_count: int = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up shades for the shade controller."""

    controller = hass.data[DOMAIN][config_entry.entry_id]
    new_entities = []
    data = controller.api.get_config()
    # hardwareProfile only lives on /loginContext (port 80), never on /discovery - see
    # fetch_login_context()'s docstring. Static for the device's lifetime, so a one-off fetch
    # at setup is enough; no live event to subscribe to for it.
    login_context = await controller.api.fetch_login_context() or {}
    if "serverId" in data:
        if "hostname" in data:
            new_entities.append(
                ESPSomfyDiagSensor(
                    controller=controller,
                    cfg=ESPSomfyDiagSensorDescription(
                        key="host_name",
                        translation_key="host_name",
                        entity_category=EntityCategory.DIAGNOSTIC,
                        has_entity_name=True,
                        native_value=data["hostname"],
                        events={},
                        icon="mdi:tag-text",
                    ),
                    data=data,
                )
            )
        if "chipModel" in data:
            chip_model = "ESP32"
            if len(data["chipModel"]):
                chip_model += "-"
                chip_model += data["chipModel"]
            new_entities.append(
                ESPSomfyDiagSensor(
                    controller=controller,
                    cfg=ESPSomfyDiagSensorDescription(
                        key="chip_model",
                        translation_key="chip_model",
                        entity_category=EntityCategory.DIAGNOSTIC,
                        has_entity_name=True,
                        native_value=chip_model.upper(),
                        events={},
                        icon="mdi:cpu-32-bit",
                    ),
                    data=data,
                )
            )
        if "hardwareProfile" in login_context:
            new_entities.append(
                ESPSomfyDiagSensor(
                    controller=controller,
                    cfg=ESPSomfyDiagSensorDescription(
                        key="hardware_profile",
                        translation_key="hardware_profile",
                        entity_category=EntityCategory.DIAGNOSTIC,
                        has_entity_name=True,
                        native_value=login_context["hardwareProfile"],
                        events={},
                        icon="mdi:chip",
                    ),
                    data=data,
                )
            )
        if "resetReason" in login_context:
            new_entities.append(
                ESPSomfyDiagSensor(
                    controller=controller,
                    cfg=ESPSomfyDiagSensorDescription(
                        key="reset_reason",
                        translation_key="reset_reason",
                        entity_category=EntityCategory.DIAGNOSTIC,
                        has_entity_name=True,
                        native_value=login_context["resetReason"],
                        events={},
                        icon="mdi:restart-alert",
                    ),
                    data=data,
                )
            )
        # uptime/netUptime are seconds-since-boot counters with no live socket event to keep
        # them ticking (see fetch_login_context()'s docstring - MQTT publishes "uptime", the
        # socket never does). Exposed as a fixed boot/connect TIMESTAMP instead of a duration:
        # HA renders that as a self-updating "X ago" without needing any further pushes, and it
        # only actually goes stale if the device reboots without HA restarting too - the closest
        # to correct achievable without adding a new socket event for this.
        if "uptime" in login_context:
            new_entities.append(
                ESPSomfyDiagSensor(
                    controller=controller,
                    cfg=ESPSomfyDiagSensorDescription(
                        key="boot_time",
                        translation_key="boot_time",
                        entity_category=EntityCategory.DIAGNOSTIC,
                        has_entity_name=True,
                        device_class=SensorDeviceClass.TIMESTAMP,
                        native_value=dt_util.utcnow()
                        - timedelta(seconds=login_context["uptime"]),
                        events={},
                        icon="mdi:clock-start",
                    ),
                    data=data,
                )
            )
        if "netUptime" in login_context:
            new_entities.append(
                ESPSomfyDiagSensor(
                    controller=controller,
                    cfg=ESPSomfyDiagSensorDescription(
                        key="net_connected_since",
                        translation_key="net_connected_since",
                        entity_category=EntityCategory.DIAGNOSTIC,
                        has_entity_name=True,
                        device_class=SensorDeviceClass.TIMESTAMP,
                        native_value=dt_util.utcnow()
                        - timedelta(seconds=login_context["netUptime"]),
                        events={},
                        icon="mdi:lan-connect",
                    ),
                    data=data,
                )
            )
        if "connType" in data:
            new_entities.append(
                ESPSomfyDiagSensor(
                    controller=controller,
                    cfg=ESPSomfyDiagSensorDescription(
                        key="conn_type",
                        translation_key="conn_type",
                        entity_category=EntityCategory.DIAGNOSTIC,
                        has_entity_name=True,
                        events={},
                        native_value=data["connType"],
                        icon="mdi:connection",
                    ),
                    data=data,
                )
            )
            if data["connType"] == "Wifi":
                new_entities.append(ESPSomfyWifiStrengthSensor(controller=controller, data=data))
                new_entities.append(
                    ESPSomfyDiagSensor(
                        controller=controller,
                        cfg=ESPSomfyDiagSensorDescription(
                            key="wifi_ssid",
                            translation_key="wifi_ssid",
                            entity_category=EntityCategory.DIAGNOSTIC,
                            has_entity_name=True,
                            icon="mdi:wifi-cog",
                            native_value=data.get("ssid", ""),
                            events={EVT_WIFISTRENGTH: "ssid"},
                        ),
                        data=data,
                    )
                )
                new_entities.append(
                    ESPSomfyDiagSensor(
                        controller=controller,
                        cfg=ESPSomfyDiagSensorDescription(
                            key="wifi_channel",
                            translation_key="wifi_channel",
                            entity_category=EntityCategory.DIAGNOSTIC,
                            has_entity_name=True,
                            icon="mdi:radio-tower",
                            events={EVT_WIFISTRENGTH: "channel"},
                        ),
                        data=data,
                    )
                )
            elif data["connType"] == "Ethernet":
                new_entities.append(
                    ESPSomfyDiagSensor(
                        controller=controller,
                        cfg=ESPSomfyDiagSensorDescription(
                            key="eth_speed",
                            translation_key="eth_speed",
                            entity_category=EntityCategory.DIAGNOSTIC,
                            has_entity_name=True,
                            state_class=SensorStateClass.MEASUREMENT,
                            device_class=SensorDeviceClass.DATA_RATE,
                            unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
                            icon="mdi:lan-connect",
                            native_value=data.get("speed", 100),
                            events={EVT_ETHERNET: "speed"},
                        ),
                        data=data,
                    )
                )
                new_entities.append(
                    ESPSomfyDiagSensor(
                        controller=controller,
                        cfg=ESPSomfyDiagSensorDescription(
                            key="eth_full_duplex",
                            translation_key="eth_full_duplex",
                            entity_category=EntityCategory.DIAGNOSTIC,
                            has_entity_name=True,
                            icon="mdi:sync",
                            native_value=bool(data.get("fullduplex", True)),
                            events={EVT_ETHERNET: "fullduplex"},
                        ),
                        data=data,
                    )
                )
        if "memory" in data:
            mem = data["memory"]
            if "free" in mem:
                new_entities.append(
                    ESPSomfyDiagSensor(
                        controller=controller,
                        cfg=ESPSomfyDiagSensorDescription(
                            key="free_memory",
                            translation_key="free_memory",
                            entity_category=EntityCategory.DIAGNOSTIC,
                            has_entity_name=True,
                            device_class=SensorDeviceClass.DATA_SIZE,
                            icon="mdi:memory",
                            state_class=SensorStateClass.MEASUREMENT,
                            unit_of_measurement=UnitOfInformation.BYTES,
                            suggested_display_precision=0,
                            native_value=mem["free"],
                            min_interval=25,
                            value_count=15,
                            events={EVT_MEMSTATUS: "free"},
                        ),
                        data=data,
                    )
                )
            if "max" in mem:
                new_entities.append(
                    ESPSomfyDiagSensor(
                        controller=controller,
                        cfg=ESPSomfyDiagSensorDescription(
                            key="max_memory",
                            translation_key="max_memory",
                            entity_category=EntityCategory.DIAGNOSTIC,
                            has_entity_name=True,
                            icon="mdi:memory",
                            device_class=SensorDeviceClass.DATA_SIZE,
                            unit_of_measurement=UnitOfInformation.BYTES,
                            state_class=SensorStateClass.MEASUREMENT,
                            suggested_display_precision=0,
                            native_value=mem["max"],
                            min_interval=30,
                            value_count=15,
                            events={EVT_MEMSTATUS: "max"},
                        ),
                        data=data,
                    )
                )
            if "min" in mem:
                new_entities.append(
                    ESPSomfyDiagSensor(
                        controller=controller,
                        cfg=ESPSomfyDiagSensorDescription(
                            key="min_memory",
                            translation_key="min_memory",
                            entity_category=EntityCategory.DIAGNOSTIC,
                            has_entity_name=True,
                            icon="mdi:memory",
                            device_class=SensorDeviceClass.DATA_SIZE,
                            min_interval=30,
                            value_count=15,
                            unit_of_measurement=UnitOfInformation.BYTES,
                            state_class=SensorStateClass.MEASUREMENT,
                            suggested_display_precision=0,
                            native_value=mem["min"],
                            events={EVT_MEMSTATUS: "min"},
                        ),
                        data=data,
                    )
                )

        new_entities.append(
            ESPSomfyDiagSensor(
                controller=controller,
                cfg=ESPSomfyDiagSensorDescription(
                    key="ip_addresss",
                    translation_key="ip_addresss",
                    entity_category=EntityCategory.DIAGNOSTIC,
                    has_entity_name=True,
                    icon="mdi:ip",
                    events={},
                    native_value=controller.api.get_data()["host"],
                ),
                data=data,
            )
        )

    # /discovery always masks linkedRemotes (see the firmware's WebSystem.cpp), so the only way
    # to know how many physical remotes are actually linked to each shade - and to build one
    # sensor per remote rather than one that conflates all of them - is the authenticated
    # /shades REST call. This also refreshes controller.api.shades with the unmasked data,
    # which only helps the rest of the integration (remoteAddress/linkedRemotes go from
    # always-zeroed to their real values instead of waiting on the first live event).
    # A transient failure here must not take down the whole sensor platform (host_name,
    # memory, etc. below have nothing to do with linked remotes) - degrade to zero
    # remote-RSSI sensors instead.
    # 3.x seulement : la 2.x ne remplit pas lastRssi, le capteur resterait à Inconnu.
    if controller.api.supports_remote_rssi:
        try:
            shades_full = await controller.api.load_shades() or []
        except aiohttp.ClientError:
            shades_full = []
        for shade in shades_full:
            remotes = shade.get("linkedRemotes") or []
            for index, remote in enumerate(remotes):
                new_entities.append(
                    ESPSomfyRemoteRssiSensor(
                        controller=controller, shade=shade, remote=remote, index=index
                    )
                )

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
                ESPSomfyScheduleNextTimeSensor(
                    controller=controller,
                    schedule=schedule,
                    target=target,
                    time_label=schedule_time_label(schedule, sun_labels),
                )
            )

    if new_entities:
        async_add_entities(new_entities)


class ESPSomfyDiagSensor(ESPSomfyEntity, SensorEntity):
    """A diagnostic entity for the hub."""

    def __init__(
        self, *, controller: ESPSomfyController, cfg: ESPSomfyDiagSensorDescription, data: any
    ) -> None:
        """Initialize a new diagnostic sensor."""
        super().__init__(data=data, controller=controller)
        self._controller = controller
        self._available = True
        self.events = {}

        self._attr_entity_category = cfg.entity_category
        self._attr_unique_id = f"{cfg.key}_{controller.unique_id}"
        self.entity_description = cfg
        self.events = cfg.events
        self._attr_native_value = cfg.native_value

        self._attr_has_entity_name = cfg.has_entity_name

        self._attr_native_unit_of_measurement = cfg.unit_of_measurement
        self._attr_device_class = cfg.device_class
        self._attr_state_class = cfg.state_class

        self._last_recorded = time.time()
        self._min_interval = cfg.min_interval
        self._value_count = cfg.value_count
        self._values = []

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.registry_entry.disabled:
            return
        if (
            "event" in self._controller.data
            and self._controller.data["event"] in self.events
        ):
            evt = self.events[self._controller.data["event"]]
            if evt in self._controller.data:
                self._values.append(self._controller.data[evt])
                val = self._controller.data[evt]
                if (
                    time.time() > self._last_recorded + self._min_interval
                    or len(self._values) >= self._value_count
                    or not self._available
                ):
                    self._available = True
                    if self._value_count > 1:
                        val = int(statistics.median(self._values))
                    self._values.clear()
                    self._last_recorded = time.time()
                    if val != self._attr_native_value:
                        self._attr_native_value = val
                        self.async_write_ha_state()
                elif self._attr_native_value is None:
                    self._attr_native_value = val
                    self.async_write_ha_state()
        elif (
            self._controller.data["event"] == EVT_CONNECTED
            and "connected" in self._controller.data
        ):
            if self._available != bool(self._controller.data["connected"]):
                self._available = bool(self._controller.data["connected"])
                self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Indicates whether the sensor is available."""
        return self._available


class ESPSomfyWifiStrengthSensor(ESPSomfyDiagSensor):
    """A wifi strength sensor indicating the current connection strength."""

    def __init__(self, *, controller: ESPSomfyController, data: any) -> None:
        """Initialize a new Wifi strength Sensor."""
        super().__init__(
            controller=controller,
            cfg=ESPSomfyDiagSensorDescription(
                key="wifi_sensor",
                translation_key="wifi_sensor",
                has_entity_name=True,
                device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                entity_category=EntityCategory.DIAGNOSTIC,
                unit_of_measurement="dBm",
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:wifi",
                min_interval=30,
                value_count=15,
                events={EVT_WIFISTRENGTH: "strength"},
            ),
            data=data,
        )
        self._available = True

    @property
    def should_poll(self) -> bool:
        """Indicates that the sensor should not poll."""
        return False


class ESPSomfyRemoteRssiSensor(ESPSomfyEntity, SensorEntity):
    """RSSI of one physical remote linked to a shade.

    One entity per entry in linkedRemotes, identified by that remote's own address so a
    second (or third...) linked remote gets its own sensor instead of the two conflating
    into one value. The "Emitter N" label mirrors the firmware's own UI convention
    (data-dev/js/70-somfy.js, REMOTESLIST_LINKED: array position, 1-based) rather than a
    stable slot number, since that's what a user comparing against the device UI expects to
    match - it can drift if a remote is unlinked and HA isn't restarted before a new one is
    added, same as it would in the firmware's own UI.
    """

    # SomfyLinkedRemote::RSSI_UNKNOWN (firmware) - "no frame received since boot", not a real
    # reading. Left as -128, a sensor reads as a genuine (if implausibly weak) signal instead
    # of "unknown", which spikes the history graph every time the device reboots or a remote
    # is freshly linked and hasn't been pressed yet.
    _RSSI_UNKNOWN = -128

    _attr_has_entity_name = True
    _attr_translation_key = "remote_rssi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(
        self, *, controller: ESPSomfyController, shade: any, remote: any, index: int
    ) -> None:
        """Initialize a new remote RSSI sensor."""
        super().__init__(data=shade, controller=controller)
        self._controller = controller
        self._shade_id = shade["shadeId"]
        self._remote_address = remote["remoteAddress"]
        self._attr_unique_id = (
            f"{controller.unique_id}_{self._shade_id}_remote_rssi_{self._remote_address}"
        )
        self._attr_translation_placeholders = {"index": str(index + 1)}
        self._available = True
        self._attr_native_value = self._clean(remote.get("lastRssi"))

    @classmethod
    def _clean(cls, rssi: int | None) -> int | None:
        """Map the firmware's RSSI_UNKNOWN sentinel to None ("unknown")."""
        return None if rssi is None or rssi == cls._RSSI_UNKNOWN else rssi

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        d = self._controller.data
        evt = d.get("event", "")
        if evt == EVT_CONNECTED:
            if "connected" in d and self._available != bool(d["connected"]):
                self._available = bool(d["connected"])
                self.async_write_ha_state()
            return
        if d.get("shadeId") != self._shade_id:
            return
        rssi = None
        found = False
        if evt in (EVT_SHADESTATE, EVT_SHADEADDED) and "linkedRemotes" in d:
            for remote in d["linkedRemotes"] or []:
                if remote.get("remoteAddress") == self._remote_address:
                    rssi = remote.get("lastRssi")
                    found = True
                    break
        elif evt == EVT_SHADECOMMAND and d.get("source") == "remote":
            # shadeCommand carries a flat lastRssi for the frame that triggered it - unlike
            # shadeState, it fires for every button press. Trust it only for "remote" (a real
            # RF reception): an "internal" (HA-triggered) command echoes the last real frame's
            # rssi, which is 0 - not RSSI_UNKNOWN - before any physical press has ever happened.
            if d.get("sourceAddress") == self._remote_address and "lastRssi" in d:
                rssi = d["lastRssi"]
                found = True
        if not found:
            return
        rssi = self._clean(rssi)
        if rssi != self._attr_native_value:
            self._attr_native_value = rssi
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Indicates whether the sensor is available."""
        return self._available


class ESPSomfyScheduleNextTimeSensor(ESPSomfyEntity, SensorEntity):
    """Next trigger time ("HH:MM") for one schedule, attached to its target shade/group.

    The live scheduleState event is the only source for a sunrise/sunset schedule (the
    firmware resolves that against the current day's solar times) - those stay "unknown"
    until the first one arrives, same as the RSSI sensors above. A "clock" schedule doesn't
    need that: hour/minute are already the answer, and /schedules (REST, used to build these
    entities at startup) has them, so it's seeded immediately instead of waiting on an event
    that only fires when someone happens to edit the schedule.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "schedule_next_time"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-outline"
    _attr_should_poll = False

    def __init__(
        self,
        *,
        controller: ESPSomfyController,
        schedule: any,
        target: any,
        time_label: str = "",
    ) -> None:
        """Initialize a new schedule next-run sensor."""
        super().__init__(data=target, controller=controller)
        self._controller = controller
        self._schedule_id = schedule["id"]
        self._attr_unique_id = (
            f"{controller.unique_id}_schedule_{self._schedule_id}_next_time"
        )
        self._attr_translation_placeholders = {
            "name": schedule.get("name") or str(self._schedule_id),
            "time": time_label,
        }
        self._available = True
        self._attr_native_value = None
        if schedule.get("timeRef") == "clock":
            hour, minute = schedule.get("hour"), schedule.get("minute")
            if hour is not None and minute is not None:
                self._attr_native_value = f"{hour:02d}:{minute:02d}"

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
            value = d.get("nextTime")
            if value != self._attr_native_value:
                self._attr_native_value = value
                self.async_write_ha_state()
        else:
            self._available = False
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Indicates whether the sensor is available."""
        return self._available
