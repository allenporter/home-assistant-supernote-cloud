"""Todo platform for Supernote Cloud."""

from __future__ import annotations

import datetime as dt
from typing import override

from aiohttp import ClientError
from homeassistant.components.todo import (
    DOMAIN as TODO_DOMAIN,
)
from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from supernote.client.exceptions import (
    NotFoundException,
    SupernoteException,
    UnauthorizedException,
)
from supernote.models.schedule import ScheduleTaskInfo

from .const import DOMAIN
from .coordinator import SupernoteTodoCoordinator
from .types import SupernoteCloudConfigEntry

PARALLEL_UPDATES = 0

INBOX_KEY = "inbox"

TODO_STATUS_MAP: dict[str, TodoItemStatus] = {
    "needsAction": TodoItemStatus.NEEDS_ACTION,
    "completed": TodoItemStatus.COMPLETED,
}
TODO_STATUS_MAP_INV: dict[TodoItemStatus, str] = {
    TodoItemStatus.NEEDS_ACTION: "needsAction",
    TodoItemStatus.COMPLETED: "completed",
}


def _group_unique_id(entry_id: str, group_id: str | None) -> str:
    """Build a collision-free unique_id for Inbox or a custom task group."""
    if group_id is None:
        return f"{entry_id}_{INBOX_KEY}"
    return f"{entry_id}_group_{group_id}"


def _is_inbox_task(task: ScheduleTaskInfo, known_groups: set[str]) -> bool:
    """Return True if a task belongs to the default Inbox list."""
    if not task.task_list_id:
        return True
    return task.task_list_id == "0" and "0" not in known_groups


def _due_to_ms(due: dt.date | dt.datetime) -> int:
    """Convert a TodoItem due date or datetime into UTC epoch milliseconds."""
    if isinstance(due, dt.datetime):
        return int(dt_util.as_utc(due).timestamp() * 1000)
    return int(dt_util.as_utc(dt_util.start_of_local_day(due)).timestamp() * 1000)


def _ms_to_datetime(ms: int | None) -> dt.datetime | None:
    """Convert epoch milliseconds to a UTC datetime, ignoring out-of-range values."""
    if not ms or ms <= 0:
        return None
    try:
        return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt_util.UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _ms_to_due(ms: int | None) -> dt.date | dt.datetime | None:
    """Convert epoch milliseconds to a local date (if local start-of-day) or datetime."""
    dt_utc = _ms_to_datetime(ms)
    if dt_utc is None:
        return None
    dt_local = dt_util.as_local(dt_utc)
    if dt_utc == dt_util.as_utc(dt_util.start_of_local_day(dt_local.date())):
        return dt_local.date()
    return dt_local


def _convert_api_task(task: ScheduleTaskInfo) -> TodoItem:
    """Convert a Supernote ScheduleTaskInfo into a Home Assistant TodoItem."""
    completed_dt = _ms_to_datetime(task.completed_time)
    if task.status:
        status = TODO_STATUS_MAP.get(task.status, TodoItemStatus.NEEDS_ACTION)
    elif completed_dt is not None:
        status = TodoItemStatus.COMPLETED
    else:
        status = TodoItemStatus.NEEDS_ACTION

    return TodoItem(
        uid=task.task_id,
        summary=task.title or "",
        status=status,
        due=_ms_to_due(task.due_time),
        description=task.detail or None,
        completed=completed_dt if status == TodoItemStatus.COMPLETED else None,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SupernoteCloudConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Supernote Cloud todo list entities from a config entry."""
    coordinator = entry.runtime_data.todo_coordinator
    known_groups: set[str] = set()
    group_prefix = f"{entry.entry_id}_group_"

    if coordinator.data is not None:
        ent_reg = er.async_get(hass)
        current_groups = set(coordinator.data.groups.keys())
        for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if reg_entry.domain == TODO_DOMAIN and reg_entry.unique_id.startswith(
                group_prefix
            ):
                group_id = reg_entry.unique_id.removeprefix(group_prefix)
                if group_id not in current_groups:
                    ent_reg.async_remove(reg_entry.entity_id)

        known_groups.update(coordinator.data.groups.keys())

    @callback
    def _async_sync_groups() -> None:
        """Dynamically add new task groups and remove deleted ones."""
        if coordinator.data is None:
            return
        current_groups = set(coordinator.data.groups.keys())

        removed_groups = known_groups - current_groups
        if removed_groups:
            ent_reg = er.async_get(hass)
            for group_id in removed_groups:
                unique_id = _group_unique_id(entry.entry_id, group_id)
                if entity_id := ent_reg.async_get_entity_id(
                    TODO_DOMAIN, DOMAIN, unique_id
                ):
                    ent_reg.async_remove(entity_id)
            known_groups.difference_update(removed_groups)

        new_groups = current_groups - known_groups
        if new_groups:
            async_add_entities(
                SupernoteTodoListEntity(
                    coordinator,
                    group_id=group_id,
                    group_title=coordinator.data.groups[group_id].title,
                )
                for group_id in new_groups
            )
            known_groups.update(new_groups)

    # Register group-sync listener BEFORE entity listeners so deleted groups are
    # removed before their entity listener could write a spurious empty state.
    entry.async_on_unload(coordinator.async_add_listener(_async_sync_groups))

    entities: list[SupernoteTodoListEntity] = [
        SupernoteTodoListEntity(coordinator, group_id=None, group_title=None)
    ]
    if coordinator.data is not None:
        entities.extend(
            SupernoteTodoListEntity(
                coordinator,
                group_id=group_id,
                group_title=group.title,
            )
            for group_id, group in coordinator.data.groups.items()
        )
    async_add_entities(entities)


class SupernoteTodoListEntity(
    CoordinatorEntity[SupernoteTodoCoordinator], TodoListEntity
):
    """Representation of a Supernote Cloud To-do list."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(
        self,
        coordinator: SupernoteTodoCoordinator,
        *,
        group_id: str | None,
        group_title: str | None,
    ) -> None:
        """Initialize the Supernote To-do list entity."""
        super().__init__(coordinator)
        self._group_id = group_id
        self._attr_unique_id = _group_unique_id(coordinator.entry.entry_id, group_id)
        if group_id is None:
            self._attr_translation_key = INBOX_KEY
        else:
            self._attr_name = group_title or f"List {group_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=f"Supernote ({coordinator.entry.title})",
            manufacturer="Supernote",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._sync_from_coordinator()

    def _sync_from_coordinator(self) -> None:
        """Populate entity items and title from current coordinator data."""
        data = self.coordinator.data
        if data is None:
            self._attr_todo_items = None
            return

        if (
            self._group_id is not None
            and (group := data.groups.get(self._group_id))
            and group.title
        ):
            self._attr_name = group.title

        known_group_ids = set(data.groups.keys())
        matching_tasks = [
            task
            for task in data.tasks
            if (
                _is_inbox_task(task, known_group_ids)
                if self._group_id is None
                else task.task_list_id == self._group_id
            )
        ]
        matching_tasks.sort(key=lambda t: t.sort if t.sort else 0)
        self._attr_todo_items = [_convert_api_task(task) for task in matching_tasks]

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if (
            self._group_id is not None
            and self.coordinator.data is not None
            and self._group_id not in self.coordinator.data.groups
        ):
            # Group was deleted on Supernote; skip writing a bogus "0" state
            # while entity removal completes.
            return
        self._sync_from_coordinator()
        super()._handle_coordinator_update()

    @override
    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Create a To-do item in Supernote Cloud."""
        title = (item.summary or "").strip()
        if not title:
            raise ServiceValidationError("To-do item summary cannot be empty")

        status_str = TODO_STATUS_MAP_INV.get(
            item.status or TodoItemStatus.NEEDS_ACTION,
            "needsAction",
        )
        due_time = _due_to_ms(item.due) if item.due is not None else None
        completed_time_ms = (
            int(dt_util.utcnow().timestamp() * 1000)
            if item.status == TodoItemStatus.COMPLETED
            else None
        )
        try:
            await self.coordinator.schedule.create_task(
                group_id=self._group_id,
                title=title,
                detail=item.description,
                status=status_str,
                due_time=due_time,
                completed_time_ms=completed_time_ms,
            )
        except ConfigEntryAuthFailed:
            raise
        except UnauthorizedException as err:
            self.coordinator.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"Supernote authentication failed: {err}") from err
        except (SupernoteException, ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Error creating Supernote to-do item: {err}"
            ) from err

        await self.coordinator.async_refresh()

    @override
    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Update a To-do item in Supernote Cloud."""
        if not item.uid:
            raise ServiceValidationError("Cannot update To-do item without UID")
        title = (item.summary or "").strip()
        if not title:
            raise ServiceValidationError("To-do item summary cannot be empty")

        uid = item.uid
        existing = (
            next(
                (t for t in self.coordinator.data.tasks if t.task_id == uid),
                None,
            )
            if self.coordinator.data
            else None
        )

        status_str = TODO_STATUS_MAP_INV.get(
            item.status or TodoItemStatus.NEEDS_ACTION,
            "needsAction",
        )
        if item.status == TodoItemStatus.COMPLETED:
            if (
                existing
                and existing.status == "completed"
                and existing.completed_time
                and existing.completed_time > 0
            ):
                completed_time_ms = existing.completed_time
            else:
                completed_time_ms = int(dt_util.utcnow().timestamp() * 1000)
        else:
            completed_time_ms = 0

        due_time = _due_to_ms(item.due) if item.due is not None else 0

        try:
            await self.coordinator.schedule.update_task(
                task_id=uid,
                title=title,
                detail=item.description or "",
                status=status_str,
                due_time=due_time,
                completed_time_ms=completed_time_ms,
            )
        except ConfigEntryAuthFailed:
            raise
        except UnauthorizedException as err:
            self.coordinator.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"Supernote authentication failed: {err}") from err
        except (SupernoteException, ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Error updating Supernote to-do item: {err}"
            ) from err

        await self.coordinator.async_refresh()

    @override
    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete To-do items from Supernote Cloud."""
        deleted_any = False
        try:
            for uid in uids:
                if not uid:
                    continue
                try:
                    await self.coordinator.schedule.delete_task(uid)
                except NotFoundException:
                    pass
                deleted_any = True
        except ConfigEntryAuthFailed:
            if deleted_any:
                await self.coordinator.async_refresh()
            raise
        except UnauthorizedException as err:
            if deleted_any:
                await self.coordinator.async_refresh()
            self.coordinator.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"Supernote authentication failed: {err}") from err
        except (SupernoteException, ClientError, TimeoutError) as err:
            if deleted_any:
                await self.coordinator.async_refresh()
            raise HomeAssistantError(
                f"Error deleting Supernote to-do item: {err}"
            ) from err

        await self.coordinator.async_refresh()
