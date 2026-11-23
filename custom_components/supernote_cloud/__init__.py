"""supernote_cloud custom component."""

from __future__ import annotations

import logging
from typing import override

from homeassistant.components import llm as llm_component
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.llm import (
    API,
    APIInstance,
    LLMContext,
    async_register_api,
    selector_serializer,
)
from homeassistant.helpers.typing import ConfigType
from supernote.client.schedule import ScheduleClient

from .api import async_get_supernote_client
from .const import DOMAIN
from .coordinator import SupernoteStorageCoordinator, SupernoteTodoCoordinator
from .media_source import async_register_http_views
from .types import SupernoteCloudConfigEntry, SupernoteCloudData

__all__ = ["DOMAIN"]

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.TODO]


class SupernoteLLMApi(API):
    """Supernote LLM API."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SupernoteCloudConfigEntry,
    ) -> None:
        """Initialize the LLM API."""
        super().__init__(
            hass=hass,
            id=f"{DOMAIN}-{entry.entry_id}",
            name=f"Supernote: {entry.title}",
        )

    @override
    async def async_get_api_instance(self, llm_context: LLMContext) -> APIInstance:
        """Return the instance of the API."""
        llm_tools = await llm_component.async_get_tools(self.hass, llm_context, self.id)
        return APIInstance(
            api=self,
            api_prompt=llm_tools.prompt or "",
            llm_context=llm_context,
            tools=llm_tools.tools,
            custom_serializer=selector_serializer,
        )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the supernote_cloud component."""
    async_register_http_views(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: SupernoteCloudConfigEntry
) -> bool:
    """Set up a config entry."""
    if not entry.options.get(CONF_USERNAME) or not entry.options.get(CONF_PASSWORD):
        raise ConfigEntryAuthFailed("Missing credentials")

    sn = await async_get_supernote_client(hass, entry)

    coordinator = SupernoteStorageCoordinator(hass, entry, sn)
    todo_coordinator = SupernoteTodoCoordinator(hass, entry, ScheduleClient(sn.client))
    await coordinator.async_config_entry_first_refresh()
    await todo_coordinator.async_config_entry_first_refresh()

    entry.runtime_data = SupernoteCloudData(
        client=sn,
        coordinator=coordinator,
        todo_coordinator=todo_coordinator,
    )

    await hass.config_entries.async_forward_entry_setups(
        entry,
        platforms=PLATFORMS,
    )
    entry.async_on_unload(async_register_api(hass, SupernoteLLMApi(hass, entry)))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SupernoteCloudConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )
