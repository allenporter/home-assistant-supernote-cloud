"""Test the Supernote Cloud media source."""

from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.components.media_source import (
    URI_SCHEME,
    async_browse_media,
    async_resolve_media,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from supernote.client.exceptions import ApiException
from supernote.models.base import BooleanEnum
from supernote.models.file_web import FilePathQueryVO, PngPageVO, PngVO, UserFileVO

from custom_components.supernote_cloud.const import DOMAIN

from .conftest import CONFIG_ENTRY_ID, CONFIG_ENTRY_TITLE

SOURCE_TITLE = "Supernote Cloud"
ROOT_FOLDER_PATH = f"{CONFIG_ENTRY_ID}/f/0"
CONTENT_BYTES = b"some-content"


@pytest.fixture(autouse=True)
async def setup_components(hass: HomeAssistant) -> None:
    """Fixture to initialize the integration."""
    await async_setup_component(hass, "media_source", {})


@pytest.mark.usefixtures("setup_integration")
async def test_no_config_entries(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test a media source with no active config entry."""

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}")

    assert browse.domain == DOMAIN
    assert browse.identifier is None
    assert browse.title == SOURCE_TITLE
    assert browse.can_expand
    assert not browse.children


@pytest.mark.usefixtures("setup_integration")
async def test_invalid_path(hass: HomeAssistant) -> None:
    """Test browsing to a path with an incorrect format."""
    with pytest.raises(BrowseError, match="Could not parse identifier"):
        await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/invalid-config-entry")

    with pytest.raises(BrowseError, match="Could not find config entry"):
        await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/invalid-config-entry/f/0")


@pytest.mark.usefixtures("setup_integration")
async def test_browse_invalid_path(hass: HomeAssistant) -> None:
    """Test browsing to an invalid node is not possible."""
    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}")
    assert browse.domain == DOMAIN
    assert browse.identifier is None
    assert browse.title == SOURCE_TITLE
    assert [(child.identifier, child.title) for child in browse.children] == [
        (ROOT_FOLDER_PATH, CONFIG_ENTRY_TITLE)
    ]

    with pytest.raises(BrowseError, match="Could not parse identifier"):
        await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{CONFIG_ENTRY_ID}/f")

    with pytest.raises(BrowseError, match="Could not parse identifier"):
        await async_browse_media(
            hass, f"{URI_SCHEME}{DOMAIN}/{CONFIG_ENTRY_ID}/q/some-id"
        )


@pytest.mark.usefixtures("setup_integration")
async def test_browse_folders(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test browsing the top level folder list."""
    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}")
    assert browse.domain == DOMAIN
    assert browse.identifier is None
    assert browse.title == SOURCE_TITLE
    assert [(child.identifier, child.title) for child in browse.children] == [
        (ROOT_FOLDER_PATH, CONFIG_ENTRY_TITLE)
    ]

    # Browse folders
    mock_supernote.web.list_query.return_value.user_file_vo_list = [
        UserFileVO(
            id="1111111111111111",
            directory_id="0",
            file_name="Folder title",
            is_folder=BooleanEnum.YES,
        )
    ]
    mock_supernote.web.path_query.return_value = FilePathQueryVO(path="/Folder title")

    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{ROOT_FOLDER_PATH}")
    assert browse.domain == DOMAIN
    assert browse.identifier == ROOT_FOLDER_PATH
    assert browse.title == CONFIG_ENTRY_TITLE
    folder_path = f"{ROOT_FOLDER_PATH}/1111111111111111"
    assert [(child.identifier, child.title) for child in browse.children] == [
        (folder_path, "Folder title"),
    ]

    # Browse contents of a subfolder
    mock_supernote.web.list_query.return_value.user_file_vo_list = [
        UserFileVO(
            id="33333333333",
            directory_id="1111111111111111",
            file_name="Note title.note",
            is_folder=BooleanEnum.NO,
            size=12345,
            md5="abcdef",
        )
    ]

    # Mock path_query for the current folder
    mock_supernote.web.path_query.return_value = FilePathQueryVO(
        path="/Folder title/Subfolder"
    )

    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{folder_path}")
    assert browse.domain == DOMAIN
    assert browse.identifier == folder_path

    assert browse.title == "Subfolder"  # Matches mock above

    note_path = f"{CONFIG_ENTRY_ID}/n/1111111111111111/33333333333"
    assert [(child.identifier, child.title) for child in browse.children] == [
        (note_path, "Note title.note")
    ]

    # Browse into a note
    mock_supernote.web.list_query.return_value.user_file_vo_list = [
        UserFileVO(
            id="33333333333",
            directory_id="1111111111111111",
            file_name="Note title.note",
            is_folder=BooleanEnum.NO,
            size=12345,
            md5="abcdef",
        )
    ]

    mock_supernote.device.note_to_png.return_value = PngVO(
        png_page_vo_list=[
            PngPageVO(page_no=1, url="http://example.com/1.png"),
            PngPageVO(page_no=2, url="http://example.com/2.png"),
        ]
    )

    # Mock list_folder for resolution
    mock_file_info = MagicMock()
    mock_file_info.id = "33333333333"
    mock_folder = MagicMock()
    mock_folder.entries = [mock_file_info]
    mock_supernote.device.list_folder = AsyncMock(return_value=mock_folder)

    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{note_path}")
    assert browse.domain == DOMAIN
    assert browse.identifier == note_path
    assert browse.title == "Note title.note"
    page_path_prefix = f"{CONFIG_ENTRY_ID}/p/1111111111111111/33333333333"
    assert [(child.identifier, child.title) for child in browse.children] == [
        (f"{page_path_prefix}/0", "Page 1"),
        (f"{page_path_prefix}/1", "Page 2"),
    ]

    # Browse into a note page
    browse = await async_browse_media(
        hass, f"{URI_SCHEME}{DOMAIN}/{page_path_prefix}/1"
    )
    assert browse.domain == DOMAIN
    assert browse.identifier == f"{page_path_prefix}/1"
    assert browse.title == "Page 2"
    assert not browse.children

    # Resolve media
    mock_supernote.client.get_content.return_value = b"page2_content"

    media = await async_resolve_media(
        hass, f"{URI_SCHEME}{DOMAIN}/{page_path_prefix}/1", None
    )

    client = await hass_client()
    response = await client.get(media.url)
    assert response.status == HTTPStatus.OK
    contents = await response.read()
    assert contents == b"page2_content"


@pytest.mark.usefixtures("setup_integration")
async def test_browse_folder_as_file(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test browsing with the wrong object time."""
    browse = await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}")
    assert browse.domain == DOMAIN
    assert browse.identifier is None
    assert browse.title == SOURCE_TITLE

    mock_supernote.web.path_query.return_value = FilePathQueryVO(path="")

    # Mock list_query to return a folder
    mock_supernote.web.list_query.return_value.user_file_vo_list = [
        UserFileVO(
            id="1111111111111111",
            directory_id="222222222",
            file_name="Folder title",
            is_folder=BooleanEnum.YES,
        )
    ]

    invalid_note_path = f"{CONFIG_ENTRY_ID}/n/222222222/1111111111111111"

    with pytest.raises(BrowseError, match="Expected file but got folder"):
        await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{invalid_note_path}")


@pytest.mark.usefixtures("setup_integration")
async def test_item_content_invalid_identifier(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
    hass_client: ClientSessionGenerator,
) -> None:
    """Test fetching content for invalid ids."""

    client = await hass_client()
    response = await client.get("/api/supernote_cloud/item_content/invalid")
    assert response.status == HTTPStatus.BAD_REQUEST

    client = await hass_client()
    response = await client.get(
        f"/api/supernote_cloud/item_content/{CONFIG_ENTRY_ID}:n:1111111111111111:33333333333"
    )
    assert response.status == HTTPStatus.BAD_REQUEST

    client = await hass_client()
    response = await client.get(
        "/api/supernote_cloud/item_content/invalid-entry:p:1111111111111111:33333333333:0"
    )
    assert response.status == HTTPStatus.BAD_REQUEST

    mock_file_info = MagicMock()
    mock_file_info.id = "33333333333"

    mock_folder = MagicMock()
    mock_folder.entries = [mock_file_info]

    mock_supernote.device.list_folder = AsyncMock(return_value=mock_folder)
    mock_supernote.device.note_to_png.return_value = PngVO(
        png_page_vo_list=[PngPageVO(page_no=1, url="http://example.com/1.png")]
    )
    mock_supernote.client.get_content.return_value = b"page1"

    client = await hass_client()
    response = await client.get(
        f"/api/supernote_cloud/item_content/{CONFIG_ENTRY_ID}:p:1111111111111111:33333333333:99"
    )
    assert response.status == HTTPStatus.NOT_FOUND


@pytest.mark.usefixtures("setup_integration")
async def test_authentication_error(
    hass: HomeAssistant,
    mock_supernote: MagicMock,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
) -> None:
    """Test browsing the top level folder list."""
    assert config_entry.state is ConfigEntryState.LOADED

    # Browse folders
    mock_supernote.web.list_query.side_effect = ApiException("Test error")

    with pytest.raises(BrowseError, match="Failed to fetch folder contents"):
        await async_browse_media(hass, f"{URI_SCHEME}{DOMAIN}/{ROOT_FOLDER_PATH}")
        await hass.async_block_till_done()

    # Check if list_query was actually called
    assert mock_supernote.web.list_query.called
