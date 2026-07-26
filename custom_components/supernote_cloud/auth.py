"""Supernote Cloud Auth."""

import datetime
import logging
from typing import cast

import aiohttp
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.util import dt as dt_util
from supernote.client.auth import AbstractAuth
from supernote.client.client import Client
from supernote.client.exceptions import SupernoteException, UnauthorizedException
from supernote.client.login_client import LoginClient

from .const import CONF_HOST, CONF_TOKEN_TIMESTAMP, DEFAULT_HOST, TOKEN_LIFETIME
from .types import SupernoteCloudConfigEntry

_LOGGER = logging.getLogger(__name__)


class ConfigEntryAuth(AbstractAuth):
    """Config entry auth."""

    _entry: SupernoteCloudConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SupernoteCloudConfigEntry,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the auth."""
        self._hass = hass
        self._entry = entry
        self._session = session
        host = entry.options.get(CONF_HOST, DEFAULT_HOST)
        self._login_client = LoginClient(Client(session, host=host))

    async def async_get_access_token(self) -> str:
        """Return a valid access token."""
        if self.is_expired():
            await self._refresh_access_token()
        return cast(str, self._entry.options[CONF_ACCESS_TOKEN])

    def is_expired(self) -> bool:
        token_timestamp = self._entry.options.get(CONF_TOKEN_TIMESTAMP, 0)
        token_ts = datetime.datetime.fromtimestamp(
            int(token_timestamp), tz=datetime.UTC
        )
        now = dt_util.now()
        age = now - token_ts
        return age > TOKEN_LIFETIME

    async def _refresh_access_token(self) -> None:
        """Refresh access token."""
        if CONF_PASSWORD not in self._entry.options:
            return
        try:
            new_token = await self._login_client.login(
                self._entry.options[CONF_USERNAME], self._entry.options[CONF_PASSWORD]
            )
        except UnauthorizedException as err:
            _LOGGER.debug("Login unauthorized: %s", err)
            self._entry.async_start_reauth(self._hass)
            raise ConfigEntryAuthFailed("Invalid credentials") from err
        except SupernoteException as err:
            _LOGGER.debug("Login api exception: %s", err)
            if "verification code" in str(err):
                self._entry.async_start_reauth(self._hass)
                raise ConfigEntryAuthFailed(
                    f"Verification code required: {err}"
                ) from err
            raise HomeAssistantError(f"API Error: {err}") from err

        self._hass.config_entries.async_update_entry(
            self._entry,
            options={
                **self._entry.options,
                CONF_ACCESS_TOKEN: new_token,
                CONF_TOKEN_TIMESTAMP: dt_util.now().timestamp(),
            },
        )
