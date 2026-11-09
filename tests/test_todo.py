"""Tests for the Supernote Cloud todo platform."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientConnectionError
from homeassistant.components.todo import (
    DOMAIN as TODO_DOMAIN,
)
from homeassistant.components.todo import (
    TodoServices,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_UNIQUE_ID,
    CONF_USERNAME,
    EVENT_STATE_CHANGED,
    Platform,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from supernote.client.exceptions import UnauthorizedException
from supernote.models.base import BooleanEnum
from supernote.models.schedule import (
    ScheduleTaskGroupItem,
    ScheduleTaskInfo,
)

from custom_components.supernote_cloud.const import (
    CONF_API_USERNAME,
    CONF_TOKEN_TIMESTAMP,
    DOMAIN,
)

from .conftest import CONFIG_ENTRY_ID


def _make_entry(
    unique_id: str = CONFIG_ENTRY_ID, title: str = "user-name"
) -> MockConfigEntry:
    """Create a standard MockConfigEntry for tests that control setup timing."""
    return MockConfigEntry(
        unique_id=unique_id,
        title=title,
        domain=DOMAIN,
        options={
            CONF_USERNAME: title,
            CONF_ACCESS_TOKEN: "access-token-1",
            CONF_TOKEN_TIMESTAMP: dt_util.now().timestamp(),
            CONF_UNIQUE_ID: unique_id,
            CONF_API_USERNAME: title,
            CONF_PASSWORD: "some-password",
        },
    )


@pytest.fixture(name="platforms")
def mock_platforms() -> list[Platform]:
    """Fixture to load the todo platform."""
    return [Platform.TODO]


async def test_todo_lists_loaded_and_items(
    hass: HomeAssistant,
    mock_supernote: AsyncMock,
    mock_schedule: AsyncMock,
) -> None:
    """Test Inbox and custom task groups load with correct states and items."""
    await hass.config.async_set_time_zone("America/Los_Angeles")

    due_date_ms = int(
        dt_util.start_of_local_day(dt.date(2026, 11, 10)).timestamp() * 1000
    )
    due_dt_local = dt.datetime(
        2026, 11, 12, 14, 30, tzinfo=dt_util.get_default_time_zone()
    )
    due_dt_ms = int(dt_util.as_utc(due_dt_local).timestamp() * 1000)
    completed_ms = int(
        dt.datetime(2026, 11, 5, 18, 0, tzinfo=dt.UTC).timestamp() * 1000
    )

    mock_schedule.list_groups.return_value.__aiter__.return_value = [
        ScheduleTaskGroupItem(task_list_id="group-1", title="Work"),
        ScheduleTaskGroupItem(
            task_list_id="group-deleted",
            title="Deleted Group",
            is_deleted=BooleanEnum.YES,
        ),
    ]
    mock_schedule.list_tasks.return_value.__aiter__.return_value = [
        ScheduleTaskInfo(
            task_id="task-inbox-1",
            task_list_id=None,
            title="Review notes",
            detail="Check weekly journal",
            status="needsAction",
            due_time=due_date_ms,
            sort=2,
        ),
        ScheduleTaskInfo(
            task_id="task-inbox-2",
            task_list_id="0",
            title="Legacy inbox item",
            status="needsAction",
            due_time=0,
            sort=1,
        ),
        ScheduleTaskInfo(
            task_id="task-inbox-deleted",
            task_list_id=None,
            title="Deleted task",
            status="needsAction",
            is_deleted=BooleanEnum.YES,
        ),
        ScheduleTaskInfo(
            task_id="task-work-1",
            task_list_id="group-1",
            title="Ship todo integration",
            detail="Write unit tests",
            status="needsAction",
            due_time=due_dt_ms,
        ),
        ScheduleTaskInfo(
            task_id="task-work-2",
            task_list_id="group-1",
            title="Update dependencies",
            status="completed",
            completed_time=completed_ms,
        ),
    ]

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Deleted group must not be created
    assert hass.states.get("todo.supernote_user_name_deleted_group") is None

    # Inbox has 2 incomplete items (sorted by sort: task-inbox-2 then task-inbox-1)
    inbox_state = hass.states.get("todo.supernote_user_name_inbox")
    assert inbox_state is not None
    assert inbox_state.state == "2"

    inbox_resp = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: "todo.supernote_user_name_inbox"},
        blocking=True,
        return_response=True,
    )
    inbox_items = inbox_resp["todo.supernote_user_name_inbox"]["items"]
    assert inbox_items == [
        {
            "uid": "task-inbox-2",
            "summary": "Legacy inbox item",
            "status": "needs_action",
        },
        {
            "uid": "task-inbox-1",
            "summary": "Review notes",
            "status": "needs_action",
            "description": "Check weekly journal",
            "due": "2026-11-10",
        },
    ]

    # Work group has 1 incomplete item and 1 completed item
    work_state = hass.states.get("todo.supernote_user_name_work")
    assert work_state is not None
    assert work_state.state == "1"

    work_resp = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: "todo.supernote_user_name_work"},
        blocking=True,
        return_response=True,
    )
    work_items = work_resp["todo.supernote_user_name_work"]["items"]
    assert work_items == [
        {
            "uid": "task-work-1",
            "summary": "Ship todo integration",
            "status": "needs_action",
            "description": "Write unit tests",
            "due": due_dt_local.isoformat(),
        },
        {
            "uid": "task-work-2",
            "summary": "Update dependencies",
            "status": "completed",
            "completed": "2026-11-05T18:00:00+00:00",
        },
    ]


@pytest.mark.usefixtures("setup_integration")
async def test_dynamic_group_add_rename_and_remove_without_spurious_zero(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_schedule: AsyncMock,
) -> None:
    """Test dynamically adding, renaming, and deleting groups without emitting state '0'."""
    ent_reg = er.async_get(hass)

    assert hass.states.get("todo.supernote_user_name_inbox") is not None
    assert hass.states.get("todo.supernote_user_name_shopping") is None

    # 1. Group added with 1 active task
    mock_schedule.list_groups.return_value.__aiter__.return_value = [
        ScheduleTaskGroupItem(task_list_id="grp-shop", title="Shopping"),
    ]
    mock_schedule.list_tasks.return_value.__aiter__.return_value = [
        ScheduleTaskInfo(
            task_id="shop-1",
            task_list_id="grp-shop",
            title="Milk",
            status="needsAction",
        ),
    ]
    await config_entry.runtime_data.todo_coordinator.async_refresh()
    await hass.async_block_till_done()

    shop_state = hass.states.get("todo.supernote_user_name_shopping")
    assert shop_state is not None
    assert shop_state.state == "1"
    assert shop_state.attributes["friendly_name"] == "Supernote (user-name) Shopping"
    assert (
        ent_reg.async_get_entity_id(
            TODO_DOMAIN, DOMAIN, f"{config_entry.entry_id}_group_grp-shop"
        )
        == "todo.supernote_user_name_shopping"
    )

    # 2. Group renamed on Supernote
    mock_schedule.list_groups.return_value.__aiter__.return_value = [
        ScheduleTaskGroupItem(task_list_id="grp-shop", title="Groceries"),
    ]
    await config_entry.runtime_data.todo_coordinator.async_refresh()
    await hass.async_block_till_done()

    shop_state = hass.states.get("todo.supernote_user_name_shopping")
    assert shop_state is not None
    assert shop_state.attributes["friendly_name"] == "Supernote (user-name) Groceries"

    # 3. Group deleted on Supernote -> must NOT emit state "0" before removal
    recorded_states: list[str | None] = []

    def _on_state_change(event: Event) -> None:
        if event.data.get("entity_id") == "todo.supernote_user_name_shopping":
            new_state = event.data.get("new_state")
            recorded_states.append(new_state.state if new_state is not None else None)

    unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, _on_state_change)
    try:
        mock_schedule.list_groups.return_value.__aiter__.return_value = []
        mock_schedule.list_tasks.return_value.__aiter__.return_value = []
        await config_entry.runtime_data.todo_coordinator.async_refresh()
        await hass.async_block_till_done()
    finally:
        unsub()

    assert "0" not in recorded_states
    assert recorded_states == [None]
    assert hass.states.get("todo.supernote_user_name_shopping") is None
    assert (
        ent_reg.async_get_entity_id(
            TODO_DOMAIN, DOMAIN, f"{config_entry.entry_id}_group_grp-shop"
        )
        is None
    )


async def test_group_id_collision_and_corrupt_timestamps_and_dst(
    hass: HomeAssistant,
    mock_supernote: AsyncMock,
    mock_schedule: AsyncMock,
) -> None:
    """Test group_id='inbox' collision safety, out-of-range timestamps, and DST midnight."""
    # America/Havana transitions DST at 00:00 -> 01:00 on 2026-03-08
    await hass.config.async_set_time_zone("America/Havana")
    dst_date = dt.date(2026, 3, 8)
    dst_due_ms = int(
        dt_util.as_utc(dt_util.start_of_local_day(dst_date)).timestamp() * 1000
    )

    mock_schedule.list_groups.return_value.__aiter__.return_value = [
        ScheduleTaskGroupItem(task_list_id="inbox", title="Custom Inbox"),
    ]
    mock_schedule.list_tasks.return_value.__aiter__.return_value = [
        ScheduleTaskInfo(
            task_id="task-builtin-inbox",
            task_list_id=None,
            title="Built-in task",
            status="needsAction",
            due_time=dst_due_ms,
        ),
        ScheduleTaskInfo(
            task_id="task-custom-inbox",
            task_list_id="inbox",
            title="Custom group task",
            status=None,
            due_time=9223372036854775807,  # Long.MAX_VALUE overflow sentinel
            completed_time=1700000000000,
        ),
    ]

    entry = _make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Both built-in Inbox and custom group_id="inbox" exist without unique_id collision
    assert hass.states.get("todo.supernote_user_name_inbox") is not None
    assert hass.states.get("todo.supernote_user_name_custom_inbox") is not None

    inbox_resp = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: "todo.supernote_user_name_inbox"},
        blocking=True,
        return_response=True,
    )
    assert inbox_resp["todo.supernote_user_name_inbox"]["items"] == [
        {
            "uid": "task-builtin-inbox",
            "summary": "Built-in task",
            "status": "needs_action",
            "due": "2026-03-08",
        }
    ]

    custom_resp = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: "todo.supernote_user_name_custom_inbox"},
        blocking=True,
        return_response=True,
    )
    # Overflow due_time gracefully ignored; status=None + completed_time > 0 -> completed
    assert custom_resp["todo.supernote_user_name_custom_inbox"]["items"] == [
        {
            "uid": "task-custom-inbox",
            "summary": "Custom group task",
            "status": "completed",
            "completed": "2023-11-14T22:13:20+00:00",
        }
    ]


async def test_stale_group_removed_on_startup(
    hass: HomeAssistant,
    mock_supernote: AsyncMock,
    mock_schedule: AsyncMock,
) -> None:
    """Test stale group entities in entity registry are cleaned up on startup."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    stale_reg_entry = ent_reg.async_get_or_create(
        TODO_DOMAIN,
        DOMAIN,
        f"{entry.entry_id}_group_old-deleted-group",
        config_entry=entry,
        suggested_object_id="supernote_user_name_old_list",
    )
    assert ent_reg.async_get(stale_reg_entry.entity_id) is not None

    mock_schedule.list_groups.return_value.__aiter__.return_value = []
    mock_schedule.list_tasks.return_value.__aiter__.return_value = []

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert ent_reg.async_get(stale_reg_entry.entity_id) is None
    assert hass.states.get("todo.supernote_user_name_inbox") is not None


async def test_todo_coordinator_setup_errors(
    hass: HomeAssistant,
    mock_supernote: AsyncMock,
    mock_schedule: AsyncMock,
) -> None:
    """Test todo coordinator ApiException/ClientError retries setup and Unauthorized triggers reauth."""
    mock_schedule.list_groups.side_effect = ClientConnectionError("Temporary outage")

    entry = _make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY

    await hass.config_entries.async_unload(entry.entry_id)

    # Now test UnauthorizedException during setup
    mock_schedule.list_groups.side_effect = UnauthorizedException("Expired")
    entry2 = _make_entry(unique_id="user-identifier-2", title="user-name-2")
    entry2.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry2.entry_id)
    assert entry2.state is ConfigEntryState.SETUP_ERROR
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(
        f["handler"] == DOMAIN and f["context"]["source"] == "reauth" for f in flows
    )
