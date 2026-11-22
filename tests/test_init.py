import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.components.media_source.helper import async_browse_media
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_UNIQUE_ID,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_aiohttp.plugin import AiohttpServer
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)
from supernote.client.exceptions import (
    ApiException,
    SmsVerificationRequired,
    UnauthorizedException,
)

from custom_components.supernote_cloud.auth import ConfigEntryAuth
from custom_components.supernote_cloud.const import (
    CONF_API_USERNAME,
    CONF_HOST,
    CONF_TOKEN_TIMESTAMP,
    DOMAIN,
    TOKEN_LIFETIME,
)


@pytest.mark.usefixtures("setup_integration")
async def test_init(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    """Setup the integration"""

    assert config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        config_entry.state is ConfigEntryState.NOT_LOADED  # type: ignore[comparison-overlap]
    )


async def test_api_unauthorized_triggers_reauth(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
) -> None:
    """Test that an UnauthorizedException during API calls triggers reauth."""

    # Configure the mock to raise UnauthorizedException during folder browse
    mock_supernote.web.list_query.side_effect = UnauthorizedException("Unauthorized")

    config_entry = MockConfigEntry(
        unique_id="user-identifier-1",
        title="user-name",
        domain=DOMAIN,
        options={
            CONF_USERNAME: "user-name",
            CONF_ACCESS_TOKEN: "access-token-1",
            CONF_TOKEN_TIMESTAMP: dt_util.now().timestamp(),
            CONF_UNIQUE_ID: "user-identifier-1",
            CONF_API_USERNAME: "user-name",
            CONF_PASSWORD: "some-password",
        },
    )
    config_entry.add_to_hass(hass)

    await async_setup_component(hass, "media_source", {})

    # Mock login to also raise UnauthorizedException so token refresh fails
    with patch(
        "supernote.client.login_client.LoginClient.login",
        side_effect=UnauthorizedException("Unauthorized refresh"),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Try to browse media, which should fail and trigger reauth
        with pytest.raises(BrowseError, match="Authentication failed"):
            await async_browse_media(
                hass, "media-source://supernote_cloud/user-identifier-1/f/0"
            )

    # Verify that the reauth flow was started
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"


async def test_concurrent_token_refresh_lock(
    hass: HomeAssistant,
) -> None:
    """Test ConfigEntryAuth serializes concurrent token refreshes."""
    expired_ts = (dt_util.now() - TOKEN_LIFETIME - dt.timedelta(hours=1)).timestamp()
    entry = MockConfigEntry(
        unique_id="user-identifier-1",
        title="user-name",
        domain=DOMAIN,
        options={
            CONF_USERNAME: "user-name",
            CONF_ACCESS_TOKEN: "old-token",
            CONF_TOKEN_TIMESTAMP: expired_ts,
            CONF_UNIQUE_ID: "user-identifier-1",
            CONF_API_USERNAME: "user-name",
            CONF_PASSWORD: "some-password",
        },
    )
    entry.add_to_hass(hass)

    async def _slow_login(username: str, password: str) -> str:
        await asyncio.sleep(0.02)
        return "refreshed-token"

    with patch(
        "custom_components.supernote_cloud.auth.LoginClient.login",
        side_effect=_slow_login,
    ) as mock_login:
        auth = ConfigEntryAuth(hass, entry, AsyncMock())
        t1, t2 = await asyncio.gather(
            auth.async_get_access_token(),
            auth.async_get_access_token(),
        )

    assert t1 == "refreshed-token"
    assert t2 == "refreshed-token"
    assert mock_login.call_count == 1


async def test_setup_missing_password_triggers_reauth(
    hass: HomeAssistant,
) -> None:
    """Test config entry setup fails with reauth when stored password is missing."""
    entry = MockConfigEntry(
        unique_id="user-identifier-1",
        title="user-name",
        domain=DOMAIN,
        options={
            CONF_USERNAME: "user-name",
            CONF_ACCESS_TOKEN: "access-token-1",
            CONF_TOKEN_TIMESTAMP: dt_util.now().timestamp(),
            CONF_UNIQUE_ID: "user-identifier-1",
            CONF_API_USERNAME: "user-name",
        },
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"


@pytest.mark.usefixtures("mock_supernote")
async def test_setup_sms_verification_on_refresh_triggers_reauth(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
    mock_login: AsyncMock,
) -> None:
    """Test SMS verification required during reactive refresh starts reauth."""
    mock_supernote.device.get_capacity.side_effect = UnauthorizedException(
        "Invalid token"
    )
    mock_login.side_effect = SmsVerificationRequired("SMS needed", "12345")

    entry = MockConfigEntry(
        unique_id="user-identifier-1",
        title="user-name",
        domain=DOMAIN,
        options={
            CONF_USERNAME: "user-name",
            CONF_PASSWORD: "some-password",
            CONF_ACCESS_TOKEN: "access-token-1",
            CONF_TOKEN_TIMESTAMP: dt_util.now().timestamp(),
            CONF_UNIQUE_ID: "user-identifier-1",
            CONF_API_USERNAME: "user-name",
        },
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.options[CONF_TOKEN_TIMESTAMP] == 0
    assert mock_supernote.device.get_capacity.await_count == 1

    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"


@pytest.mark.usefixtures("mock_supernote")
async def test_setup_transient_login_error_retries_without_reauth(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
    mock_login: AsyncMock,
) -> None:
    """Test transient login API error invalidates timestamp and retries setup without reauth."""
    mock_supernote.device.get_capacity.side_effect = UnauthorizedException(
        "Invalid token"
    )
    mock_login.side_effect = ApiException("503 Service Unavailable")

    entry = MockConfigEntry(
        unique_id="user-identifier-1",
        title="user-name",
        domain=DOMAIN,
        options={
            CONF_USERNAME: "user-name",
            CONF_PASSWORD: "some-password",
            CONF_ACCESS_TOKEN: "access-token-1",
            CONF_TOKEN_TIMESTAMP: dt_util.now().timestamp(),
            CONF_UNIQUE_ID: "user-identifier-1",
            CONF_API_USERNAME: "user-name",
        },
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.options[CONF_TOKEN_TIMESTAMP] == 0
    assert mock_supernote.device.get_capacity.await_count == 1
    assert not hass.config_entries.flow.async_progress()


@pytest.mark.usefixtures("socket_enabled")
async def test_setup_mid_ttl_401_end_to_end_with_http_server(
    hass: HomeAssistant,
    aiohttp_server: AiohttpServer,
) -> None:
    """Test real Supernote client sends refreshed x-access-token on retry via HA setup."""
    sent_tokens: list[str | None] = []

    async def _handle_csrf(request: web.Request) -> web.Response:
        _ = request
        return web.Response(headers={"X-XSRF-TOKEN": "csrf-1"})

    async def _handle_capacity(request: web.Request) -> web.Response:
        token = request.headers.get("x-access-token")
        sent_tokens.append(token)
        if token == "stale-token":
            return web.json_response(
                {"success": False, "errorMsg": "Invalid token"},
                status=401,
            )
        return web.json_response(
            {
                "success": True,
                "used": 10 * 1024 * 1024 * 1024,
                "allocationVO": {
                    "tag": "personal",
                    "allocated": 50 * 1024 * 1024 * 1024,
                },
            }
        )

    async def _handle_schedule_list(request: web.Request) -> web.Response:
        token = request.headers.get("x-access-token")
        sent_tokens.append(token)
        return web.json_response(
            {"success": True, "scheduleTaskGroup": [], "scheduleTask": []}
        )

    app = web.Application()
    app.router.add_get("/api/csrf", _handle_csrf)
    app.router.add_post("/api/file/2/users/get_space_usage", _handle_capacity)
    app.router.add_post("/api/file/schedule/group/all", _handle_schedule_list)
    app.router.add_post("/api/file/schedule/task/all", _handle_schedule_list)
    server = await aiohttp_server(app)
    host = str(server.make_url("")).rstrip("/")

    entry = MockConfigEntry(
        unique_id="user-identifier-1",
        title="user-name",
        domain=DOMAIN,
        options={
            CONF_HOST: host,
            CONF_USERNAME: "user-name",
            CONF_PASSWORD: "some-password",
            CONF_ACCESS_TOKEN: "stale-token",
            CONF_TOKEN_TIMESTAMP: dt_util.now().timestamp(),
            CONF_UNIQUE_ID: "user-identifier-1",
            CONF_API_USERNAME: "user-name",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "supernote.client.login_client.LoginClient.login",
        new_callable=AsyncMock,
        return_value="fresh-token",
    ) as mock_login:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert sent_tokens[0] == "stale-token"
    assert sent_tokens[1] == "fresh-token"
    mock_login.assert_awaited_once_with("user-name", "some-password")
    assert entry.options[CONF_ACCESS_TOKEN] == "fresh-token"
    state = hass.states.get("sensor.supernote_user_name_storage_used")
    assert state is not None
    assert float(state.state) == 10.0
    assert not hass.config_entries.flow.async_progress()
