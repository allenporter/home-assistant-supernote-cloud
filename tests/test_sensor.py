"""Tests for the Supernote Cloud sensor platform."""

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from supernote.client.exceptions import ApiException
from supernote.models.file_device import AllocationVO, CapacityLocalVO

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
