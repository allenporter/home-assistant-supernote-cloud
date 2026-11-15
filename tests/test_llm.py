"""Test the Supernote Cloud LLM tools."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry
from supernote.client.exceptions import UnauthorizedException
from supernote.models.extended import (
    PageTranscriptVO,
    SearchResultVO,
    WebSearchResponseVO,
    WebTranscriptResponseVO,
)

from custom_components.supernote_cloud.const import DOMAIN
from custom_components.supernote_cloud.llm import SearchTool, TranscriptTool


@pytest.fixture(name="search_tool")
def search_tool_fixture(config_entry: MockConfigEntry) -> SearchTool:
    """Fixture for SearchTool."""
    return SearchTool(config_entry)


@pytest.fixture(name="transcript_tool")
def transcript_tool_fixture(config_entry: MockConfigEntry) -> TranscriptTool:
    """Fixture for TranscriptTool."""
    return TranscriptTool(config_entry)


@pytest.mark.usefixtures("mock_supernote")
async def test_search_tool_initialization(search_tool: SearchTool):
    """Test initializing the search tool."""
    assert search_tool.name == "search_supernote"
    assert (
        search_tool.description
        == "Search for content in Supernote notebooks using semantic search."
    )


@pytest.mark.usefixtures("mock_supernote")
async def test_transcript_tool_initialization(transcript_tool: TranscriptTool):
    """Test initializing the transcript tool."""
    assert transcript_tool.name == "get_supernote_transcript"
    assert (
        transcript_tool.description
        == "Retrieve the text transcript for a Supernote notebook by file ID."
    )


@pytest.mark.usefixtures("mock_supernote")
async def test_search_tool_call_success(
    hass: HomeAssistant,
    search_tool: SearchTool,
    mock_supernote: AsyncMock,
):
    """Test calling the search tool successfully."""
    # Setup mock response
    mock_result = SearchResultVO(
        file_id=123,
        file_name="Test Note",
        page_index=1,
        page_id="page_1",
        score=0.95,
        text_preview="This is a test note content.",
        date="2023-10-27",
    )
    mock_response = WebSearchResponseVO(results=[mock_result])

    # Configure post_json to return the response
    mock_supernote.client.post_json = AsyncMock(return_value=mock_response)

    tool_input = llm.ToolInput(
        tool_name="search_supernote", tool_args={"query": "test query", "top_n": 3}
    )

    result = await search_tool.async_call(hass, tool_input, AsyncMock())

    assert result == {
        "results": [
            {
                "file_name": "Test Note",
                "page_index": 1,
                "text_preview": "This is a test note content.",
                "score": 0.95,
                "date": "2023-10-27",
            }
        ]
    }

    # Verify post_json call
    mock_supernote.client.post_json.assert_called_once()
    args, kwargs = mock_supernote.client.post_json.call_args
    assert args[0] == "/api/extended/search"
    assert kwargs["json"]["query"] == "test query"
    assert kwargs["json"]["topN"] == 3


@pytest.mark.usefixtures("mock_supernote")
async def test_transcript_tool_call_success(
    hass: HomeAssistant,
    transcript_tool: TranscriptTool,
    mock_supernote: AsyncMock,
):
    """Test calling the transcript tool successfully."""
    # Setup mock response
    mock_response = WebTranscriptResponseVO(
        pages=[
            PageTranscriptVO(
                page_index=0,
                page_id="page_0",
                text="This is the full transcript.",
                date="2023-10-27",
            )
        ]
    )

    # Configure post_json to return the response
    mock_supernote.client.post_json = AsyncMock(return_value=mock_response)

    tool_input = llm.ToolInput(
        tool_name="get_supernote_transcript", tool_args={"file_id": 12345}
    )

    result = await transcript_tool.async_call(hass, tool_input, AsyncMock())

    assert result == {
        "pages": [
            {
                "page_index": 0,
                "text": "This is the full transcript.",
                "date": "2023-10-27",
            }
        ],
    }

    # Verify post_json call
    mock_supernote.client.post_json.assert_called_once()
    args, kwargs = mock_supernote.client.post_json.call_args
    assert args[0] == "/api/extended/transcript"
    assert kwargs["json"]["fileId"] == "12345"


@pytest.mark.usefixtures("mock_supernote")
async def test_search_tool_call_error(
    hass: HomeAssistant,
    search_tool: SearchTool,
    mock_supernote: AsyncMock,
):
    """Test calling the search tool with an error."""
    mock_supernote.client.post_json = AsyncMock(side_effect=Exception("API Error"))

    tool_input = llm.ToolInput(
        tool_name="search_supernote", tool_args={"query": "test query"}
    )

    result = await search_tool.async_call(hass, tool_input, AsyncMock())

    assert result == {"error": "Error searching Supernote: API Error"}


@pytest.mark.usefixtures("mock_supernote")
async def test_transcript_tool_call_error(
    hass: HomeAssistant,
    transcript_tool: TranscriptTool,
    mock_supernote: AsyncMock,
):
    """Test calling the transcript tool with an error."""
    mock_supernote.client.post_json = AsyncMock(side_effect=Exception("API Error"))

    tool_input = llm.ToolInput(
        tool_name="get_supernote_transcript", tool_args={"file_id": 12345}
    )

    result = await transcript_tool.async_call(hass, tool_input, AsyncMock())

    assert result == {"error": "Error fetching Supernote transcript: API Error"}


@pytest.mark.usefixtures("mock_supernote")
async def test_search_tool_auth_error(
    hass: HomeAssistant,
    search_tool: SearchTool,
    mock_supernote: AsyncMock,
):
    """Test calling the search tool with an authentication error."""
    mock_supernote.client.post_json = AsyncMock(
        side_effect=UnauthorizedException("Unauthorized")
    )

    tool_input = llm.ToolInput(
        tool_name="search_supernote", tool_args={"query": "test query"}
    )

    result = await search_tool.async_call(hass, tool_input, AsyncMock())

    assert result == {"error": "Supernote authentication failed: Unauthorized"}

    # Verify that the reauth flow was started
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"


@pytest.mark.usefixtures("mock_supernote")
async def test_transcript_tool_auth_error(
    hass: HomeAssistant,
    transcript_tool: TranscriptTool,
    mock_supernote: AsyncMock,
):
    """Test calling the transcript tool with an authentication error."""
    mock_supernote.client.post_json = AsyncMock(
        side_effect=UnauthorizedException("Unauthorized")
    )

    tool_input = llm.ToolInput(
        tool_name="get_supernote_transcript", tool_args={"file_id": 12345}
    )

    result = await transcript_tool.async_call(hass, tool_input, AsyncMock())

    assert result == {"error": "Supernote authentication failed: Unauthorized"}

    # Verify that the reauth flow was started
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"
