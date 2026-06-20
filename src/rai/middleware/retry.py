"""RetryMiddleware — auto-retry tool calls on transient failures.

Catches network timeouts, HTTP 429/502/503 errors from tool execution and
retries with exponential backoff. Only retries tools known to make external
network requests (http_request, web_search, nuclei_scan, nmap_scan).

Never retries: bash, memory_*, findings_*, opplan_*, read_file, write_file.

Configurable via env vars:
  RAI_RETRY_MAX   — max retry attempts (default: 3)
  RAI_RETRY_TOOLS — comma-separated tool names to retry (overrides default list)
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)

_MAX_RETRIES_DEFAULT = 3
_BACKOFF_BASE = 1.0  # seconds

_DEFAULT_RETRYABLE_TOOLS = frozenset({
    "http_request",
    "web_search",
    "web_fetch",
})

_TRANSIENT_INDICATORS = (
    "timed out",
    "timeout",
    "connect timeout",
    "read timeout",
    "connection reset",
    "connection refused",
    "429",
    "502",
    "503",
    "rate limit",
    "too many requests",
    "temporary failure",
    "network unreachable",
    "name resolution",
)


def _is_transient(error_text: str) -> bool:
    """Check if an error message indicates a transient/retryable failure."""
    lower = error_text.lower()
    return any(indicator in lower for indicator in _TRANSIENT_INDICATORS)


class RetryMiddleware(AgentMiddleware):
    """Auto-retry tool calls that fail with transient errors.

    Retries with exponential backoff (1s, 2s, 4s, ...) up to max_retries.
    Only retries tools in the retryable set. Fails open — if uncertain, does not retry.
    """

    def __init__(
        self,
        max_retries: int | None = None,
        retryable_tools: set[str] | None = None,
    ) -> None:
        super().__init__()
        self._max_retries = max_retries or int(
            os.environ.get("RAI_RETRY_MAX", str(_MAX_RETRIES_DEFAULT))
        )
        env_tools = os.environ.get("RAI_RETRY_TOOLS", "")
        if retryable_tools is not None:
            self._retryable = retryable_tools
        elif env_tools:
            self._retryable = set(t.strip() for t in env_tools.split(",") if t.strip())
        else:
            self._retryable = set(_DEFAULT_RETRYABLE_TOOLS)

    async def awrap_tool_call(
        self,
        request: "ToolCallRequest",
        handler: Callable[["ToolCallRequest"], Awaitable["ToolMessage | Command"]],
    ) -> "ToolMessage | Command":
        tool_name = request.name if hasattr(request, "name") else ""
        if tool_name not in self._retryable:
            return await handler(request)

        last_result = None
        for attempt in range(self._max_retries + 1):
            try:
                result = await handler(request)
            except Exception as exc:
                error_text = str(exc)
                if attempt < self._max_retries and _is_transient(error_text):
                    delay = _BACKOFF_BASE * (2 ** attempt)
                    logger.info(
                        "Retry %d/%d for %s (error: %s, backoff: %.1fs)",
                        attempt + 1, self._max_retries, tool_name, error_text[:80], delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

            # Check if the result is a ToolMessage with transient error content
            if isinstance(result, ToolMessage):
                content = result.content if isinstance(result.content, str) else str(result.content)
                if _is_transient(content) and attempt < self._max_retries:
                    delay = _BACKOFF_BASE * (2 ** attempt)
                    logger.info(
                        "Retry %d/%d for %s (transient in output, backoff: %.1fs)",
                        attempt + 1, self._max_retries, tool_name, delay,
                    )
                    last_result = result
                    await asyncio.sleep(delay)
                    continue

            return result

        # Exhausted retries — return last result
        return last_result  # type: ignore[return-value]
