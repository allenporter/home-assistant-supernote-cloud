"""Test the Supernote Cloud LLM API and tools."""

import logging
from unittest.mock import AsyncMock

import pytest
from homeassistant.components import llm as llm_component
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from supernote.client.exceptions import UnauthorizedException
from supernote.models.extended import (
    PageTranscriptVO,
    SearchResultVO,
    WebSearchResponseVO,
    WebTranscriptResponseVO,
)

from custom_components.supernote_cloud.const import DOMAIN

SEARCH_TOOL_NAME = "supernote_cloud__search"
TRANSCRIPT_TOOL_NAME = "supernote_cloud__get_transcript"


@pytest.fixture(name="llm_context")
def llm_context_fixture() -> llm.LLMContext:
    """Return a test LLMContext."""
    return llm.LLMContext(
        platform="test",
        context=Context(),
        language="en",
        assistant="conversation",
        device_id=None,
    )


@pytest.fixture(name="api_instance")
async def api_instance_fixture(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    llm_context: llm.LLMContext,
) -> llm.APIInstance:
    """Return the registered Supernote LLM APIInstance loaded via Home Assistant."""
    api_id = f"{DOMAIN}-{config_entry.entry_id}"
    return await llm.async_get_api(hass, api_id, llm_context)


@pytest.mark.usefixtures("mock_supernote")
async def test_api_registration_and_unload(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    """Test the Supernote LLM API is registered on setup and removed on unload."""
    api_id = f"{DOMAIN}-{config_entry.entry_id}"
    api_ids = [api.id for api in llm.async_get_apis(hass)]
    assert api_id in api_ids

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    api_ids_after = [api.id for api in llm.async_get_apis(hass)]
    assert api_id not in api_ids_after


@pytest.mark.usefixtures("mock_supernote")
async def test_llm_platform_ignores_other_apis(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    llm_context: llm.LLMContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test other APIs (assist, homeassistant) do not get Supernote tools or log errors."""
    _ = config_entry
    assert await async_setup_component(hass, "homeassistant", {})
    caplog.set_level(logging.WARNING)

    assist_instance = await llm.async_get_api(hass, llm.LLM_API_ASSIST, llm_context)
    assert all(tool.integration != DOMAIN for tool in assist_instance.tools)

    mgmt_tools = await llm_component.async_get_tools(hass, llm_context, "homeassistant")
    assert all(tool.integration != DOMAIN for tool in mgmt_tools.tools)
    assert mgmt_tools.prompt is None

    unknown_entry_tools = await llm_component.async_get_tools(
        hass, llm_context, f"{DOMAIN}-nonexistent"
    )
    assert all(tool.integration != DOMAIN for tool in unknown_entry_tools.tools)
    assert unknown_entry_tools.prompt is None

    merged_instance = await llm.async_get_api(
        hass,
        [llm.LLM_API_ASSIST, f"{DOMAIN}-{config_entry.entry_id}"],
        llm_context,
    )
    assert any(tool.integration == DOMAIN for tool in merged_instance.tools)

    llm_warnings = [
        r
        for r in caplog.records
        if r.name in ("homeassistant.components.llm", "homeassistant.helpers.frame")
    ]
    assert llm_warnings == []


@pytest.mark.usefixtures("mock_supernote")
async def test_tools_metadata_and_no_warnings(
    api_instance: llm.APIInstance,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test tools loaded through the LLM API have full metadata and log no warnings."""
    assert "Supernote notebooks" in api_instance.api_prompt
    tools_by_name = {
        tool.name: tool for tool in api_instance.tools if tool.integration == DOMAIN
    }
    assert set(tools_by_name) == {SEARCH_TOOL_NAME, TRANSCRIPT_TOOL_NAME}

    expected_annotations = llm.ToolAnnotations(
        read_only=True,
        destructive=False,
        idempotent=True,
        open_world=True,
    )

    search_tool = tools_by_name[SEARCH_TOOL_NAME]
    assert search_tool.integration == DOMAIN
    assert search_tool.title == "Search Supernote notebooks"
    assert (
        search_tool.description
        == "Search for content in Supernote notebooks using semantic search."
    )
    assert search_tool.annotations == expected_annotations

    transcript_tool = tools_by_name[TRANSCRIPT_TOOL_NAME]
    assert transcript_tool.integration == DOMAIN
    assert transcript_tool.title == "Get Supernote notebook transcript"
    assert (
        transcript_tool.description
        == "Retrieve the text transcript for a Supernote notebook by file ID."
    )
    assert transcript_tool.annotations == expected_annotations

    frame_warnings = [
        r
        for r in caplog.records
        if r.name in ("homeassistant.components.llm", "homeassistant.helpers.frame")
        and r.levelno >= logging.WARNING
    ]
    assert frame_warnings == []


@pytest.mark.usefixtures("mock_supernote")
async def test_search_tool_call_success(
    api_instance: llm.APIInstance,
    mock_supernote: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test calling the search tool through the LLM APIInstance."""
    caplog.set_level(logging.WARNING)
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
    mock_supernote.client.post_json = AsyncMock(return_value=mock_response)

    tool_input = llm.ToolInput(
        tool_name=SEARCH_TOOL_NAME,
        tool_args={"query": "test query", "top_n": 3},
    )

    result = await api_instance.async_call_tool(tool_input)

    assert result == llm.ToolResult(
        data={
            "results": [
                {
                    "file_name": "Test Note",
                    "page_index": 1,
                    "text_preview": "This is a test note content.",
                    "score": 0.95,
                    "date": "2023-10-27",
                }
            ]
        },
        error=False,
    )

    mock_supernote.client.post_json.assert_called_once()
    args, kwargs = mock_supernote.client.post_json.call_args
    assert args[0] == "/api/extended/search"
    assert kwargs["json"]["query"] == "test query"
    assert kwargs["json"]["topN"] == 3
    assert [r for r in caplog.records if r.name == "homeassistant.helpers.frame"] == []


@pytest.mark.usefixtures("mock_supernote")
async def test_transcript_tool_call_success(
    api_instance: llm.APIInstance,
    mock_supernote: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test calling the transcript tool through the LLM APIInstance."""
    caplog.set_level(logging.WARNING)
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
    mock_supernote.client.post_json = AsyncMock(return_value=mock_response)

    tool_input = llm.ToolInput(
        tool_name=TRANSCRIPT_TOOL_NAME,
        tool_args={"file_id": 12345},
    )

    result = await api_instance.async_call_tool(tool_input)

    assert result == llm.ToolResult(
        data={
            "pages": [
                {
                    "page_index": 0,
                    "text": "This is the full transcript.",
                    "date": "2023-10-27",
                }
            ],
        },
        error=False,
    )

    mock_supernote.client.post_json.assert_called_once()
    args, kwargs = mock_supernote.client.post_json.call_args
    assert args[0] == "/api/extended/transcript"
    assert kwargs["json"]["fileId"] == "12345"
    assert [r for r in caplog.records if r.name == "homeassistant.helpers.frame"] == []


@pytest.mark.usefixtures("mock_supernote")
async def test_search_tool_call_error(
    api_instance: llm.APIInstance,
    mock_supernote: AsyncMock,
) -> None:
    """Test calling the search tool with an error."""
    mock_supernote.client.post_json = AsyncMock(side_effect=Exception("API Error"))

    tool_input = llm.ToolInput(
        tool_name=SEARCH_TOOL_NAME,
        tool_args={"query": "test query"},
    )

    result = await api_instance.async_call_tool(tool_input)

    assert result == llm.ToolResult(
        data={"error": "Error searching Supernote: API Error"},
        error=True,
    )


@pytest.mark.usefixtures("mock_supernote")
async def test_transcript_tool_call_error(
    api_instance: llm.APIInstance,
    mock_supernote: AsyncMock,
) -> None:
    """Test calling the transcript tool with an error."""
    mock_supernote.client.post_json = AsyncMock(side_effect=Exception("API Error"))

    tool_input = llm.ToolInput(
        tool_name=TRANSCRIPT_TOOL_NAME,
        tool_args={"file_id": 12345},
    )

    result = await api_instance.async_call_tool(tool_input)

    assert result == llm.ToolResult(
        data={"error": "Error fetching Supernote transcript: API Error"},
        error=True,
    )


@pytest.mark.usefixtures("mock_supernote")
async def test_search_tool_auth_error(
    hass: HomeAssistant,
    api_instance: llm.APIInstance,
    mock_supernote: AsyncMock,
) -> None:
    """Test calling the search tool with an authentication error."""
    mock_supernote.client.post_json = AsyncMock(
        side_effect=UnauthorizedException("Unauthorized")
    )

    tool_input = llm.ToolInput(
        tool_name=SEARCH_TOOL_NAME,
        tool_args={"query": "test query"},
    )

    result = await api_instance.async_call_tool(tool_input)

    assert result == llm.ToolResult(
        data={"error": "Supernote authentication failed: Unauthorized"},
        error=True,
    )

    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"


@pytest.mark.usefixtures("mock_supernote")
async def test_transcript_tool_auth_error(
    hass: HomeAssistant,
    api_instance: llm.APIInstance,
    mock_supernote: AsyncMock,
) -> None:
    """Test calling the transcript tool with an authentication error."""
    mock_supernote.client.post_json = AsyncMock(
        side_effect=UnauthorizedException("Unauthorized")
    )

    tool_input = llm.ToolInput(
        tool_name=TRANSCRIPT_TOOL_NAME,
        tool_args={"file_id": 12345},
    )

    result = await api_instance.async_call_tool(tool_input)

    assert result == llm.ToolResult(
        data={"error": "Supernote authentication failed: Unauthorized"},
        error=True,
    )

    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["handler"] == DOMAIN
    assert flows[0]["context"]["source"] == "reauth"
