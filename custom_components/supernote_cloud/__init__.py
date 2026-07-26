"""supernote_cloud custom component."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .api import async_get_supernote_client
from .const import DOMAIN
from .coordinator import SupernoteStorageCoordinator
from .llm import async_register_llm_apis
from .media_source import async_register_http_views
from .types import SupernoteCloudConfigEntry, SupernoteCloudData

__all__ = ["DOMAIN"]

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the supernote_cloud component."""
    async_register_http_views(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: SupernoteCloudConfigEntry
) -> bool:
    """Set up a config entry."""
    sn = await async_get_supernote_client(hass, entry)

    coordinator = SupernoteStorageCoordinator(hass, entry, sn)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = SupernoteCloudData(client=sn, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(
        entry,
        platforms=PLATFORMS,
    )
    await async_register_llm_apis(hass, entry)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SupernoteCloudConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )
