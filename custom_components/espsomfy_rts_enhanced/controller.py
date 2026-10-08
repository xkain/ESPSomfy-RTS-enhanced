"""Controller for all the devices."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import os
import threading
from threading import Timer
from typing import Any
from urllib.parse import quote

import aiofiles
import aiohttp
from packaging.version import InvalidVersion, parse as version_parse
import websocket

from homeassistant.components.cover import CoverDeviceClass, CoverEntityFeature
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PIN, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    aiohttp_client,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    API_DISCOVERY,
    API_GROUPCOMMAND,
    API_GROUPS,
    API_LOGIN,
    API_SAVESCHEDULE,
    API_SCHEDULES,
    API_SETPOSITIONS,
    API_SETSENSOR,
    API_SHADECOMMAND,
    API_SHADES,
    API_TILTCOMMAND,
    DOMAIN,
    EVT_CONNECTED,
    EVT_ETHERNET,
    EVT_FWSTATUS,
    EVT_GROUPSTATE,
    EVT_MEMSTATUS,
    EVT_SCHEDULEREMOVED,
    EVT_SCHEDULESTATE,
    EVT_SHADEADDED,
    EVT_SHADECOMMAND,
    EVT_SHADEREMOVED,
    EVT_SHADESTATE,
    EVT_UPDPROGRESS,
    EVT_WIFISTRENGTH,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)
logging.getLogger("websocket").setLevel(logging.CRITICAL)


class SocketListener(threading.Thread):
    """A listener of sockets."""

    def __init__(
        self, hass: HomeAssistant, url, onpacket, onopen, onclose, onerror
    ) -> None:
        """Initialize a new socket listener.

        `url` may be a string or a callable returning one. It is a callable here so that
        every reconnection re-reads it: the handshake carries the api key as a query
        parameter, and that key is HMAC(secret, "<credentials>:<client IP>"). A key frozen
        at construction goes stale the moment the PIN changes on the device -- which also
        revokes the open sessions -- or Home Assistant's DHCP lease moves. The reconnect
        loop then retried for ever with a dead key, silently, until the config entry was
        reloaded.
        """
        super().__init__()
        self._url = url
        self.onpacket = onpacket
        self.onopen = onopen
        self.onclose = onclose
        self.onerror = onerror
        self.connected = False
        self.main_loop = None
        self.ws_app = None
        self.hass = hass
        self._should_stop = False
        self.filter = None
        self.running_future = None
        self.reconnects = 0
        self._connect_timer = None

    @property
    def url(self) -> str:
        """Resolve the socket url, freshly, at each connection attempt."""
        return self._url() if callable(self._url) else self._url

    def __enter__(self):
        """Start the thread."""
        self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop and join the thread."""
        self.stop()

    def stop(self):
        """Cancel event stream and join the thread."""
        _LOGGER.debug("Stopping event thread")
        self._should_stop = True
        if self.ws_app:  # and self.connected:
            self.ws_app.close()

        _LOGGER.debug("Joining event thread")
        if self.is_alive():
            self.join()
        _LOGGER.debug("Event thread joined")

    async def connect(self):
        """Start up the web socket."""
        self.main_loop = asyncio.get_event_loop()
        self.ws_app = websocket.WebSocketApp(
            self.url,
            on_message=self.ws_onmessage,
            on_error=self.ws_onerror,
            on_close=self.ws_onclose,
            on_open=self.ws_onopen,
            keep_running=True,
        )
        self.main_loop.run_in_executor(None, self.ws_begin)

    def reconnect(self):
        """Reconnect to the web socket."""
        if self._connect_timer is not None:
            self._connect_timer.cancel()
        self.reconnects = self.reconnects + 1
        self.main_loop = asyncio.get_event_loop()
        try:
            self.ws_app = websocket.WebSocketApp(
                self.url,
                on_message=self.ws_onmessage,
                on_error=self.ws_onerror,
                on_close=self.ws_onclose,
                on_open=self.ws_onopen,
                keep_running=True,
            )
            self.main_loop.run_in_executor(None, self.ws_begin)
            self._connect_timer = None
            self.connected = True

        except websocket.WebSocketAddressException:
            self._connect_timer = Timer(
                min(10 * self.reconnects / 2, 20), self.reconnect
            )
            self._connect_timer.start()
        except websocket.WebSocketTimeoutException:
            self._connect_timer = Timer(
                min(10 * self.reconnects / 2, 20), self.reconnect
            )
            self._connect_timer.start()
        except websocket.WebSocketConnectionClosedException:
            self._connect_timer = Timer(
                min(10 * self.reconnects / 2, 20), self.reconnect
            )
            self._connect_timer.start()

    def set_filter(self, arr: Any) -> None:
        """Filter for the events."""
        self.filter = arr.copy()

    def close(self) -> None:
        """Synonym for stop."""
        self.stop()

    def ws_begin(self) -> None:
        """Begin the socket."""
        self.running_future = self.ws_app.run_forever(ping_interval=20, ping_timeout=15)
        # print("Fell out of run_runforever")
        if not self._should_stop:
            self.hass.loop.call_soon_threadsafe(self.reconnect)

    def ws_onerror(self, wsapp, exception):
        """Socket error."""
        # print(f"We have an error {exception}")
        self.hass.loop.call_soon_threadsafe(self.onerror, exception)

    def ws_onclose(self, wsapp, status, msg):
        """Socket closed."""
        # print(f"The socket was closed {status}")
        self.connected = False
        if not self._should_stop:
            self.hass.loop.call_soon_threadsafe(self.onclose)

    def ws_onopen(self, wsapp):
        """Open the socket."""
        self.connected = True
        self.hass.loop.call_soon_threadsafe(self.onopen)

    def ws_onmessage(self, wsapp, message: str):
        """Process the incoming message."""
        try:
            if message is None:
                _LOGGER.debug("Got an empty socket payload")
            elif message.startswith("42["):
                ndx = message.find(",")
                event = message[3:ndx]
                if not self.filter or event in self.filter:
                    payload = message[ndx + 1 : -1]
                    # print(f"Event:{event} Payload:{payload}")
                    data = json.loads(payload)
                    data["event"] = event
                    self.hass.loop.call_soon_threadsafe(self.onpacket, data)
            elif message.lower() == "connected":
                self.reconnects = 0
                self.connected = True
        except Exception as e:
            _LOGGER.debug(e.message)
            raise


class ESPSomfyController(DataUpdateCoordinator):
    """Data coordinator/controller for receiving from espsomfy_RTS."""

    def __init__(self, config_entry_id, hass: HomeAssistant, api: ESPSomfyAPI) -> None:
        """Initialize data coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=DOMAIN,
            # The setting below is only for polling.
            # update_interval=timedelta(seconds=5),
        )
        self.config_entry_id = config_entry_id
        self.api = api
        self.ws_listener = None
        # Un seul renouvellement de clé par série d'échecs, réarmé à la réouverture.
        self._reauth_tried = False

    @property
    def device_name(self) -> str:
        """Get the device name from the host."""
        return self.api.deviceName

    @property
    def server_id(self) -> str:
        """Get the server id from the api."""
        return self.api.server_id

    @property
    def unique_id(self) -> str:
        """Get a unique id for the controller."""
        return f"espsomfy_{self.server_id}"

    @property
    def model(self) -> str:
        """Get the model for the controller."""
        return self.api.model

    @property
    def version(self) -> str:
        """Get the current version for the controller."""
        return self.api.version

    @property
    def latest_version(self) -> str:
        """Get the latest version for the controller."""
        return self.api.latest_version

    @property
    def check_for_update(self) -> bool:
        """Indicate whether the firmware should check for updates."""
        return self.api.check_for_update

    @property
    def internet_available(self) -> bool:
        """Indicates whether the ESPSomfy-RTS hardware has internet access."""
        return self.api.internet_available

    @property
    def can_update(self) -> bool:
        """Get a flag that indicates whether the firmware can be updated."""
        return self.api.can_update

    async def ws_close(self) -> None:
        """Close the tasks and sockets."""
        if self.ws_listener is not None:
            self.ws_listener.close()

    async def ws_connect(self):
        """Connect to WebSocket."""
        if self.ws_listener is not None:
            self.ws_listener.close()
        self.ws_listener = SocketListener(
            self.hass,
            # La méthode, pas son résultat : rappelée à chaque reconnexion.
            self.api.get_sock_url,
            self.ws_onpacket,
            self.ws_onopen,
            self.ws_onclose,
            self.ws_onerror,
        )
        self.ws_listener.set_filter(
            [
                EVT_CONNECTED,
                EVT_SHADEADDED,
                EVT_SHADEREMOVED,
                EVT_SHADESTATE,
                EVT_SHADECOMMAND,
                EVT_GROUPSTATE,
                EVT_FWSTATUS,
                EVT_UPDPROGRESS,
                EVT_WIFISTRENGTH,
                EVT_ETHERNET,
                EVT_MEMSTATUS,
                EVT_SCHEDULESTATE,
                EVT_SCHEDULEREMOVED,
            ]
        )
        await self.ws_listener.connect()

    async def create_backup(self) -> bool:
        """Create a backup of the configuration and stores it in HA."""
        return await self.api.create_backup()

    async def update_firmware(self, version) -> bool:
        """Start the firmware update process."""
        return await self.api.update_firmware(version)

    async def fetch_release_notes(self, version: str) -> str | None:
        """Passerelle pour récupérer les notes de version."""
        return await self.api.fetch_release_notes(version)


    async def set_host(self, host) -> None:
        """Set a host name and reloads the sockets if the host has changed."""
        if self.api.get_host() != host:
            # Tear down the socket
            self.api.set_host(host)
            self.ws_connect()

    def ensure_group_configured(self, data):
        """Ensure the group exists on Home Assistant."""
        uuid = f"{self.unique_id}_group{data['groupId']}"
        entities = er.async_get(self.hass)

        for entity in er.async_entries_for_config_entry(entities, self.config_entry_id):
            if entity.unique_id == uuid:
                return

        dev_features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )
        dev_class = CoverDeviceClass.SHADE

        # Pas de device_id forcé : HA crée l'appareil depuis le DeviceInfo.
        entities.async_get_or_create(
            domain=DOMAIN,
            platform=Platform.COVER,
            original_device_class=dev_class,
            unique_id=uuid,
            original_name=data["name"],
            suggested_object_id=f"{str(data['name']).lower().replace(' ', '_')}",
            supported_features=dev_features,
        )

    def ensure_shade_configured(self, data):
        """Ensure the shade exists on Home Assistant."""
        uuid = f"{self.unique_id}_{data['shadeId']}"
        entities = er.async_get(self.hass)

        for entity in er.async_entries_for_config_entry(entities, self.config_entry_id):
            if entity.unique_id == uuid:
                return

        dev_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

        dev_class = CoverDeviceClass.SHADE
        if "shadeType" in data:
            match int(data["shadeType"]):
                case 1:
                    dev_class = CoverDeviceClass.BLIND
                    if "tiltType" in data:
                        match int(data["tiltType"]):
                            case 1 | 2:
                                dev_features |= (
                                    CoverEntityFeature.OPEN_TILT
                                    | CoverEntityFeature.CLOSE_TILT
                                    | CoverEntityFeature.SET_TILT_POSITION
                                )
                    elif "hasTilt" in data and data["hasTilt"] is True:
                        dev_features |= (
                            CoverEntityFeature.OPEN_TILT
                            | CoverEntityFeature.CLOSE_TILT
                            | CoverEntityFeature.SET_TILT_POSITION
                        )
                case 2 | 7 | 8:
                    dev_class = CoverDeviceClass.CURTAIN
                case 3:
                    dev_class = CoverDeviceClass.AWNING
                case _:
                    dev_class = CoverDeviceClass.SHADE

        # Pas de device_id forcé : HA crée l'appareil dédié.
        entities.async_get_or_create(
            domain=DOMAIN,
            platform=Platform.COVER,
            original_device_class=dev_class,
            unique_id=uuid,
            original_name=data["name"],
            suggested_object_id=f"{str(data['name']).lower().replace(' ', '_')}",
            supported_features=dev_features,
        )

    def ws_onpacket(self, data):
        """Packet from the websocket."""
        # Below doesn't work.  Near as I can tell there is no
        # real way of adding an entity on the fly.  All this
        # does is add an entity that is not really attached.
        # if data["event"] == EVT_SHADEADDED:
        #    self.ensure_shade_configured(data)

        # Catch the fwStatus messages before they go anywhere
        # this will allow us to simply update the latest firmware
        if "event" in data and data["event"] == EVT_FWSTATUS:
            self.api.set_firmware(data)

        self.async_set_updated_data(data=data)

    def ws_onopen(self):
        """Websocket is opened."""
        # Réarmé, sinon une clé périmée plus tard ne serait jamais rattrapée.
        self._reauth_tried = False
        _LOGGER.debug("ESPSomfy-RTS Socket was opened")
        if self.api.is_configured:
            _LOGGER.debug("ESPSomfy-RTS Already Configured")
            data = {"event": EVT_CONNECTED, "connected": True}
            self.async_set_updated_data(data=data)

    def ws_onerror(self, exception):
        """Error on the socket connection."""
        data = {"event": EVT_CONNECTED, "connected": False}
        self.async_set_updated_data(data=data)

    def ws_onclose(self):
        """Socket closed."""
        data = {"event": EVT_CONNECTED, "connected": False}
        self.async_set_updated_data(data=data)
        # Fermetures répétées : clé de poignée de main probablement périmée, on la
        # renouvelle une fois. Le seuil évite un /login à chaque coupure isolée.
        if self.ws_listener is None or self.ws_listener.reconnects < 3:
            return
        if self._reauth_tried:
            return
        self._reauth_tried = True
        self.hass.async_create_task(self._refresh_socket_key())

    async def _refresh_socket_key(self) -> None:
        """Renew the api key, then reopen the socket with it."""
        self.api.clear_api_key()
        await self.api.ensure_authenticated()
        await self.ws_connect()

class ESPSomfyAPI:
    """API for sending data to ESPSomfy-RTS hardware device."""

    def __init__(self, hass: HomeAssistant, config_entry_id, data) -> None:
        """Initialize the API."""
        self.hass = hass
        self.data = data
        self.set_host(data[CONF_HOST])
        self._config: Any = {}
        self._session = async_get_clientsession(self.hass, verify_ssl=False)
        self._authType = 0
        self._needsKey = False
        self._headers = {"apikey": ""}
        self._canLogin = False
        self._deviceName = data[CONF_HOST]
        self._can_update = False
        self._config_entry_id = config_entry_id
        self._configured = False
        # Aucun identifiant stocké : à distinguer d'« appareil injoignable », qui lui
        # mérite des reprises.
        self._needs_credentials = False
        # Identifiants présents mais refusés, typiquement périmés d'un mode précédent.
        self._bad_credentials = False

    @property
    def needs_credentials(self) -> bool:
        """True when setup failed only for want of credentials on the config entry."""
        return self._needs_credentials

    @property
    def bad_credentials(self) -> bool:
        """True when the device rejected the credentials stored on the config entry."""
        return self._bad_credentials

    @property
    def supports_remote_rssi(self) -> bool:
        """True when the firmware fills lastRssi on each entry of linkedRemotes.

        Both lines do expose linkedRemotes, but only 3.x attaches a per-remote signal level
        to it: on 2.x, SomfyLinkedRemote carries no RSSI field at all, so a sensor built on
        it would read "unknown" for ever.

        Entities are withheld ONLY when a major version below 3 has been positively
        determined. An unparseable version string keeps the original behaviour rather than
        stripping a 3.x device of its sensors over a parsing error.
        """
        reported = self._config.get("version")
        if not reported:
            # Pas encore connue : on ne retire rien.
            return True
        try:
            return version_parse(reported).major >= 3
        except InvalidVersion:
            _LOGGER.debug(
                "Unparseable firmware version %r, assuming per-remote RSSI is available",
                reported,
            )
            return True

    @property
    def shades(self) -> Any:
        """Return the state shades."""
        if "shades" in self._config:
            return self._config["shades"]
        return []

    @property
    def groups(self) -> Any:
        """Return the state groups."""
        if "groups" in self._config:
            return self._config["groups"]
        return []

    @property
    def server_id(self) -> str | None:
        """Getter for the server id."""
        if "serverId" in self._config:
            return self._config["serverId"]

    @property
    def version(self) -> str:
        """Getter for the api version."""
        if "version" in self._config:
            return self._config["version"]
        if "fwVersion" in self._config:
            return self._config["fwVersion"]
        return "0.0.0"

    @property
    def latest_version(self) -> str | None:
        """Getter for the latest version."""
        if "latest" in self._config:
            if self._config["latest"] == "":
                return None
            return self._config["latest"]
        return None

    @property
    def model(self) -> str:
        """Getter for the model number."""
        return self._config["model"]

    @property
    def apiKey(self) -> str:
        """Getter for the api key."""
        return self._config["apiKey"]

    @property
    def deviceName(self) -> str:
        """Getter for the device name."""
        return self._deviceName

    @property
    def can_update(self) -> bool:
        """Getter for whether the firmware is updatable."""
        return self._can_update

    @property
    def backup_dir(self) -> str:
        """Gets the backup directory for the device."""
        return self.hass.config.path(f"ESPSomfyRTS_{self.server_id}")

    @property
    def check_for_update(self) -> bool:
        """Check for the current update."""
        if "checkForUpdate" in self._config:
            return self._config["checkForUpdate"]
        return self._can_update

    @property
    def internet_available(self) -> bool:
        """Check to see if the ESPSomfy-RTS hardware has internet."""
        if "inetAvailable" in self._config:
            return self._config["inetAvailable"]
        return self._can_update

    @property
    def is_configured(self) -> bool:
        """Indicates whether the integration has been configured."""
        return self._configured

    def set_host(self, host) -> None:
        """Set the host for the integration."""
        self._host = host
        self._sock_url = f"ws://{self._host}:8080"
        self._api_url = f"http://{self._host}:8081"
        self._config_url = f"http://{self._host}"

    def get_host(self):
        """Get the current host."""
        return self._host

    def clear_api_key(self) -> None:
        """Drop the stored api key so the next authentication fetches a fresh one."""
        self._config.pop("apiKey", None)
        self._headers["apikey"] = ""

    def get_sock_url(self):
        """Get the socket interface url.

        v3 firmware with "Full" security rejects the handshake unless the
        apikey is present as a query parameter ("/?apikey=..."); v2.x
        firmware, which never checks for one, simply ignores it.
        """
        api_key = self._headers.get("apikey", "")
        if api_key:
            return f"{self._sock_url}/?apikey={quote(api_key, safe='')}"
        return self._sock_url

    def get_api_url(self):
        """Get that url used for api reference."""
        return self._api_url

    def get_config_url(self) -> str:
        """Get the configuration url."""
        return self._config_url

    def get_config(self):
        """Return the initial config."""
        return self._config

    def get_data(self):
        """Return the internal data."""
        return self.data

    def set_firmware(self, data) -> None:
        """Set the firmware data from the socket."""
        cver = "0.0.0"
        if "version" in self._config:
            cver = self._config["version"]
        new_ver = cver
        if "fwVersion" in data:
            new_ver = data["fwVersion"]
            if "name" in new_ver:
                new_ver = new_ver["name"]
        elif "version" in data:
            new_ver = data["version"]
        if "latest" in data:
            latest_ver = data["latest"]
            if "name" in latest_ver:
                latest_ver = latest_ver["name"]
            self._config["latest"] = latest_ver
        if "checkForUpdate" in data:
            self._config["checkForUpdate"] = data["checkForUpdate"]
        if "inetAvailable" in data:
            self._config["inetAvailable"] = data["inetAvailable"]
        if cver != new_ver:
            # print(f"Version: {cver} to {new_ver}")
            dev_registry = dr.async_get(self.hass)
            if dev := dev_registry.async_get_device_by_identifier(
                (DOMAIN, f"espsomfy_{self.server_id}"), self._config_entry_id
            ):
                dev_registry.async_update_device(dev.id, sw_version=new_ver)
        self._config["version"] = new_ver
        v = version_parse(new_ver)
        if (
            (v.major > 2)
            or (v.major == 2 and v.minor > 2)
            or (v.major == 2 and v.minor == 2 and v.micro > 0)
        ):
            self._can_update = True
        else:
            self._can_update = False

    async def check_address(self, url) -> bool:
        """Send a head to a url to check if it exists."""
        try:
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            # We don't care to do anything other than catch expections here.  If anything goes wrong there is an issue
            # with the address and we should not use it.
            pass
        return False

    async def update_firmware(self, version) -> bool:
        "Update to the latest firmware version."
        url = f"{self._api_url}/downloadFirmware?ver={version}"
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status == 200:
                return True
            _LOGGER.error(await resp.text())
        return False

    async def create_backup(self) -> bool:
        """Create a backup."""
        try:
            url = f"{self._api_url}/backup?attach=true"
            async with self._session.get(url, headers=self._headers) as resp:
                if resp.status != 200:
                    return False

                os.makedirs(self.backup_dir, exist_ok=True)

                data = await resp.text(encoding=None)
                local_dt = dt_util.as_local(datetime.now(dt_util.UTC))
                fpath = self.hass.config.path(
                    f"{self.backup_dir}/{local_dt.strftime('%Y-%m-%dT%H_%M_%S')}.backup"
                )
            async with aiofiles.open(fpath, mode="wb+") as f:
                await f.write(data.encode())
                return True
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("An error occurred while creating backup: %s", e)
            return False

    def get_backups(self) -> list[str] | None:
        """Get a list of all the available backups."""
        f: list[str] = []
        if not os.path.exists(self.backup_dir):
            return None
        files = os.listdir(self.backup_dir)
        f = [
            file
            for file in files
            if os.path.isfile(os.path.join(self.backup_dir, file))
            and file.endswith(".backup")
            and file[:1].isdigit()
        ]
        f.sort(reverse=True)
        return f

    def apply_data(self, data) -> None:
        """Apply the returned data to the configuration."""
        self._config["serverId"] = data["serverId"]
        self._config["model"] = data["model"]
        if "chipModel" in data:
            self._config["chipModel"] = data["chipModel"]
        if "connType" in data:
            self._config["connType"] = data["connType"]
        if "checkForUpdate" in data:
            self._config["checkForUpdate"] = data["checkForUpdate"]
        if "rooms" in data:
            self._config["rooms"] = data["rooms"]
        elif "rooms" not in self._config:
            self._config["rooms"] = []
        if "shades" in data:
            self._config["shades"] = data["shades"]
        elif "shades" not in self._config:
            self._config["shades"] = []
        if "groups" in data:
            self._config["groups"] = data["groups"]
        elif "groups" not in self._config:
            self._config["groups"] = []
        if "hostname" in data:
            self._config["hostname"] = data["hostname"]
            self._deviceName = data["hostname"]
        if "authType" in data:
            self._config["authType"] = data["authType"]
            self._canLogin = True
        elif "authType" not in self._config:
            self._config["authType"] = 0
            self._canLogin = False
        if "permissions" in data:
            self._config["permissions"] = data["permissions"]
        elif "permissions" not in self._config:
            self._config["permissions"] = 1
        if "memory" in data:
            self._config["memory"] = data["memory"]
        self._needsKey = False
        if self._config["authType"] > 0:
            if self._config["permissions"] != 1:
                self._needsKey = True
        self.set_firmware(data)

    def _has_credentials(self) -> bool:
        """True when the config entry actually carries something to log in with.

        An entry created while the device had no security stores only its host. Logging in
        with that empty payload cannot succeed -- the firmware answers "Invalid Pin Entry",
        which surfaces as a misleading `invalid_pin` -- and each attempt counts against the
        firmware's brute-force quota: three free tries, then a lock. A device switched to
        "Full" security answers 401 on every route, so setup retries, and each retry burned
        another attempt.
        """
        return any(self._login_payload().values())

    def _login_payload(self) -> dict[str, str]:
        """Build a login payload from the credentials stored on the config entry."""
        return {
            "username": self.data.get(CONF_USERNAME, ""),
            "password": self.data.get(CONF_PASSWORD, ""),
            "pin": self.data.get(CONF_PIN, ""),
        }

    async def discover(self) -> Any | None:
        """Discover the device on the network."""
        url = f"{self._api_url}{API_DISCOVERY}"
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                self.apply_data(data)
                return data
            if resp.status != 401:
                _LOGGER.error(await resp.text())
                raise DiscoveryError(f"{url} - {await resp.text()}")

        # v3 firmware with "Full" security requires authentication before it
        # will even answer /discovery (v2.x always answered it publicly, so
        # this branch is never taken against v2.x devices). Log in with
        # whatever credentials were provided, then retry once.
        if not self._has_credentials():
            raise DiscoveryError(
                f"{url} - authentication required but no credentials are stored"
            )
        await self._do_login(self._login_payload())
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                self.apply_data(data)
                return data
            _LOGGER.error(await resp.text())
            raise DiscoveryError(f"{url} - {await resp.text()}")

    async def fetch_login_context(self) -> Any | None:
        """Fetch /loginContext.

        hardwareProfile (and a few other device-identity fields) only live here, not on the
        port-8081 REST API this class otherwise talks to entirely - /loginContext is served by
        the main web server (port 80, self._config_url), so this is the one call that targets
        it instead of self._api_url.

        The route itself has been around for a long time - rstrouse's firmware serves it as
        far back as v2.4.7, with type/permissions/serverId/version/model/hostname - but the
        hardwareProfile, resetReason, uptime and netUptime fields are specific to 3.x. Callers
        therefore test for each field's presence rather than for a version, and a non-200 stays
        at debug level: old firmware, or a missing route, is not a fault.
        """
        url = f"{self._config_url}/loginContext"
        try:
            async with self._session.get(url, headers=self._headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                _LOGGER.debug(await resp.text())
        except aiohttp.ClientError as e:
            _LOGGER.error("Error fetching /loginContext: %s", e)
        return None

    async def load_shades(self) -> Any | None:
        """Load all the shades from the controller."""
        async with self._session.get(
            f"{self._api_url}{API_SHADES}", headers=self._headers
        ) as resp:
            if resp.status == 200:
                self._config["shades"] = await resp.json()
                return self._config["shades"]
            _LOGGER.error(await resp.text())

    async def load_groups(self) -> Any | None:
        """Load all the groups from the controller."""
        async with self._session.get(
            f"{self._api_url}{API_GROUPS}", headers=self._headers
        ) as resp:
            if resp.status == 200:
                self._config["groups"] = await resp.json()
                return self._config["groups"]
            _LOGGER.error(await resp.text())

    async def load_schedules(self) -> Any | None:
        """Load all the schedules from the controller.

        /schedules only exists from firmware v3.0.0 onward. On the 2.x line the route is
        simply absent, so a 404 here is the expected shape of "this device has no
        schedules" - not a fault. Logging it as an error surfaced a red "custom
        integration error" in the UI at every start, twice over, since both the switch
        and the sensor platform call this. Same treatment as fetch_login_context().
        """
        async with self._session.get(
            f"{self._api_url}{API_SCHEDULES}", headers=self._headers
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            if resp.status == 404:
                _LOGGER.debug("No /schedules route on %s: firmware predates v3", self._host)
            else:
                _LOGGER.error(await resp.text())
        return None

    async def set_schedule_enabled(self, schedule_id: int, enabled: bool) -> None:
        """Enable or disable a schedule.

        /schedule (API_SCHEDULES' single-item sibling) is GET-only by design - the write path
        is the separate /saveSchedule. A partial payload is enough: ScheduleRule::fromJSON()
        (firmware) only mutates the fields present in the body, same contract already relied
        on by MQTT's schedules/<id>/enabled/set.
        """
        async with self._session.put(
            f"{self._api_url}{API_SAVESCHEDULE}",
            json={"id": schedule_id, "enabled": enabled},
            headers=self._headers,
        ) as resp:
            if resp.status != 200:
                _LOGGER.error(await resp.text())

    async def tilt_open(self, shade_id: int):
        """Send the command to open the tilt."""
        await self.tilt_command({"shadeId": shade_id, "command": "up"})

    async def tilt_close(self, shade_id: int):
        """Send the command to close the tilt."""
        await self.tilt_command({"shadeId": shade_id, "command": "down"})

    async def position_tilt(self, shade_id: int, position: int):
        """Send the command to position the shade."""
        # print(f"Setting tilt position to {position}")
        await self.tilt_command({"shadeId": shade_id, "target": position})

    async def sun_flag_off(self, shade_id: int):
        """Send the command to turn off the sun flag."""
        await self.shade_command({"shadeId": shade_id, "command": "flag"})

    async def sun_flag_on(self, shade_id: int):
        """Send the command to turn off the sun flag."""
        await self.shade_command({"shadeId": shade_id, "command": "sunflag"})

    async def sun_flag_group_off(self, group_id: int):
        """Send the command to turn off the sun flag."""
        await self.group_command({"groupId": group_id, "command": "flag"})

    async def sun_flag_group_on(self, group_id: int):
        """Send the command to turn off the sun flag."""
        await self.group_command({"groupId": group_id, "command": "sunflag"})

    async def open_shade(self, shade_id: int):
        """Send the command to open the shade."""
        await self.shade_command({"shadeId": shade_id, "command": "up"})

    async def close_shade(self, shade_id: int):
        """Send the command to close the shade."""
        await self.shade_command({"shadeId": shade_id, "command": "down"})

    async def toggle_shade(self, shade_id: int):
        """Sent the command to toggle."""
        await self.shade_command({"shadeId": shade_id, "command": "toggle"})

    async def stop_shade(self, shade_id: int):
        """Send the command to stop the shade."""
        # print(f"STOP ShadeId:{shade_id}")
        await self.shade_command({"shadeId": shade_id, "command": "my"})

    async def open_group(self, group_id: int):
        """Send the command to open the group."""
        await self.group_command({"groupId": group_id, "command": "up"})

    async def close_group(self, group_id: int):
        """Send the command to close the group."""
        await self.group_command({"groupId": group_id, "command": "down"})

    async def stop_group(self, group_id: int):
        """Send the command to stop the group."""
        await self.group_command({"groupId": group_id, "command": "my"})

    async def position_shade(self, shade_id: int, position: int):
        """Send the command to position the shade."""
        await self.shade_command({"shadeId": shade_id, "target": position})

    async def raw_command(self, shade_id: int, command: str, repeat: int):
        """Send the command to the shade."""
        await self.shade_command(
            {"shadeId": shade_id, "command": command, "repeat": repeat}
        )

    async def shade_command(self, data):
        """Send commands to ESPSomfy-RTS via PUT request."""
        await self.put_command(API_SHADECOMMAND, data)

    async def set_current_position(self, shade_id: int, position: int):
        """Set the current position without moving the motor."""
        await self.put_command(
            API_SETPOSITIONS, {"shadeId": shade_id, "position": position}
        )

    async def set_current_tilt_position(self, shade_id: int, tilt_position: int):
        """Set the current tilt position without moving the motor."""
        await self.put_command(
            API_SETPOSITIONS, {"shadeId": shade_id, "tiltPosition": tilt_position}
        )

    async def set_sunny(self, shade_id: int, sunny: bool):
        """Set the sunny condition for the motor."""
        await self.put_command(API_SETSENSOR, {"shadeId": shade_id, "sunny": sunny})

    async def set_windy(self, shade_id: int, windy: bool):
        """Set the windy condition for the motor."""
        await self.put_command(API_SETSENSOR, {"shadeId": shade_id, "windy": windy})

    async def put_command(self, command, data):
        """Send a put command to the device, re-authenticating once on a 401.

        The firmware's token is HMAC(secret, "<credentials>:<client IP>"), so a key that
        was valid can stop being so without anything failing visibly here: the PIN was
        changed on the device, or Home Assistant's DHCP lease moved. Without this retry
        the commands simply stopped working until the config entry was reloaded, with a
        single line in the log and no other symptom.
        """
        async with self._session.put(
            f"{self._api_url}{command}", json=data, headers=self._headers
        ) as resp:
            if resp.status == 200:
                return
            if resp.status != 401:
                _LOGGER.error(await resp.text())
                return
        # 401: the stored key is stale. Log in again and replay the command once. A second
        # failure is reported and not retried, so a wrong PIN cannot loop on /login and
        # burn through the firmware's brute-force quota.
        try:
            self._headers["apikey"] = ""
            await self._do_login(self._login_payload())
        except (aiohttp.ClientError, LoginError) as err:
            _LOGGER.error("Could not re-authenticate to replay %s: %s", command, err)
            return
        async with self._session.put(
            f"{self._api_url}{command}", json=data, headers=self._headers
        ) as retry:
            if retry.status != 200:
                _LOGGER.error(await retry.text())

    async def _do_login(self, data) -> None:
        """Actually PUT the login request and store the returned apiKey.

        Unlike login(), this is not gated on _canLogin: it is also the retry
        path discover()/get_initial() use when a v3 "Full"-security device
        answers 401 before authType is even known (see login() below).
        """
        async with self._session.put(
            f"{self._api_url}{API_LOGIN}", json=data
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                if result.get("success"):
                    if "apiKey" in result:
                        self._config["apiKey"] = self._headers["apikey"] = result[
                            "apiKey"
                        ]
                else:
                    if "type" in result:
                        if result["type"] == 1:
                            raise LoginError(CONF_PIN, "invalid_pin")
                        if result["type"] == 2:
                            raise LoginError(CONF_USERNAME, "invalid_password")
                    raise LoginError(CONF_HOST, "invalid_login")
            elif resp.status == 429:
                # The firmware's brute-force guard (WebAuth.cpp) - distinct from a real bad
                # credential so the UI can say "wait N seconds" instead of "wrong password",
                # which previously pushed people to immediately retry and re-trigger the lock.
                result = await resp.json()
                _LOGGER.error("Error logging in: %s", result)
                raise LoginError(CONF_HOST, "too_many_attempts", result.get("retryAfter", 0))
            else:
                _LOGGER.error("Error logging in: %s", await resp.text())
                raise LoginError(CONF_HOST, "login_error")

    async def login(self, data):
        """Log in to the EPSSomfy hardware device.

        No-op if discover() already bootstrapped an apiKey (v3 "Full"-security
        devices reject /discovery until authenticated, so discover() logs in
        itself) -- otherwise this would fire a second, redundant /login PUT
        right after, burning through the firmware's brute-force attempt quota
        twice as fast for no benefit.
        """
        if self._headers.get("apikey"):
            return
        if self._canLogin:
            await self._do_login(data)

    async def group_command(self, data):
        """Send commands to ESPSomfy-RTS via PUT request."""
        await self.put_command(API_GROUPCOMMAND, data)

    async def tilt_command(self, data):
        """Send tilt commands to ESPSomfy-RTS via PUT request."""
        await self.put_command(API_TILTCOMMAND, data)

    async def ensure_authenticated(self) -> None:
        """Obtain an api key when the device declares security, without waiting for a 401.

        No-op when security is disabled (authType 0) or when a key is already held -- the
        latter guard also keeps discover()'s bootstrap from firing a second, redundant
        /login right after, which would burn the firmware's brute-force quota twice as fast.
        """
        if self._headers.get("apikey"):
            return
        if int(self._config.get("authType", 0)) <= 0:
            return
        if not self._has_credentials():
            # Rien à envoyer : une tentative à vide userait le quota à chaque démarrage.
            _LOGGER.warning(
                "ESPSomfy-RTS (%s) requires authentication but no credentials are stored for "
                "this entry: state and commands keep working, configuration-level features "
                "(backup, reboot, firmware update, remote addresses) stay unavailable. "
                "Reconfigure the integration to supply the PIN or password",
                self._host,
            )
            return
        try:
            await self._do_login(self._login_payload())
        except (aiohttp.ClientError, LoginError) as err:
            # Degrade instead of failing setup: in "config only" mode the dashboard, the
            # state and the commands all keep working without a key.
            _LOGGER.warning(
                "ESPSomfy-RTS: could not authenticate, configuration-level features "
                "(backup, reboot, firmware update, remote addresses) stay unavailable: %s",
                err,
            )

    async def get_initial(self):
        """Get the initial config from ESPSomfy-RTS."""
        try:
            self._session = aiohttp_client.async_get_clientsession(self.hass)
            url = f"{self._api_url}{API_DISCOVERY}"
            async with self._session.get(url, headers=self._headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                elif resp.status == 401:
                    # Same bootstrap as discover(): a "Full"-security device rejects the
                    # unauthenticated first attempt, so log in with the credentials stored on
                    # the config entry and retry once.
                    if not self._has_credentials():
                        # Muet : async_setup_entry lève AuthFailed, l'invite suffit.
                        self._needs_credentials = True
                        return
                    try:
                        await self._do_login(self._login_payload())
                    except LoginError as err:
                        # 429 n'est pas un mauvais identifiant : verrou temporaire, on
                        # laisse les reprises faire.
                        if len(err.args) > 1 and err.args[1] != "too_many_attempts":
                            self._bad_credentials = True
                        raise
                    async with self._session.get(url, headers=self._headers) as retry_resp:
                        if retry_resp.status != 200:
                            _LOGGER.error(await retry_resp.text())
                            return
                        data = await retry_resp.json()
                else:
                    _LOGGER.error(await resp.text())
                    return

            self.apply_data(data)
            # En "config seule" rien ne répond 401 : on se connecte dès qu'une sécurité est
            # déclarée. Non fatal, état et commandes marchent sans clé.
            await self.ensure_authenticated()
            entry = self.hass.config_entries.async_get_entry(self._config_entry_id)
            if not self._configured:
                _LOGGER.debug("ESPSomfy-RTS Setting up entities")
                await self.hass.config_entries.async_forward_entry_setups(
                    entry, PLATFORMS
                )
                self._configured = True
        except (aiohttp.ClientError, LoginError) as e:
            _LOGGER.error("ESPSomfy-RTS could not authenticate or connect: %s", e)

    async def fetch_release_notes(self, version: str) -> str | None:
        """Télécharge les notes de version depuis l'API GitHub."""
        url = f"https://api.github.com/repos/xkain/ESPSomfy-RTS/releases/tags/{version}"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "HomeAssistant-ESPSomfyRTS-Integration"
        }

        # Catégorie "exceptions" : la seule que hassfest laisse en texte libre.
        # Repli sur l'anglais si la clé manque.
        prefix = f"component.{DOMAIN}.exceptions.release_notes_"
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "exceptions", integrations=[DOMAIN]
        )
        if not any(key.startswith(prefix) for key in translations):
            translations = await async_get_translations(
                self.hass, "en", "exceptions", integrations=[DOMAIN]
            )
        banner_text = translations.get(f"{prefix}firmware_banner.message", "")
        banner = f"{banner_text}\n\n---\n\n" if banner_text else ""
        no_description = translations.get(f"{prefix}no_description.message", "No description available.")
        fetch_error_status = translations.get(
            f"{prefix}fetch_error_status.message", "Unable to load release notes (GitHub code: {status})"
        )
        fetch_error_exception = translations.get(
            f"{prefix}fetch_error_exception.message", "Error while fetching release notes."
        )

        try:
            async with self._session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    # Extrait le texte au format Markdown
                    body = data.get("body", no_description)
                    return banner + body
                return fetch_error_status.format(status=response.status)
        except Exception as e:
            _LOGGER.error("Erreur notes de version : %s", e)
            return fetch_error_exception


class InvalidHost(HomeAssistantError):
    """Error to indicate that hostname/IP address is invalid."""


class DiscoveryError(HomeAssistantError):
    """Error that occurred during discovery."""


class LoginError(HomeAssistantError):
    """Error that occurs when login fails."""
