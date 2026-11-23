"""DataUpdateCoordinator for Supernote Cloud."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from supernote.client.api import Supernote
from supernote.client.exceptions import SupernoteException, UnauthorizedException
from supernote.client.schedule import ScheduleClient
from supernote.models.base import BooleanEnum
from supernote.models.file_device import CapacityLocalVO
from supernote.models.schedule import ScheduleTaskGroupItem, ScheduleTaskInfo

from .const import DOMAIN
from .types import SupernoteCloudConfigEntry

_LOGGER = logging.getLogger(__name__)

# Update interval for storage stats. Since storage stats change slowly,
# 30 minutes is a sensible default.
UPDATE_INTERVAL = timedelta(minutes=30)
TODO_UPDATE_INTERVAL = timedelta(minutes=15)


@dataclass
class SupernoteTodoData:
    """Schedule task groups and tasks fetched from Supernote Cloud."""

    groups: dict[str, ScheduleTaskGroupItem]
    tasks_by_group: dict[str | None, list[ScheduleTaskInfo]]


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
            config_entry=entry,
            name=f"{DOMAIN}_storage_{entry.title}",
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self) -> CapacityLocalVO:
        """Fetch storage data from API."""
        try:
            return await self.client.device.get_capacity()
        except ConfigEntryAuthFailed:
            raise
        except UnauthorizedException as err:
            raise ConfigEntryAuthFailed(
                f"Authentication failed fetching storage capacity: {err}"
            ) from err
        except (
            SupernoteException,
            HomeAssistantError,
            ClientError,
            TimeoutError,
        ) as err:
            raise UpdateFailed(f"Error fetching storage capacity: {err}") from err


class SupernoteTodoCoordinator(DataUpdateCoordinator[SupernoteTodoData]):
    """Class to manage fetching schedule task groups and tasks from Supernote Cloud."""

    entry: SupernoteCloudConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SupernoteCloudConfigEntry,
        schedule: ScheduleClient,
    ) -> None:
        """Initialize the todo coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_todo_{entry.title}",
            update_interval=TODO_UPDATE_INTERVAL,
        )
        self.schedule = schedule
        self.entry = entry

    async def _async_update_data(self) -> SupernoteTodoData:
        """Fetch schedule task groups and tasks from API."""
        try:
            raw_groups = [group async for group in self.schedule.list_groups()]
            raw_tasks = [task async for task in self.schedule.list_tasks()]
        except ConfigEntryAuthFailed:
            raise
        except UnauthorizedException as err:
            raise ConfigEntryAuthFailed(
                f"Authentication failed fetching Supernote to-do lists: {err}"
            ) from err
        except (
            SupernoteException,
            HomeAssistantError,
            ClientError,
            TimeoutError,
        ) as err:
            raise UpdateFailed(f"Error fetching Supernote to-do lists: {err}") from err

        groups = {
            group.task_list_id: group
            for group in raw_groups
            if group.task_list_id and group.is_deleted != BooleanEnum.YES
        }
        tasks_by_group: dict[str | None, list[ScheduleTaskInfo]] = defaultdict(list)
        for task in raw_tasks:
            if not task.task_id or task.is_deleted == BooleanEnum.YES:
                continue
            if not task.task_list_id or (
                task.task_list_id == "0" and "0" not in groups
            ):
                group_key: str | None = None
            else:
                group_key = task.task_list_id
            tasks_by_group[group_key].append(task)

        for group_tasks in tasks_by_group.values():
            group_tasks.sort(key=lambda t: t.sort if t.sort else 0)

        return SupernoteTodoData(groups=groups, tasks_by_group=dict(tasks_by_group))
