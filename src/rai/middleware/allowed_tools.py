"""AllowedToolsMiddleware — hard enforcement of the per-run tool whitelist (B3).

When a run is created with `allowed_tools=["bash", "http_request"]`, the agent
must only call those tools.  Previously this was observational — a
`permission_denied` SSE was emitted but the tool still executed.

This middleware enforces the whitelist at the call layer: disallowed tool calls
never reach the tool implementation and receive an error ToolMessage instead.

The allowed list is communicated via a ContextVar set by execute_run() before
the first _stream_once() call.  Each asyncio.Task inherits its own copy so
concurrent runs with different whitelists are fully isolated.

Fail-open on internal errors — if the ContextVar cannot be read or any
unexpected exception occurs the tool call proceeds normally.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

# Set by execute_run() to the per-run allowed list (or None = unrestricted).
# Each asyncio.Task created by execute_run() inherits its own copy automatically.
_allowed_tools_ctx: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "rai_allowed_tools", default=None
)


def set_allowed_tools(allowed: list[str] | None) -> contextvars.Token:
    """Set the allowed-tools list for the current asyncio task.

    Returns the contextvars Token so execute_run() can reset it in finally.
    """
    return _allowed_tools_ctx.set(allowed)


def reset_allowed_tools(token: contextvars.Token) -> None:
    """Reset the contextvar to its previous value using the token."""
    _allowed_tools_ctx.reset(token)


class AllowedToolsMiddleware(AgentMiddleware):
    """Hard-enforce the per-run allowed_tools whitelist.

    When `allowed_tools` is None (unrestricted), this middleware is a zero-cost
    pass-through — the ContextVar default is None and the fast-path returns
    immediately without touching the tool call.

    When a whitelist is active, disallowed tools receive a ToolMessage with
    status="error" and never reach their implementation.  The error message
    tells the agent which tools it may use so it can self-correct.
    """

    def _check(self, request: Any) -> ToolMessage | None:
        """Return a blocking ToolMessage if the tool is not allowed, else None."""
        allowed = _allowed_tools_ctx.get(None)
        if allowed is None:
            return None  # unrestricted — fast path

        tc = request.tool_call if hasattr(request, "tool_call") else {}
        if not isinstance(tc, dict):
            return None

        tool_name = tc.get("name", "") or ""
        tool_call_id = tc.get("id", "") or ""

        if tool_name in allowed:
            return None  # allowed — proceed normally

        logger.warning(
            "AllowedToolsMiddleware: blocked '%s' — not in allowed_tools=%s",
            tool_name, allowed,
        )
        return ToolMessage(
            content=(
                f"Tool '{tool_name}' is not permitted for this run.\n"
                f"Allowed tools: {', '.join(sorted(allowed)) or '(none)'}.\n"
                "Use only the listed tools to complete this task."
            ),
            tool_call_id=tool_call_id,
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            blocked = self._check(request)
            if blocked is not None:
                return blocked
        except Exception:
            logger.debug("AllowedToolsMiddleware.wrap_tool_call error", exc_info=True)
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            blocked = self._check(request)
            if blocked is not None:
                return blocked
        except Exception:
            logger.debug("AllowedToolsMiddleware.awrap_tool_call error", exc_info=True)
        return await handler(request)
