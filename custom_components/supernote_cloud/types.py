from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from supernote.client.api import Supernote

if TYPE_CHECKING:
    from .coordinator import SupernoteStorageCoordinator


@dataclass
class SupernoteCloudData:
    """Runtime data stored in the ConfigEntry."""

    client: Supernote
    coordinator: SupernoteStorageCoordinator


type SupernoteCloudConfigEntry = ConfigEntry[SupernoteCloudData]
