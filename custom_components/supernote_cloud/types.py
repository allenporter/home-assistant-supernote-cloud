from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from supernote.client.api import Supernote

if TYPE_CHECKING:
    from .coordinator import SupernoteStorageCoordinator, SupernoteTodoCoordinator


@dataclass
class SupernoteCloudData:
    """Runtime data stored in the ConfigEntry."""

    client: Supernote
    coordinator: SupernoteStorageCoordinator
    todo_coordinator: SupernoteTodoCoordinator


type SupernoteCloudConfigEntry = ConfigEntry[SupernoteCloudData]
