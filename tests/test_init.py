from unittest.mock import MagicMock, patch

import pytest
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
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)
from supernote.client.exceptions import UnauthorizedException

from custom_components.supernote_cloud.const import (
    CONF_API_USERNAME,
    CONF_TOKEN_TIMESTAMP,
    DOMAIN,
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
