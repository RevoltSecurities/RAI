"""SubagentRestrictMiddleware — block task tool for subagents.

deepagents' base stack always injects a `task` tool via SubAgentMiddleware,
even when RAI passes subagents=None. This causes subagents to spawn nested
sub-sub-agents — recursive delegation that wastes tokens and context.

This middleware is ONLY added when is_subagent=True. It intercepts the `task`
tool call and returns a clear rejection telling the agent to handle the work
itself instead of delegating further.

Position: 3.7 (after ReadFileDedup 3.6, before DockerRouter 4).
Only active for subagent compilations.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

_BLOCKED_TOOLS = frozenset({"task"})

_RESTRICTION_MSG = (
    "<task-tool-restriction>\n"
    "You are a subagent — the `task` tool is not available to you.\n"
    "You must handle this work directly. Do NOT attempt to delegate.\n"
    "Use your own tools (bash, read_file, write_file, etc.) to complete the task.\n"
    "</task-tool-restriction>"
)


class SubagentRestrictMiddleware(AgentMiddleware):
    """Block task tool calls for subagents.

    Prevents recursive subagent spawning by intercepting deepagents' built-in
    `task` tool. Only added to the middleware stack when is_subagent=True.
    Fail-open: exceptions pass through to normal execution.
    """

    def _check(self, request: Any) -> ToolMessage | None:
        tc = request.tool_call if hasattr(request, "tool_call") else {}
        if not isinstance(tc, dict):
            return None
        name = tc.get("name", "") or ""
        if name not in _BLOCKED_TOOLS:
            return None
        tool_call_id = tc.get("id", "") or ""
        logger.debug("SubagentRestrictMiddleware: blocked '%s' tool call", name)
        return ToolMessage(content=_RESTRICTION_MSG, tool_call_id=tool_call_id)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            blocked = self._check(request)
            if blocked is not None:
                return blocked
        except Exception:
            pass
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        try:
            blocked = self._check(request)
            if blocked is not None:
                return blocked
        except Exception:
            pass
        return await handler(request)
