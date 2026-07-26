"""Media source for viewing local copies of Supernote media."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Self, cast

from aiohttp.web import Request, Response, StreamResponse
from homeassistant.components.http.view import HomeAssistantView
from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant, callback
from supernote.client.api import Supernote
from supernote.client.exceptions import ApiException, UnauthorizedException
from supernote.models.base import BooleanEnum

from .const import DOMAIN
from .types import SupernoteCloudConfigEntry

_LOGGER = logging.getLogger(__name__)


class SupernoteIdentifierType(StrEnum):
    """Type for a SupernoteIdentifier."""

    NOTE_FILE = "n"
    NOTE_PAGE = "p"
    FOLDER = "f"

    @classmethod
    def of(cls, name: str) -> SupernoteIdentifierType:
        """Parse a SupernoteIdentifierType by string value."""
        for enum in SupernoteIdentifierType:
            if enum.value == name:
                return enum
        raise ValueError(f"Invalid SupernoteIdentifierType: {name}")


@dataclass
class SupernoteIdentifier:
    """Item identifier in a media source URL."""

    config_entry_id: str
    """Identifies the account for the media item."""

    id_type: SupernoteIdentifierType
    """Type of identifier"""

    media_id_path: list[int]
    """Identifies the folder or file contents to show."""

    @property
    def is_root(self) -> bool:
        """Return True if this is the root node."""
        return len(self.media_id_path) == 1

    @property
    def media_id(self) -> int:
        """Return the leaf media id for the identifier."""
        return self.media_id_path[-1]

    @property
    def parent_folder_id(self) -> int | None:
        """Return the parent node media id for the identifier."""
        if self.id_type == SupernoteIdentifierType.NOTE_PAGE:
            if len(self.media_id_path) < 3:
                raise ValueError(
                    f"Invalid note page identifier did not contain a parent folder id: {self}"
                )
            return self.media_id_path[-3]
        if len(self.media_id_path) < 2:
            return None
        return self.media_id_path[-2]

    @property
    def note_file_id(self) -> int | None:
        """Return the note file id for a NOTE_PAGE identifier."""
        if self.id_type == SupernoteIdentifierType.NOTE_FILE:
            return self.media_id
        if self.id_type != SupernoteIdentifierType.NOTE_PAGE:
            return None
        if len(self.media_id_path) < 2:
            raise ValueError(
                f"Invalid note page identifier did not contain a note file id: {self}"
            )
        return self.media_id_path[-2]

    @property
    def page_id(self) -> int | None:
        """Return the page id for a NOTE_PAGE identifier."""
        if self.id_type != SupernoteIdentifierType.NOTE_PAGE:
            return None
        if len(self.media_id_path) < 3:
            raise ValueError(
                f"Invalid note page identifier did not contain a page id: {self}"
            )
        return self.media_id_path[-1]

    def as_string(self, separator: str = "/") -> str:
        """Serialize the identifier as a string."""
        path_parts = separator.join(str(part) for part in self.media_id_path)
        return f"{self.config_entry_id}{separator}{self.id_type}{separator}{path_parts}"

    @classmethod
    def of(cls, identifier: str, separator: str = "/") -> Self:
        """Parse a SupernoteIdentifier form a string."""
        parts = identifier.split(separator, maxsplit=2)
        if len(parts) != 3:
            raise ValueError(f"Invalid identifier: {identifier}")
        try:
            path_parts = [int(p) for p in parts[2].split(separator)]
        except ValueError as err:
            raise ValueError(f"Invalid identifier: {identifier}") from err
        return cls(parts[0], SupernoteIdentifierType.of(parts[1]), path_parts)

    def encode(self) -> str:
        """Serialize the identifier as a url string."""
        return self.as_string(":")

    @classmethod
    def decode(cls, identifier: str) -> Self:
        """Parse a SupernoteIdentifier form a url string."""
        return cls.of(identifier, ":")

    @classmethod
    def folder(cls, config_entry_id: str, media_ids: list[int]) -> Self:
        """Create an album SupernoteIdentifier."""
        return cls(config_entry_id, SupernoteIdentifierType.FOLDER, media_ids)

    @classmethod
    def note_file(cls, config_entry_id: str, media_ids: list[int]) -> Self:
        """Create an album SupernoteIdentifier."""
        return cls(config_entry_id, SupernoteIdentifierType.NOTE_FILE, media_ids)

    @classmethod
    def note_page(cls, config_entry_id: str, media_ids: list[int]) -> Self:
        """Create an album SupernoteIdentifier."""
        return cls(config_entry_id, SupernoteIdentifierType.NOTE_PAGE, media_ids)


@callback
def async_register_http_views(hass: HomeAssistant) -> None:
    """Register the http views."""
    hass.http.register_view(ItemContentView(hass))


class ItemContentView(HomeAssistantView):
    """Returns media content for a specific media source item."""

    url = "/api/supernote_cloud/item_content/{item_identifier}"
    name = "api:supernote_cloud:item_content"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the view."""
        self.hass = hass

    async def get(self, request: Request, item_identifier: str) -> StreamResponse:
        """Start a GET request."""
        try:
            identifier = SupernoteIdentifier.decode(item_identifier)
        except ValueError as err:
            _LOGGER.error("Invalid identifier: %s", item_identifier)
            return Response(status=400, text=str(err))

        _LOGGER.debug("Fetching item content for %s", identifier)
        if identifier.id_type != SupernoteIdentifierType.NOTE_PAGE:
            msg = f"Invalid identifier type: {identifier}"
            _LOGGER.error(msg)
            return Response(status=400, text=msg)

        if (
            entry := self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN,
                identifier.config_entry_id,
            )
        ) is None:
            msg = f"Could not find config entry for identifier: {identifier.config_entry_id}"
            _LOGGER.error(msg)
            return Response(status=400, text=msg)
        sn: Supernote = entry.runtime_data.client

        try:
            folder_contents = await sn.device.list_folder(
                folder_id=identifier.parent_folder_id
            )
        except UnauthorizedException as err:
            _LOGGER.error("Authentication failed during folder list: %s", err)
            entry.async_start_reauth(self.hass)
            return Response(status=401, text=str(err))
        except ApiException as err:
            _LOGGER.error("Failed to fetch folder contents: %s", err)
            return Response(status=500, text=str(err))

        if folder_contents is None:
            msg = f"Could not find folder contents for {identifier}"
            _LOGGER.error(msg)
            return Response(status=400, text=msg)

        # Find file_info in entries
        file_info = next(
            (
                item
                for item in folder_contents.entries
                if str(item.id) == str(identifier.note_file_id)
            ),
            None,
        )
        if file_info is None:
            msg = f"Could not find file {identifier.note_file_id} in parent {identifier.parent_folder_id}"
            _LOGGER.error(msg)
            return Response(status=400, text=msg)

        try:
            png_pages = await sn.device.note_to_png(int(file_info.id))
            if (
                png_pages.png_page_vo_list is None
                or identifier.page_id is None
                or identifier.page_id >= len(png_pages.png_page_vo_list)
            ):
                return Response(status=404, text="Page not found")

            page_vo = png_pages.png_page_vo_list[identifier.page_id]
            if not page_vo.url:
                return Response(status=404, text="Page URL not found")

            content = await sn.client.get_content(page_vo.url)
            return Response(body=content, content_type="image/png")
        except UnauthorizedException as err:
            _LOGGER.error("Authentication failed during note download: %s", err)
            entry.async_start_reauth(self.hass)
            return Response(status=401, text=str(err))
        except ApiException as err:
            _LOGGER.error("Failed to fetch note content: %s", err)
            return Response(status=500, text=str(err))


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Set up Supernote Cloud media source."""
    return SupernoteCloudMediaSource(hass)


class SupernoteCloudMediaSource(MediaSource):
    """Provide Supernote Cloud as media sources."""

    name = "Supernote Cloud"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize Supernote Cloud source."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve media identifier to a note page url."""
        try:
            identifier = SupernoteIdentifier.of(item.identifier)
        except ValueError as err:
            raise BrowseError(f"Could not parse identifier: {item.identifier}") from err
        return PlayMedia(
            url=ItemContentView.url.format(item_identifier=identifier.encode()),
            mime_type="image/png",
        )

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return details about the media source."""
        try:
            return await self._async_browse_media(item)
        except UnauthorizedException as err:
            _LOGGER.error("Authentication failed: %s", err)
            if item.identifier:
                try:
                    identifier = SupernoteIdentifier.of(item.identifier)
                    entry = self._async_config_entry(identifier.config_entry_id)
                    entry.async_start_reauth(self.hass)
                except (ValueError, BrowseError):
                    pass
            raise BrowseError("Authentication failed") from err

    async def _async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return details about the media source.

        This renders the multi-level album structure for an account, its folders,
        or the contents of a note. This will return a BrowseMediaSource with a
        single level of children at the next level of the hierarchy.
        """
        if not item.identifier:
            # Top level view that lists all accounts.
            return BrowseMediaSource(
                domain=DOMAIN,
                identifier=None,
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaClass.IMAGE,
                title="Supernote Cloud",
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.DIRECTORY,
                children=[
                    _build_account(
                        entry,
                        SupernoteIdentifier.folder(cast(str, entry.unique_id), [0]),
                    )
                    for entry in self._async_config_entries()
                ],
            )

        # Determine the configuration entry for this item
        try:
            identifier = SupernoteIdentifier.of(item.identifier)
        except ValueError as err:
            raise BrowseError(f"Could not parse identifier: {item.identifier}") from err
        _LOGGER.debug("Browsing media for %s", identifier)
        if identifier.id_type is None:
            raise BrowseError(
                f"Invalid identifier did not contain an id type: {identifier}"
            )

        entry = self._async_config_entry(identifier.config_entry_id)
        entry_unique_id = cast(str, entry.unique_id)
        sn: Supernote = entry.runtime_data.client
        if identifier.id_type == SupernoteIdentifierType.FOLDER:
            if identifier.parent_folder_id is None:
                # This is the root folder for the account
                source = _build_folder(entry_unique_id, [0], entry.title)
            else:
                try:
                    # Identifier media_id is the folder we are looking at
                    path_info = await sn.web.path_query(identifier.media_id)
                    name = (
                        path_info.path.split("/")[-1]
                        if path_info.path
                        else f"Folder {identifier.media_id}"
                    )
                except UnauthorizedException:
                    raise
                except ApiException:
                    name = f"Folder {identifier.media_id}"

                source = _build_folder(
                    entry_unique_id,
                    [identifier.parent_folder_id, identifier.media_id],
                    name,
                )

            # Add the children of the folder
            try:
                folder_contents_vo = await sn.web.list_query(
                    directory_id=identifier.media_id, page_size=100
                )
            except UnauthorizedException:
                raise
            except ApiException as err:
                raise BrowseError(f"Failed to fetch folder contents: {err}") from err

            children = []
            for child_item in folder_contents_vo.user_file_vo_list:
                _LOGGER.debug("Item: %s", child_item)
                try:
                    item_id = int(child_item.id)
                except ValueError:
                    _LOGGER.warning(
                        "Skipping item with non-integer ID: %s", child_item.id
                    )
                    continue

                if child_item.is_folder == BooleanEnum.YES:
                    children.append(
                        _build_folder(
                            entry_unique_id,
                            [identifier.media_id, item_id],
                            child_item.file_name,
                        )
                    )
                elif child_item.file_name.lower().endswith(".note"):
                    children.append(
                        _build_file(
                            entry_unique_id,
                            identifier.media_id,
                            item_id,
                            child_item.file_name,
                        )
                    )
                # Ignore other file types

            source.children = children
            return source

        if identifier.parent_folder_id is None:
            raise ValueError("Cannot browse root folder as a note")

        # We are browsing a note file
        try:
            parent_folder_contents_vo = await sn.web.list_query(
                directory_id=identifier.parent_folder_id, page_size=100
            )
        except UnauthorizedException:
            raise
        except ApiException as err:
            raise BrowseError(f"Failed to fetch parent folder contents: {err}") from err

        # Find the file in the listing
        file_info = None
        for child_item in parent_folder_contents_vo.user_file_vo_list:
            if str(child_item.id) == str(identifier.note_file_id):
                file_info = child_item
                break

        if file_info is None:
            raise BrowseError(
                f"Could not find note file {identifier.note_file_id} in parent {identifier.parent_folder_id}"
            )

        if file_info.is_folder == BooleanEnum.YES:
            raise BrowseError(f"Expected file but got folder {file_info}")

        # Convert note to PNG to get page count
        # We can use that for count.
        try:
            conversion_res = await sn.device.note_to_png(int(file_info.id))
            # We assume pages are just numbered for now or check if API gives names?
            # PngPageVO has url.
            page_count = len(conversion_res.png_page_vo_list)
            page_names = [f"Page {i + 1}" for i in range(page_count)]
        except UnauthorizedException:
            raise
        except ApiException as err:
            raise BrowseError(f"Failed to get note info: {err}") from err
        _LOGGER.debug("Note has %s pages", len(page_names))

        if identifier.id_type is SupernoteIdentifierType.NOTE_PAGE:
            if identifier.page_id is None or identifier.page_id > len(page_names):
                raise BrowseError(
                    f"Invalid page id {identifier.page_id} for note {identifier}"
                )
            page_name = page_names[identifier.page_id]

            # Ensure we have valid ints for note_page
            p_folder_id = (
                identifier.parent_folder_id or 0
            )  # Should not happen based on checks
            p_note_id = (
                identifier.note_file_id or 0
            )  # Should not happen based on checks

            return _build_page(
                SupernoteIdentifier.note_page(
                    identifier.config_entry_id,
                    [
                        p_folder_id,
                        p_note_id,
                        identifier.page_id,
                    ],
                ).as_string(),
                page_name,
            )

        source = _build_file(
            entry_unique_id,
            identifier.parent_folder_id,
            int(file_info.id),
            file_info.file_name,
        )
        source.children = [
            _build_page(
                SupernoteIdentifier.note_page(
                    identifier.config_entry_id,
                    [identifier.parent_folder_id, identifier.media_id, page_id],
                ).as_string(),
                page_name,
            )
            for page_id, page_name in enumerate(page_names)
        ]
        return source

    def _async_config_entries(self) -> list[SupernoteCloudConfigEntry]:
        """Return all config entries that support photo library reads."""
        return list(self.hass.config_entries.async_loaded_entries(DOMAIN))

    def _async_config_entry(self, config_entry_id: str) -> SupernoteCloudConfigEntry:
        """Return a config entry with the specified id."""
        entry: SupernoteCloudConfigEntry | None = (
            self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN,
                config_entry_id,
            )
        )
        if not entry:
            raise BrowseError(
                f"Could not find config entry for identifier: {config_entry_id}"
            )
        return entry


def _build_account(
    config_entry: SupernoteCloudConfigEntry,
    identifier: SupernoteIdentifier,
) -> BrowseMediaSource:
    """Build the root node for a Supernote Cloud account for a config entry."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=identifier.as_string(),
        media_class=MediaClass.DIRECTORY,
        media_content_type=MediaClass.IMAGE,
        title=config_entry.title,
        can_play=False,
        can_expand=True,
    )


def _build_folder(
    config_entry_id: str, media_ids: list[int], name: str
) -> BrowseMediaSource:
    """Build a media item node for a folder."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=SupernoteIdentifier.folder(
            config_entry_id,
            media_ids,
        ).as_string(),
        media_class=MediaClass.APP,
        media_content_type=MediaType.APP,
        title=name,
        can_play=False,
        can_expand=True,
    )


def _build_file(
    config_entry_id: str, parent_folder_id: int, file_id: int, name: str
) -> BrowseMediaSource:
    """Build a media item node for a file."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=SupernoteIdentifier.note_file(
            config_entry_id,
            [parent_folder_id, file_id],
        ).as_string(),
        media_class=MediaClass.ALBUM,
        media_content_type=MediaClass.ALBUM,
        title=name,
        can_play=False,
        can_expand=True,
    )


def _build_page(
    identifier_str: str,
    page_name: str,
) -> BrowseMediaSource:
    """Build a media item node for a page."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=identifier_str,
        media_class=MediaClass.APP,
        media_content_type=MediaType.APP,
        title=page_name,
        can_play=True,
        can_expand=False,
    )
