"""Fixtures for the custom component."""

import logging
from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_UNIQUE_ID,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)

from custom_components.supernote_cloud.const import (
    CONF_API_USERNAME,
    CONF_TOKEN_TIMESTAMP,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


CONFIG_ENTRY_ID = "user-identifier-1"
CONFIG_ENTRY_TITLE = "user-name"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable custom integration."""
    _ = enable_custom_integrations  # unused
    yield


@pytest.fixture(name="platforms")
def mock_platforms() -> list[Platform]:
    """Fixture for platforms loaded by the integration."""
    return []


@pytest.fixture(name="setup_integration")
async def mock_setup_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    platforms: list[Platform],
    mock_supernote: AsyncMock,
) -> AsyncGenerator[None]:
    """Set up the integration."""

    with patch(f"custom_components.{DOMAIN}.PLATFORMS", platforms):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
        yield


@pytest.fixture(name="config_entry")
async def mock_config_entry(
    hass: HomeAssistant,
    mock_supernote: AsyncMock,
) -> MockConfigEntry:
    """Fixture to create a configuration entry."""
    config_entry = MockConfigEntry(
        unique_id=CONFIG_ENTRY_ID,
        title=CONFIG_ENTRY_TITLE,
        domain=DOMAIN,
        options={
            CONF_ACCESS_TOKEN: "access-token-1",
            CONF_TOKEN_TIMESTAMP: dt_util.now().timestamp(),
            CONF_UNIQUE_ID: CONFIG_ENTRY_ID,
            CONF_API_USERNAME: "user-name",
            CONF_PASSWORD: "some-password",
        },
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


@pytest.fixture(name="mock_supernote")
def mock_supernote_fixture() -> Generator[AsyncMock]:
    """Mock the Supernote client."""
    # Force reload of the component modules to ensure they pick up the patch
    import sys  # noqa: PLC0415

    for module in list(sys.modules.keys()):
        if module.startswith("custom_components.supernote_cloud"):
            del sys.modules[module]

    with patch("supernote.client.api.Supernote", autospec=True) as mock_sn_cls:
        # Create the instance mock
        mock_sn = mock_sn_cls.return_value

        mock_sn.token = "access-token-1"

        # WebClient and DeviceClient have async methods.
        # we configure them to be AsyncMocks
        mock_sn.web.list_query = AsyncMock()
        mock_sn.web.path_query = AsyncMock()
        mock_sn.web.query_user = AsyncMock()

        from supernote.models.file_device import (  # noqa: PLC0415
            AllocationVO,
            CapacityLocalVO,
        )

        mock_sn.device.note_to_png = AsyncMock()
        mock_sn.device.get_note_png_pages = AsyncMock()
        mock_sn.device.get_capacity = AsyncMock(
            return_value=CapacityLocalVO(
                used=10 * 1024 * 1024 * 1024,
                allocation_vo=AllocationVO(
                    tag="personal", allocated=50 * 1024 * 1024 * 1024
                ),
            )
        )
        mock_sn.device.list_folder = AsyncMock()
        mock_sn.client.get_content = AsyncMock()
        mock_sn.client.request = AsyncMock()

        # Class methods
        mock_sn_cls.login = AsyncMock(return_value=mock_sn)
        mock_sn_cls.from_token.return_value = mock_sn
        mock_sn_cls.from_auth.return_value = mock_sn

        yield mock_sn
