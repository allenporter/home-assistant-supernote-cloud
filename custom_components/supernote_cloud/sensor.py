"""Sensor platform for Supernote Cloud."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from supernote.models.file_device import CapacityLocalVO

from .const import DOMAIN
from .coordinator import SupernoteStorageCoordinator
from .types import SupernoteCloudConfigEntry

# Constants for conversions
BYTES_TO_GB = 1024 * 1024 * 1024


@dataclass(frozen=True, kw_only=True)
class SupernoteSensorEntityDescription(SensorEntityDescription):
    """Class describing Supernote Cloud sensor entities."""

    value_fn: Callable[[CapacityLocalVO], float | None]


STORAGE_SENSORS: tuple[SupernoteSensorEntityDescription, ...] = (
    SupernoteSensorEntityDescription(
        key="used_space",
        name="Storage Used",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.used / BYTES_TO_GB,
    ),
    SupernoteSensorEntityDescription(
        key="total_space",
        name="Storage Total",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: (
            (data.allocation_vo.allocated if data.allocation_vo else 0) / BYTES_TO_GB
        ),
    ),
    SupernoteSensorEntityDescription(
        key="free_space",
        name="Storage Free",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: (
            max(
                0,
                (data.allocation_vo.allocated if data.allocation_vo else 0) - data.used,
            )
            / BYTES_TO_GB
        ),
    ),
    SupernoteSensorEntityDescription(
        key="usage_percent",
        name="Storage Usage Ratio",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: (
            (data.used / data.allocation_vo.allocated * 100.0)
            if data.allocation_vo and data.allocation_vo.allocated > 0
            else 0.0
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SupernoteCloudConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Supernote Cloud sensors from a config entry."""
    data = entry.runtime_data
    coordinator = data.coordinator

    async_add_entities(
        SupernoteStorageSensor(coordinator, description)
        for description in STORAGE_SENSORS
    )


class SupernoteStorageSensor(
    CoordinatorEntity[SupernoteStorageCoordinator], SensorEntity
):
    """Represents a Supernote storage sensor."""

    entity_description: SupernoteSensorEntityDescription

    def __init__(
        self,
        coordinator: SupernoteStorageCoordinator,
        description: SupernoteSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_has_entity_name = True
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=f"Supernote ({coordinator.entry.title})",
            manufacturer="Supernote",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> float | None:  # ty: ignore[invalid-property-type-override]
        """Return the value reported by the sensor."""
        data = self.coordinator.data
        if not data:
            return None
        return self.entity_description.value_fn(data)
