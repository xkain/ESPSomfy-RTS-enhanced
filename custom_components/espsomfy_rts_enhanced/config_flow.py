"""Config flow for ESPSomfy RTS."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.network import is_host_valid

from .const import DOMAIN
from .controller import (
    DiscoveryError,
    ESPSomfyAPI,
    ESPSomfyController,
    InvalidHost,
    LoginError,
)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, "Server Address"): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        )
    }
)

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""

    session = aiohttp_client.async_get_clientsession(hass)
    async with session.get(f'http://{data["host"]}/discovery') as resp:
        if resp.status == 200:
            pass
        else:
            raise DiscoveryError(f"{await resp.text()}")
    return {"title": "ESPSomfy-RTS", "server_id": "A1"}


# The ConfigFlow is only accessed when first setting up the integration.  This simply
# collects the initial data from the user to determine whether the integration can
# be installed.
class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configuration flow for ESPSomfyController."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self.controller: ESPSomfyController
        self.zero_conf: zeroconf.ZeroconfServiceInfo
        self.server_id = None
        self.host = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @callback
    def _show_setup_form(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, Any] | None = None,
        description_placeholders: dict[str, str] | None = None,
    ) -> FlowResult:
        """Show the setup form to the user."""
        host = user_input.get(CONF_HOST, self.host) if user_input else ""
        return self.async_show_form(
            step_id="user",
            data_schema=_get_data_schema(self.hass, data=user_input, host=host),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the flow initialized by the user."""

        errors = {}
        description_placeholders = {}
        if user_input is not None:
            try:
                if not is_host_valid(user_input.get(CONF_HOST, "")):
                    raise InvalidHost
                self.host = user_input.get(CONF_HOST, "")
                api = ESPSomfyAPI(self.hass, 0, user_input)
                await api.discover()
                await self.async_set_unique_id(f"espsomfy_{api.server_id}")
                self._abort_if_unique_id_configured(updates={CONF_HOST: self.host})
                await api.login(
                    {
                        "username": user_input.get(CONF_USERNAME, ""),
                        "password": user_input.get(CONF_PASSWORD, ""),
                        "pin": user_input.get(CONF_PIN, ""),
                    }
                )
                return self.async_create_entry(
                    title=api.deviceName,
                    description=f"ESPSomfy-RTS {api.server_id}",
                    data=user_input,
                )
            except InvalidHost:
                # wrong_host n'existe dans aucune traduction : s'affichait en brut.
                errors[CONF_HOST] = "invalid_host"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except DiscoveryError:
                errors[CONF_HOST] = "discovery_error"
            except LoginError as ex:
                errors[ex.args[0]] = ex.args[1]
                if len(ex.args) > 2:
                    description_placeholders["retry_after"] = str(ex.args[2])
            except AbortFlow:
                # Abandon volontaire : le filet ci-dessous ne doit pas l'avaler.
                raise
            except Exception:  # noqa: BLE001
                # Sinon aiohttp répond 500 : « Unknown error occurred », sans trace.
                _LOGGER.exception("Unexpected error while configuring ESPSomfy-RTS")
                errors["base"] = "unknown"
        return self._show_setup_form(
            user_input=user_input,
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zeroconf discovery."""
        self.zero_conf = discovery_info

        # Check if already configured
        self.server_id = discovery_info.properties.get("serverId", "")
        # This was part of PR#54 and was incorrect the server id is simply the chip id of the ESP32 but could
        # be replicated by other items.  If this server id is already configured then we do not want
        # to add the device again.  Since the unique id contains the server id this will always be the same
        # as identified by the chip id on the ESP32.
        # await self.async_set_unique_id(f"{self.server_id}")
        self.host = discovery_info.host
        await self.async_set_unique_id(f"espsomfy_{self.server_id}")

        self._abort_if_unique_id_configured(
            updates={CONF_HOST: discovery_info.host}, reload_on_update=True
        )
        self.context.update(
            {
                "title_placeholders": {
                    CONF_NAME: discovery_info.hostname,
                    CONF_HOST: discovery_info.host,
                    "model": discovery_info.properties.get("model", ""),
                    "configuration_url": f"http://{discovery_info.host}",
                },
            }
        )
        return await self.async_step_zeroconf_confirm()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle a re-authentication request raised by async_setup_entry.

        Reached when the device demands a key and the entry has none -- typically an entry
        created by zeroconf discovery, which only ever stores the host, on a box whose
        security was enabled afterwards.
        """
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self.host = entry_data.get(CONF_HOST, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the credentials, verify them, then reload the entry."""
        errors: dict[str, Any] = {}
        description_placeholders: dict[str, str] = {}
        # /loginContext répond sans clé : il dit quel champ le firmware vérifie.
        security_type = None
        probe = ESPSomfyAPI(self.hass, 0, {CONF_HOST: self.host})
        ctx = await probe.fetch_login_context()
        if ctx is not None:
            security_type = ctx.get("type", 0)
        if user_input is not None:
            if security_type is None:
                # Type inconnu : les trois champs étaient affichés, on fusionne.
                data = {**self._reauth_entry.data, **user_input}
            else:
                # Remplace au lieu de fusionner : purge les identifiants périmés.
                data = dict(user_input)
            try:
                api = ESPSomfyAPI(self.hass, 0, data)
                await api.discover()
                await api.login(
                    {
                        "username": data.get(CONF_USERNAME, ""),
                        "password": data.get(CONF_PASSWORD, ""),
                        "pin": data.get(CONF_PIN, ""),
                    }
                )
            except LoginError as err:
                errors[err.args[0]] = err.args[1]
                if len(err.args) > 2:
                    description_placeholders["retry_after"] = str(err.args[2])
            except DiscoveryError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                # Sinon aiohttp répond 500 : « Unknown error occurred », sans trace.
                _LOGGER.exception("Unexpected error during re-authentication")
                errors["base"] = "unknown"
            else:
                # Méthode de ConfigFlow, pas de hass.config_entries. `reason` pointe nos
                # traductions ; celle du cœur manque en français.
                return self.async_update_reload_and_abort(
                    self._reauth_entry, data=data, reason="reauth_successful"
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_get_data_schema(
                self.hass,
                self._reauth_entry.data,
                host=self.host,
                security_type=security_type,
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a flow initiated by zeroconf."""
        if user_input is not None:
            return self.async_create_entry(
                title=self.zero_conf.hostname,
                data={
                    CONF_HOST: self.zero_conf.host,
                },
            )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                CONF_NAME: self.zero_conf.hostname,
                "model": self.zero_conf.properties.get("model", ""),
                "server_id": self.server_id,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for espsomfy_rts_enhanced."""
        return ESPSomfyOptionsFlowHandler(config_entry)


class ESPSomfyOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow for espsomfy_rts_enhanced component."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the OptionsFlow."""
        self._config_entry = config_entry
        self._errors: dict[str, Any] = {}
        self._host = (
            config_entry.data.get(CONF_HOST, "")
            if config_entry and config_entry.data
            else ""
        )
        # Fetched lazily from /loginContext (unauthenticated, no attempt-quota cost) so the
        # form only shows the credential field the firmware is actually checking right now -
        # otherwise it always showed all three (username/password/pin), leaving stale values
        # from a previous security mode lying around and resubmitted on every save.
        self._security_type: int | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure options for espsomfy_rts_enhanced."""
        errors = {}
        description_placeholders = {}
        if self._security_type is None:
            probe = ESPSomfyAPI(self.hass, 0, {CONF_HOST: self._host})
            ctx = await probe.fetch_login_context()
            if ctx is not None:
                self._security_type = ctx.get("type", 0)
        if user_input is not None:
            try:
                if not is_host_valid(user_input[CONF_HOST]):
                    raise InvalidHost
                api = ESPSomfyAPI(self.hass, 0, user_input)
                await api.discover()
                await api.login(
                    {
                        "username": user_input.get(CONF_USERNAME, ""),
                        "password": user_input.get(CONF_PASSWORD, ""),
                        "pin": user_input.get(CONF_PIN, ""),
                    }
                )
                # Update config entry with data from user input
                self.hass.config_entries.async_update_entry(
                    self._config_entry, title=api.deviceName, data=user_input
                )
                # Sinon l'instance en mémoire garde les anciens identifiants.
                self.hass.config_entries.async_schedule_reload(
                    self._config_entry.entry_id
                )
                return self.async_create_entry(
                    title=api.deviceName,
                    data=user_input,
                )
            except InvalidHost:
                # wrong_host n'existe dans aucune traduction : s'affichait en brut.
                errors[CONF_HOST] = "invalid_host"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except DiscoveryError:
                errors[CONF_HOST] = "discovery_error"
            except LoginError as ex:
                errors[ex.args[0]] = ex.args[1]
                if len(ex.args) > 2:
                    description_placeholders["retry_after"] = str(ex.args[2])
            except AbortFlow:
                # Abandon volontaire : le filet ci-dessous ne doit pas l'avaler.
                raise
            except Exception:  # noqa: BLE001
                # Sinon aiohttp répond 500 : « Unknown error occurred », sans trace.
                _LOGGER.exception("Unexpected error while configuring ESPSomfy-RTS")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="init",
            data_schema=_get_data_schema(
                self.hass,
                data=self._config_entry.data,
                host=self._host,
                security_type=self._security_type,
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )


def _credential_fields(
    security_type: int | None, data: dict[str, Any]
) -> dict[Any, type]:
    """Build only the credential field(s) the firmware is actually checking.

    security_type comes from the device's own /loginContext (0=None, 1=PinEntry,
    2=Password). None means it couldn't be determined (device unreachable, or this is
    the very first setup before a host is even known) - fall back to showing all three
    as optional, the original behavior.
    """
    if security_type == 1:
        return {
            vol.Required(
                CONF_PIN, description={"suggested_value": data.get(CONF_PIN, "")}
            ): str
        }
    if security_type == 2:
        return {
            vol.Required(
                CONF_USERNAME,
                description={"suggested_value": data.get(CONF_USERNAME, "")},
            ): str,
            vol.Required(
                CONF_PASSWORD,
                description={"suggested_value": data.get(CONF_PASSWORD, "")},
            ): str,
        }
    if security_type == 0:
        return {}
    return {
        vol.Optional(
            CONF_USERNAME, description={"suggested_value": data.get(CONF_USERNAME, "")}
        ): str,
        vol.Optional(
            CONF_PASSWORD, description={"suggested_value": data.get(CONF_PASSWORD, "")}
        ): str,
        vol.Optional(
            CONF_PIN, description={"suggested_value": data.get(CONF_PIN, "")}
        ): str,
    }


def _get_data_schema(
    hass: HomeAssistant,
    data: dict[str, Any] | None = None,
    host: str = "",
    security_type: int | None = None,
) -> vol.Schema:
    """Get a schema with default values."""
    # If tracking home or no config entry is passed in, default value come from Home location
    if data is None or data.get(CONF_HOST, host) == "":
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=host): str,
                **_credential_fields(security_type, {}),
            }
        )
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=data.get(CONF_HOST)): str,
            **_credential_fields(security_type, data),
        }
    )
