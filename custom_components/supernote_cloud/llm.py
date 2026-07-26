"""LLM APIs for Supernote Cloud."""

import logging
from typing import cast

import slugify
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.llm import (
    API,
    APIInstance,
    LLMContext,
    Tool,
    ToolInput,
    async_register_api,
)
from homeassistant.util.json import JsonObjectType
from supernote.client.exceptions import UnauthorizedException
from supernote.client.extended import ExtendedClient

from .const import DOMAIN
from .types import SupernoteCloudConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_register_llm_apis(
    hass: HomeAssistant, entry: SupernoteCloudConfigEntry
) -> None:
    """Register LLM APIs for Supernote Cloud."""
    try:
        async_register_api(
            hass,
            SupernoteLLMApi(
                hass,
                entry,
            ),
        )
    except HomeAssistantError as err:
        _LOGGER.debug("Error registering Supernote LLM APIs: %s", err)


class SearchTool(Tool):
    """Supernote search tool."""

    name = "search_supernote"
    description = "Search for content in Supernote notebooks using semantic search."
    parameters = vol.Schema(
        {
            vol.Required("query", description="The search query."): cv.string,
            vol.Optional(
                "top_n", default=5, description="Number of results to return."
            ): vol.Coerce(int),
            vol.Optional(
                "name_filter", description="Filter by notebook name substring."
            ): cv.string,
            vol.Optional(
                "date_after",
                description="Filter results created after this date (YYYY-MM-DD).",
            ): cv.string,
            vol.Optional(
                "date_before",
                description="Filter results created before this date (YYYY-MM-DD).",
            ): cv.string,
        }
    )

    def __init__(self, entry: SupernoteCloudConfigEntry) -> None:
        """Initialize the tool."""
        self._entry = entry

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> JsonObjectType:
        """Call the tool."""
        args = tool_input.tool_args

        # Instantiate ExtendedClient on the fly
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
        except UnauthorizedException as err:
            self._entry.async_start_reauth(hass)
            return {"error": f"Supernote authentication failed: {err}"}
        except Exception as err:  # noqa: BLE001
            return {"error": f"Error searching Supernote: {err}"}

        return cast(
            JsonObjectType,
            {
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
            },
        )


class TranscriptTool(Tool):
    """Supernote transcript tool."""

    name = "get_supernote_transcript"
    description = "Retrieve the text transcript for a Supernote notebook by file ID."
    parameters = vol.Schema(
        {
            vol.Required("file_id", description="The ID of the notebook."): vol.Coerce(
                int
            ),
            vol.Optional(
                "start_index",
                description="Optional 0-based start page index (inclusive).",
            ): vol.Coerce(int),
            vol.Optional(
                "end_index",
                description="Optional 0-based end page index (inclusive).",
            ): vol.Coerce(int),
        }
    )

    def __init__(self, entry: SupernoteCloudConfigEntry) -> None:
        """Initialize the tool."""
        self._entry = entry

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> JsonObjectType:
        """Call the tool."""
        args = tool_input.tool_args

        # Instantiate ExtendedClient on the fly
        sn = self._entry.runtime_data.client
        extended = ExtendedClient(sn.client)

        try:
            result = await extended.get_transcript(
                file_id=args["file_id"],
                start_index=args.get("start_index"),
                end_index=args.get("end_index"),
            )
        except UnauthorizedException as err:
            self._entry.async_start_reauth(hass)
            return {"error": f"Supernote authentication failed: {err}"}
        except Exception as err:  # noqa: BLE001
            return {"error": f"Error fetching Supernote transcript: {err}"}

        return cast(
            JsonObjectType,
            {
                "transcript": result.transcript,
            },
        )


class SupernoteLLMApi(API):
    """Supernote LLM API."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SupernoteCloudConfigEntry,
    ) -> None:
        """Initialize the LLM API."""
        self.hass = hass
        self.id = f"{DOMAIN}-{entry.entry_id}"
        self.name = slugify.slugify(f"Supernote: {entry.title}")[:16]
        self._entry = entry

    async def async_get_api_instance(self, llm_context: LLMContext) -> APIInstance:
        """Return the instance of the API."""
        return APIInstance(
            api=self,
            api_prompt="You have access to the user's Supernote notebooks via search and transcript tools.",
            llm_context=llm_context,
            tools=[
                SearchTool(self._entry),
                TranscriptTool(self._entry),
            ],
        )
