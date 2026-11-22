"""Tests for Supernote Cloud ConfigEntryAuth reactive token refresh."""

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aiohttp import web
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_UNIQUE_ID,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_aiohttp.plugin import AiohttpServer
from pytest_homeassistant_custom_component.common import MockConfigEntry
from supernote.client.client import Client
from supernote.client.exceptions import (
    ApiException,
    SmsVerificationRequired,
    UnauthorizedException,
)
from supernote.models.base import BaseResponse

from custom_components.supernote_cloud.auth import ConfigEntryAuth
from custom_components.supernote_cloud.const import (
    CONF_API_USERNAME,
    CONF_TOKEN_TIMESTAMP,
    DOMAIN,
)


def _make_entry(
    *,
    token: str = "initial-token",
    token_timestamp: float | None = None,
    include_password: bool = True,
) -> MockConfigEntry:
    """Create a MockConfigEntry with mid-TTL token by default."""
    if token_timestamp is None:
        # 1 hour old -> well within 5-day TOKEN_LIFETIME
        token_timestamp = (dt_util.now() - dt.timedelta(hours=1)).timestamp()
    options: dict[str, object] = {
        CONF_USERNAME: "user-name",
        CONF_ACCESS_TOKEN: token,
        CONF_TOKEN_TIMESTAMP: token_timestamp,
        CONF_UNIQUE_ID: "user-identifier-1",
        CONF_API_USERNAME: "user-name",
    }
    if include_password:
        options[CONF_PASSWORD] = "some-password"
    return MockConfigEntry(
        unique_id="user-identifier-1",
        title="user-name",
        domain=DOMAIN,
        options=options,
    )


async def test_reactive_refresh_mid_ttl_succeeds_on_retry(
    hass: HomeAssistant,
) -> None:
    """Test mid-TTL 401 refreshes token, updates entry options, and succeeds on retry."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    old_ts = entry.options[CONF_TOKEN_TIMESTAMP]

    auth = ConfigEntryAuth(hass, entry, AsyncMock())
    assert not auth.is_expired()

    calls = 0

    async def _api_call() -> str:
        nonlocal calls
        calls += 1
        token = await auth.async_get_access_token()
        if token == "initial-token":
            raise UnauthorizedException("Invalid token")
        return f"ok:{token}"

    with patch(
        "custom_components.supernote_cloud.auth.LoginClient.login",
        new_callable=AsyncMock,
        return_value="fresh-token",
    ) as mock_login:
        result = await auth.async_call_with_retry(_api_call)

    assert result == "ok:fresh-token"
    assert calls == 2
    mock_login.assert_awaited_once_with("user-name", "some-password")
    assert entry.options[CONF_ACCESS_TOKEN] == "fresh-token"
    assert entry.options[CONF_TOKEN_TIMESTAMP] > old_ts
    assert not hass.config_entries.flow.async_progress()


async def test_concurrent_401_shares_single_login_refresh(
    hass: HomeAssistant,
) -> None:
    """Test concurrent mid-TTL 401s share a single login refresh under _refresh_lock."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    auth = ConfigEntryAuth(hass, entry, AsyncMock())

    async def _slow_login(username: str, password: str) -> str:
        await asyncio.sleep(0.02)
        return "fresh-token"

    async def _api_call(label: str) -> str:
        token = await auth.async_get_access_token()
        if token == "initial-token":
            raise UnauthorizedException("Invalid token")
        return f"{label}:{token}"

    with patch(
        "custom_components.supernote_cloud.auth.LoginClient.login",
        side_effect=_slow_login,
    ) as mock_login:
        res1, res2 = await asyncio.gather(
            auth.async_call_with_retry(lambda: _api_call("a")),
            auth.async_call_with_retry(lambda: _api_call("b")),
        )

    assert res1 == "a:fresh-token"
    assert res2 == "b:fresh-token"
    assert mock_login.call_count == 1
    assert entry.options[CONF_ACCESS_TOKEN] == "fresh-token"


async def test_setup_missing_password_triggers_reauth(
    hass: HomeAssistant,
) -> None:
    """Test config entry setup fails with reauth when stored password is missing."""
    entry = _make_entry(include_password=False)
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"


async def test_reactive_refresh_login_unauthorized_raises_auth_failed(
    hass: HomeAssistant,
) -> None:
    """Test login failure on reactive refresh invalidates timestamp and starts reauth."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    auth = ConfigEntryAuth(hass, entry, AsyncMock())

    api_call = AsyncMock(side_effect=UnauthorizedException("Invalid token"))

    with (
        patch(
            "custom_components.supernote_cloud.auth.LoginClient.login",
            new_callable=AsyncMock,
            side_effect=UnauthorizedException("Bad password"),
        ) as mock_login,
        pytest.raises(ConfigEntryAuthFailed, match="Invalid credentials"),
    ):
        await auth.async_call_with_retry(api_call)

    await hass.async_block_till_done()
    mock_login.assert_awaited_once()
    assert api_call.await_count == 1
    assert entry.options[CONF_TOKEN_TIMESTAMP] == 0
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"


async def test_reactive_refresh_sms_verification_raises_auth_failed(
    hass: HomeAssistant,
) -> None:
    """Test SMS verification requirement during reactive refresh starts reauth."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    auth = ConfigEntryAuth(hass, entry, AsyncMock())

    api_call = AsyncMock(side_effect=UnauthorizedException("Invalid token"))

    with (
        patch(
            "custom_components.supernote_cloud.auth.LoginClient.login",
            new_callable=AsyncMock,
            side_effect=SmsVerificationRequired("SMS needed", "12345"),
        ),
        pytest.raises(ConfigEntryAuthFailed, match="Verification code required"),
    ):
        await auth.async_call_with_retry(api_call)

    await hass.async_block_till_done()
    assert api_call.await_count == 1
    assert entry.options[CONF_TOKEN_TIMESTAMP] == 0
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"


async def test_reactive_refresh_transient_api_error_does_not_start_reauth(
    hass: HomeAssistant,
) -> None:
    """Test transient login API error invalidates timestamp without starting reauth."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    auth = ConfigEntryAuth(hass, entry, AsyncMock())

    api_call = AsyncMock(side_effect=UnauthorizedException("Invalid token"))

    with (
        patch(
            "custom_components.supernote_cloud.auth.LoginClient.login",
            new_callable=AsyncMock,
            side_effect=ApiException("503 Service Unavailable"),
        ),
        pytest.raises(HomeAssistantError, match="API Error"),
    ):
        await auth.async_call_with_retry(api_call)

    await hass.async_block_till_done()
    assert api_call.await_count == 1
    assert entry.options[CONF_TOKEN_TIMESTAMP] == 0
    assert not hass.config_entries.flow.async_progress()


async def test_reactive_refresh_retry_still_401_invalidates_timestamp_and_raises(
    hass: HomeAssistant,
) -> None:
    """Test retry still returning 401 invalidates timestamp and raises without looping."""
    entry = _make_entry()
    entry.add_to_hass(hass)
    auth = ConfigEntryAuth(hass, entry, AsyncMock())

    api_call = AsyncMock(side_effect=UnauthorizedException("Still invalid"))

    with (
        patch(
            "custom_components.supernote_cloud.auth.LoginClient.login",
            new_callable=AsyncMock,
            return_value="fresh-token",
        ) as mock_login,
        pytest.raises(UnauthorizedException, match="Still invalid"),
    ):
        await auth.async_call_with_retry(api_call)

    mock_login.assert_awaited_once()
    assert api_call.await_count == 2
    assert entry.options[CONF_TOKEN_TIMESTAMP] == 0


@pytest.mark.usefixtures("socket_enabled")
async def test_real_client_re_reads_refreshed_token_on_retry(
    hass: HomeAssistant,
    aiohttp_server: AiohttpServer,
) -> None:
    """Test real supernote.client.Client sends refreshed x-access-token on retry."""
    entry = _make_entry(token="stale-token")
    entry.add_to_hass(hass)

    sent_tokens: list[str | None] = []

    async def _handle_csrf(request: web.Request) -> web.Response:
        _ = request
        return web.Response(headers={"X-XSRF-TOKEN": "csrf-1"})

    async def _handle_test(request: web.Request) -> web.Response:
        token = request.headers.get("x-access-token")
        sent_tokens.append(token)
        if token == "stale-token":
            return web.json_response(
                {"success": False, "errorMsg": "Invalid token"},
                status=401,
            )
        return web.json_response({"success": True})

    app = web.Application()
    app.router.add_get("/api/csrf", _handle_csrf)
    app.router.add_post("/api/test", _handle_test)
    server = await aiohttp_server(app)
    host = str(server.make_url("")).rstrip("/")

    async with aiohttp.ClientSession() as session:
        auth = ConfigEntryAuth(hass, entry, session)
        client = Client(session, host=host, auth=auth)

        with patch(
            "custom_components.supernote_cloud.auth.LoginClient.login",
            new_callable=AsyncMock,
            return_value="fresh-token",
        ) as mock_login:
            result = await auth.async_call_with_retry(
                lambda: client.post_json("/api/test", BaseResponse, json={})
            )

    assert result.success is True
    assert sent_tokens == ["stale-token", "fresh-token"]
    mock_login.assert_awaited_once_with("user-name", "some-password")
    assert entry.options[CONF_ACCESS_TOKEN] == "fresh-token"
