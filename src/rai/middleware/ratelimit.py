"""Rate limiting middleware — per-tool delay profiles for stealth/normal/aggressive modes."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)

PROFILES: dict[str, dict[str, float]] = {
    "aggressive": {},
    "normal": {
        "nmap_scan": 3,
        "nuclei_scan": 1,
        "http_request": 0.5,
    },
    "stealth": {
        "nmap_scan": 10,
        "nuclei_scan": 5,
        "http_request": 2,
        "web_search": 3,
        "web_fetch": 2,
    },
}


class RateLimitMiddleware(AgentMiddleware):
    """Enforce minimum delay between consecutive calls to the same tool.

    Pre-handler: sleeps if the tool was called too recently.
    Profiles: 'aggressive' (no delay), 'normal', 'stealth'.
    """

    def __init__(self, profile: str = "normal", overrides: dict[str, float] | None = None) -> None:
        super().__init__()
        self._delays: dict[str, float] = dict(PROFILES.get(profile, PROFILES["normal"]))
        if overrides:
            self._delays.update(overrides)
        self._last_call: dict[str, float] = {}

    def _wait_sync(self, tool_name: str) -> None:
        min_delay = self._delays.get(tool_name, 0)
        if min_delay <= 0:
            return
        last = self._last_call.get(tool_name, 0)
        elapsed = time.monotonic() - last
        remaining = min_delay - elapsed
        if remaining > 0:
            logger.debug("RateLimitMiddleware: sleeping %.1fs before %s", remaining, tool_name)
            time.sleep(remaining)
        self._last_call[tool_name] = time.monotonic()

    async def _wait_async(self, tool_name: str) -> None:
        min_delay = self._delays.get(tool_name, 0)
        if min_delay <= 0:
            return
        last = self._last_call.get(tool_name, 0)
        elapsed = time.monotonic() - last
        remaining = min_delay - elapsed
        if remaining > 0:
            logger.debug("RateLimitMiddleware: sleeping %.1fs before %s", remaining, tool_name)
            await asyncio.sleep(remaining)
        self._last_call[tool_name] = time.monotonic()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        self._wait_sync(request.tool_call.get("name", ""))
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        await self._wait_async(request.tool_call.get("name", ""))
        return await handler(request)
