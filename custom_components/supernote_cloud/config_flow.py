"""Config flow for supernote_cloud integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_UNIQUE_ID,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import (
    async_get_clientsession,
)
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaFlowFormStep,
    SchemaOptionsFlowHandler,
)
from homeassistant.util import dt as dt_util
from supernote.client.api import Supernote
from supernote.client.client import Client
from supernote.client.exceptions import (
    ApiException,
    SmsVerificationRequired,
    SupernoteException,
)
from supernote.client.login_client import LoginClient

from .const import (
    CONF_API_USERNAME,
    CONF_HOST,
    CONF_TOKEN_TIMESTAMP,
    DEFAULT_HOST,
    DOMAIN,
)
from .types import SupernoteCloudConfigEntry

_LOGGER = logging.getLogger(__name__)

OPTIONS_FLOW = {
    "init": SchemaFlowFormStep(),
}


class SupernoteCloudConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Supernote Cloud."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._username: str | None = None
        self._password: str | None = None
        self._host: str | None = None
        self._sms_timestamp: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: SupernoteCloudConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return SchemaOptionsFlowHandler(config_entry, OPTIONS_FLOW)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            self._host = user_input[CONF_HOST]

            websession = async_get_clientsession(self.hass)
            try:
                sn = await Supernote.login(
                    self._username, self._password, host=self._host, session=websession
                )
                assert sn.token is not None
                access_token = sn.token
                return await self._async_create_supernote_entry(access_token)
            except SmsVerificationRequired as err:
                self._sms_timestamp = err.timestamp
                try:
                    login_client = self._async_get_login_client()
                    await login_client.request_sms_code(self._username)
                except (ApiException, SupernoteException):
                    errors["base"] = "api_error"
                else:
                    return await self.async_step_sms()
            except (ApiException, SupernoteException):
                errors["base"] = "api_error"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST,
                        default=self._host or DEFAULT_HOST,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(),
                    ),
                    vol.Required(
                        CONF_USERNAME,
                        default=self._username or "",
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(),
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        ),
                    ),
                }
            ),
            errors=errors or None,
        )

    async def async_step_sms(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the SMS verification step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = user_input["code"]
            login_client = self._async_get_login_client()

            try:
                assert self._username is not None
                assert self._sms_timestamp is not None
                access_token = await login_client.sms_login(
                    self._username,
                    code,
                    self._sms_timestamp,
                )
                return await self._async_create_supernote_entry(access_token)
            except (ApiException, SupernoteException):
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="sms",
            data_schema=vol.Schema(
                {
                    vol.Required("code"): selector.TextSelector(
                        selector.TextSelectorConfig(),
                    ),
                }
            ),
            errors=errors or None,
        )

    @callback
    def _async_get_login_client(self) -> LoginClient:
        """Get a login client."""
        return LoginClient(Client(async_get_clientsession(self.hass), host=self._host))

    async def _async_create_supernote_entry(
        self, access_token: str
    ) -> ConfigFlowResult:
        """Create the config entry."""
        # Verify the API works and get user info
        websession = async_get_clientsession(self.hass)
        sn = Supernote.from_token(access_token, host=self._host, session=websession)

        # Let's try to get something to confirm it works.
        await sn.device.get_capacity()

        assert self._username is not None
        unique_id = self._username
        await self.async_set_unique_id(unique_id)

        options = {
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_ACCESS_TOKEN: access_token,
            CONF_UNIQUE_ID: unique_id,
            CONF_API_USERNAME: self._username,
            CONF_TOKEN_TIMESTAMP: dt_util.now().timestamp(),
            CONF_HOST: self._host,
        }

        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch()
            reauth_entry = self.hass.config_entries.async_get_entry(
                self.context["entry_id"]
            )
            assert reauth_entry is not None
            return self.async_update_reload_and_abort(
                reauth_entry,
                title=self._username,
                data={},
                options=options,
            )

        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=self._username,
            data={},
            options=options,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if reauth_entry:
            self._host = reauth_entry.options.get(CONF_HOST)
            self._username = reauth_entry.options.get(CONF_USERNAME)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
            )
        return await self.async_step_user()
