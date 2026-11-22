"""Tests for the Supernote Cloud sensor platform."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ACCESS_TOKEN, UnitOfInformation
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from supernote.client.exceptions import ApiException, UnauthorizedException
from supernote.models.file_device import AllocationVO, CapacityLocalVO
from supernote.models.schedule import ScheduleTaskGroupItem, ScheduleTaskInfo

from custom_components.supernote_cloud.const import DOMAIN

from .conftest import CONFIG_ENTRY_ID


@pytest.mark.usefixtures("setup_integration")
async def test_sensors_loaded(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_supernote: MagicMock,
) -> None:
    """Test that storage sensors are loaded and report values correctly."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Mock get_capacity response
    mock_capacity = CapacityLocalVO(
        used=10 * 1024 * 1024 * 1024,  # 10 GB
        allocation_vo=AllocationVO(
            tag="personal",
            allocated=50 * 1024 * 1024 * 1024,  # 50 GB
        ),
    )
    mock_supernote.device.get_capacity.return_value = mock_capacity

    data = config_entry.runtime_data
    assert data is not None
    coordinator = data.coordinator
    assert coordinator is not None

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Verify used space sensor
    state = hass.states.get("sensor.supernote_user_name_storage_used")
    assert state is not None
    assert float(state.state) == 10.0
    assert state.attributes.get("unit_of_measurement") == UnitOfInformation.GIGABYTES
    assert state.attributes.get("device_class") == SensorDeviceClass.DATA_SIZE

    # Verify total space sensor
    state = hass.states.get("sensor.supernote_user_name_storage_total")
    assert state is not None
    assert float(state.state) == 50.0
    assert state.attributes.get("unit_of_measurement") == UnitOfInformation.GIGABYTES
    assert state.attributes.get("device_class") == SensorDeviceClass.DATA_SIZE

    # Verify free space sensor
    state = hass.states.get("sensor.supernote_user_name_storage_free")
    assert state is not None
    assert float(state.state) == 40.0
    assert state.attributes.get("unit_of_measurement") == UnitOfInformation.GIGABYTES
    assert state.attributes.get("device_class") == SensorDeviceClass.DATA_SIZE

    # Verify usage percent sensor
    state = hass.states.get("sensor.supernote_user_name_storage_usage_ratio")
    assert state is not None
    assert float(state.state) == 20.0
    assert state.attributes.get("unit_of_measurement") == "%"


@pytest.mark.usefixtures("mock_supernote")
async def test_setup_entry_not_ready(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
) -> None:
    """Test config entry setup fails when get_capacity raises ApiException."""
    mock_supernote.device.get_capacity.side_effect = ApiException("API Error")

    config_entry = MockConfigEntry(
        unique_id=CONFIG_ENTRY_ID,
        title="user-name",
        domain=DOMAIN,
        options={
            "access_token": "access-token-1",
            "token_timestamp": 1609502400,
            "unique_id": CONFIG_ENTRY_ID,
            "username": "user-name",
            "password": "some-password",
        },
    )
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.usefixtures("mock_supernote")
async def test_setup_entry_unauthorized_triggers_reauth(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
) -> None:
    """Test config entry setup triggers reauth when get_capacity raises UnauthorizedException."""
    mock_supernote.device.get_capacity.side_effect = UnauthorizedException(
        "Token expired"
    )

    config_entry = MockConfigEntry(
        unique_id=CONFIG_ENTRY_ID,
        title="user-name",
        domain=DOMAIN,
        options={
            "access_token": "access-token-1",
            "token_timestamp": 1609502400,
            "unique_id": CONFIG_ENTRY_ID,
            "username": "user-name",
            "password": "some-password",
        },
    )
    config_entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR

    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"


@pytest.mark.usefixtures("setup_integration")
async def test_zero_allocation_vo(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_supernote: MagicMock,
) -> None:
    """Test zero total capacity allocation handled safely."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Mock capacity with zero allocation
    mock_capacity = CapacityLocalVO(
        used=0,
        allocation_vo=None,
    )
    mock_supernote.device.get_capacity.return_value = mock_capacity

    data = config_entry.runtime_data
    assert data is not None
    coordinator = data.coordinator
    assert coordinator is not None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Total space should be 0
    state = hass.states.get("sensor.supernote_user_name_storage_total")
    assert state is not None
    assert float(state.state) == 0.0

    # Usage ratio should be 0
    state = hass.states.get("sensor.supernote_user_name_storage_usage_ratio")
    assert state is not None
    assert float(state.state) == 0.0


@pytest.mark.usefixtures("setup_integration")
async def test_storage_coordinator_mid_ttl_401_refreshes_and_retries(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_supernote: MagicMock,
    mock_login: AsyncMock,
) -> None:
    """Test mid-TTL 401 in storage coordinator refreshes token and succeeds on retry."""
    mock_capacity = CapacityLocalVO(
        used=20 * 1024 * 1024 * 1024,
        allocation_vo=AllocationVO(
            tag="personal",
            allocated=50 * 1024 * 1024 * 1024,
        ),
    )
    mock_supernote.device.get_capacity.reset_mock()
    mock_supernote.device.get_capacity.side_effect = [
        UnauthorizedException("Invalid token"),
        mock_capacity,
    ]
    mock_login.return_value = "refreshed-token-2"

    coordinator = config_entry.runtime_data.coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert mock_login.await_count == 1
    assert mock_supernote.device.get_capacity.await_count == 2
    assert config_entry.options[CONF_ACCESS_TOKEN] == "refreshed-token-2"
    state = hass.states.get("sensor.supernote_user_name_storage_used")
    assert state is not None
    assert float(state.state) == 20.0
    assert not hass.config_entries.flow.async_progress()


@pytest.mark.usefixtures("setup_integration")
async def test_coordinators_concurrent_401_share_single_login(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_supernote: MagicMock,
    mock_schedule: AsyncMock,
    mock_login: AsyncMock,
) -> None:
    """Test concurrent 401s across storage and todo coordinators share one login."""

    async def _slow_login(username: str, password: str) -> str:
        await asyncio.sleep(0.02)
        return "shared-refreshed-token"

    mock_login.side_effect = _slow_login

    mock_capacity = CapacityLocalVO(
        used=15 * 1024 * 1024 * 1024,
        allocation_vo=AllocationVO(
            tag="personal",
            allocated=50 * 1024 * 1024 * 1024,
        ),
    )
    mock_supernote.device.get_capacity.reset_mock()
    mock_supernote.device.get_capacity.side_effect = [
        UnauthorizedException("Invalid token"),
        mock_capacity,
    ]

    async def _groups_gen():
        yield ScheduleTaskGroupItem(task_list_id="grp-1", title="Work")

    async def _tasks_gen():
        yield ScheduleTaskInfo(
            task_id="t-1",
            task_list_id="grp-1",
            title="Task",
            status="needsAction",
        )

    mock_schedule.list_groups.reset_mock()
    mock_schedule.list_groups.side_effect = [
        UnauthorizedException("Invalid token"),
        _groups_gen(),
    ]
    mock_schedule.list_tasks.side_effect = [_tasks_gen()]

    await asyncio.gather(
        config_entry.runtime_data.coordinator.async_refresh(),
        config_entry.runtime_data.todo_coordinator.async_refresh(),
    )
    await hass.async_block_till_done()

    assert config_entry.runtime_data.coordinator.last_update_success
    assert config_entry.runtime_data.todo_coordinator.last_update_success
    assert mock_login.await_count == 1
    assert config_entry.options[CONF_ACCESS_TOKEN] == "shared-refreshed-token"
    assert not hass.config_entries.flow.async_progress()


@pytest.mark.usefixtures("setup_integration")
async def test_storage_coordinator_401_login_failure_triggers_reauth(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_supernote: MagicMock,
    mock_login: AsyncMock,
) -> None:
    """Test failed login during reactive refresh triggers reauth without retry."""
    mock_supernote.device.get_capacity.reset_mock()
    mock_supernote.device.get_capacity.side_effect = UnauthorizedException(
        "Invalid token"
    )
    mock_login.side_effect = UnauthorizedException("Invalid credentials")

    coordinator = config_entry.runtime_data.coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not coordinator.last_update_success
    assert mock_login.await_count == 1
    assert mock_supernote.device.get_capacity.await_count == 1
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"
