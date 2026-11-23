"""Supernote Cloud Auth."""

import asyncio
import datetime as dt
import logging
from typing import cast, override

import aiohttp
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.util import dt as dt_util
from supernote.client.auth import AbstractAuth
from supernote.client.client import Client
from supernote.client.exceptions import (
    SmsVerificationRequired,
    SupernoteException,
    UnauthorizedException,
)
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
        self._refresh_lock = asyncio.Lock()

    async def async_get_access_token(self) -> str:
        """Return a valid access token."""
        if self.is_expired():
            async with self._refresh_lock:
                if self.is_expired():
                    await self._refresh_access_token()
        return cast(str, self._entry.options[CONF_ACCESS_TOKEN])

    def is_expired(self) -> bool:
        """Return True if the cached access token has exceeded its TTL."""
        token_timestamp = self._entry.options.get(CONF_TOKEN_TIMESTAMP) or 0
        token_ts = dt.datetime.fromtimestamp(int(token_timestamp), tz=dt.UTC)
        now = dt_util.now()
        age = now - token_ts
        return age > TOKEN_LIFETIME

    @override
    async def async_refresh_access_token(self, failed_token: str) -> bool:
        """Force a reactive access token refresh after a 401 response.

        Serialized with self._refresh_lock so concurrent 401s for the same stale
        token only trigger a single login request.
        """
        async with self._refresh_lock:
            current_token = self._entry.options.get(CONF_ACCESS_TOKEN)
            if current_token != failed_token:
                _LOGGER.debug(
                    "Access token already refreshed by another request; skipping login"
                )
                return True
            try:
                await self._refresh_access_token()
            except Exception:
                self._invalidate_token_timestamp()
                raise
            return True

    def _invalidate_token_timestamp(self) -> None:
        """Invalidate the cached token timestamp so reloads do not reuse a dead token."""
        if self._entry.options.get(CONF_TOKEN_TIMESTAMP, 0) != 0:
            self._hass.config_entries.async_update_entry(
                self._entry,
                options={
                    **self._entry.options,
                    CONF_TOKEN_TIMESTAMP: 0,
                },
            )

    async def _refresh_access_token(self) -> None:
        """Refresh access token."""
        try:
            new_token = await self._login_client.login(
                self._entry.options[CONF_USERNAME], self._entry.options[CONF_PASSWORD]
            )
        except UnauthorizedException as err:
            _LOGGER.debug("Login unauthorized: %s", err)
            self._entry.async_start_reauth(self._hass)
            raise ConfigEntryAuthFailed("Invalid credentials") from err
        except SmsVerificationRequired as err:
            _LOGGER.debug("Login verification code required: %s", err)
            self._entry.async_start_reauth(self._hass)
            raise ConfigEntryAuthFailed(f"Verification code required: {err}") from err
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
