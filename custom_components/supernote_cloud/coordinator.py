"""DataUpdateCoordinator for Supernote Cloud."""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from supernote.client.api import Supernote
from supernote.client.exceptions import ApiException
from supernote.models.file_device import CapacityLocalVO

from .const import DOMAIN
from .types import SupernoteCloudConfigEntry

_LOGGER = logging.getLogger(__name__)

# Update interval for storage stats. Since storage stats change slowly,
# 30 minutes is a sensible default.
UPDATE_INTERVAL = timedelta(minutes=30)


class SupernoteStorageCoordinator(DataUpdateCoordinator[CapacityLocalVO]):
    """Class to manage fetching storage capacity from Supernote Cloud API."""

    entry: SupernoteCloudConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SupernoteCloudConfigEntry,
        client: Supernote,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_storage_{entry.title}",
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self) -> CapacityLocalVO:
        """Fetch storage data from API."""
        try:
            return await self.client.device.get_capacity()
        except ApiException as err:
            raise UpdateFailed(f"Error fetching storage capacity: {err}") from err
