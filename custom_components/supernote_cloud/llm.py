"""LLM tools platform for Supernote Cloud."""

from __future__ import annotations

from typing import override

import probatio
from homeassistant.components.llm import LLMTools
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.llm import (
    LLMContext,
    Tool,
    ToolAnnotations,
    ToolInput,
    ToolResult,
)
from supernote.client.exceptions import UnauthorizedException
from supernote.client.extended import ExtendedClient

from .const import DOMAIN
from .types import SupernoteCloudConfigEntry

API_ID_PREFIX = f"{DOMAIN}-"
API_PROMPT = (
    "You have access to the user's Supernote notebooks via search and transcript tools."
)

READ_ONLY_CLOUD_ANNOTATIONS = ToolAnnotations(
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=True,
)


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """Return the Supernote LLM tools for a Supernote API instance."""
    _ = llm_context
    if not api_id.startswith(API_ID_PREFIX):
        return None

    entry_id = api_id.removeprefix(API_ID_PREFIX)
    entry: SupernoteCloudConfigEntry | None = hass.config_entries.async_get_entry(
        entry_id
    )
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
    ):
        return None

    return LLMTools(
        tools=[
            SearchTool(entry),
            TranscriptTool(entry),
        ],
        prompt=API_PROMPT,
    )


class SearchTool(Tool):
    """Supernote search tool."""

    name = f"{DOMAIN}__search"
    title = "Search Supernote notebooks"
    description = "Search for content in Supernote notebooks using semantic search."
    integration = DOMAIN
    annotations = READ_ONLY_CLOUD_ANNOTATIONS
    parameters = probatio.Schema(
        {
            probatio.Required("query", description="The search query."): cv.string,
            probatio.Optional(
                "top_n", default=5, description="Number of results to return."
            ): probatio.Coerce(int),
            probatio.Optional(
                "name_filter", description="Filter by notebook name substring."
            ): cv.string,
            probatio.Optional(
                "date_after",
                description="Filter results created after this date (YYYY-MM-DD).",
            ): cv.string,
            probatio.Optional(
                "date_before",
                description="Filter results created before this date (YYYY-MM-DD).",
            ): cv.string,
        }
    )

    def __init__(self, entry: SupernoteCloudConfigEntry) -> None:
        """Initialize the tool."""
        self._entry = entry

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Call the tool."""
        _ = llm_context
        args = self.parameters(tool_input.tool_args)

        sn = self._entry.runtime_data.client
        extended = ExtendedClient(sn.client)

        try:
            results = await extended.search(
                query=args["query"],
                top_n=args.get("top_n", 5),
                name_filter=args.get("name_filter"),
                date_after=args.get("date_after"),
                date_before=args.get("date_before"),
            )
        except ConfigEntryAuthFailed as err:
            return ToolResult(
                data={"error": f"Supernote authentication failed: {err}"},
                error=True,
            )
        except UnauthorizedException as err:
            self._entry.async_start_reauth(hass)
            return ToolResult(
                data={"error": f"Supernote authentication failed: {err}"},
                error=True,
            )
        except Exception as err:  # noqa: BLE001
            return ToolResult(
                data={"error": f"Error searching Supernote: {err}"},
                error=True,
            )

        return ToolResult(
            data={
                "results": [
                    {
                        "file_name": r.file_name,
                        "page_index": r.page_index,
                        "text_preview": r.text_preview,
                        "score": r.score,
                        "date": r.date,
                    }
                    for r in results.results
                ]
            }
        )


class TranscriptTool(Tool):
    """Supernote transcript tool."""

    name = f"{DOMAIN}__get_transcript"
    title = "Get Supernote notebook transcript"
    description = "Retrieve the text transcript for a Supernote notebook by file ID."
    integration = DOMAIN
    annotations = READ_ONLY_CLOUD_ANNOTATIONS
    parameters = probatio.Schema(
        {
            probatio.Required(
                "file_id", description="The ID of the notebook."
            ): probatio.Coerce(int),
            probatio.Optional(
                "start_index",
                description="Optional 0-based start page index (inclusive).",
            ): probatio.Coerce(int),
            probatio.Optional(
                "end_index",
                description="Optional 0-based end page index (inclusive).",
            ): probatio.Coerce(int),
        }
    )

    def __init__(self, entry: SupernoteCloudConfigEntry) -> None:
        """Initialize the tool."""
        self._entry = entry

    @override
    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Call the tool."""
        _ = llm_context
        args = self.parameters(tool_input.tool_args)

        sn = self._entry.runtime_data.client
        extended = ExtendedClient(sn.client)

        try:
            result = await extended.get_transcript(
                file_id=args["file_id"],
                start_index=args.get("start_index"),
                end_index=args.get("end_index"),
            )
        except ConfigEntryAuthFailed as err:
            return ToolResult(
                data={"error": f"Supernote authentication failed: {err}"},
                error=True,
            )
        except UnauthorizedException as err:
            self._entry.async_start_reauth(hass)
            return ToolResult(
                data={"error": f"Supernote authentication failed: {err}"},
                error=True,
            )
        except Exception as err:  # noqa: BLE001
            return ToolResult(
                data={"error": f"Error fetching Supernote transcript: {err}"},
                error=True,
            )

        return ToolResult(
            data={
                "pages": [
                    {
                        "page_index": page.page_index,
                        "text": page.text,
                        "date": page.date,
                    }
                    for page in result.pages
                ],
            }
        )
