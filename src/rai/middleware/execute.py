"""Execute interceptor middleware — routes deepagents' execute tool through RAI BashTool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command


# ---------------------------------------------------------------------------
# Execute interceptor — routes deepagents execute → RAI BashTool
# ---------------------------------------------------------------------------


class ExecuteInterceptorMiddleware(AgentMiddleware):
    """Route every ``execute`` tool call through RAI's BashTool.

    deepagents' FilesystemMiddleware always injects an ``execute`` tool and
    there is no public API to exclude it.  This middleware intercepts calls
    to ``execute`` before they reach the backend and runs them through
    BashTool._run() instead, which adds:

    - Credential env-var stripping (*_API_KEY, *_SECRET, *_TOKEN, …)
    - [stderr] prefix on stderr lines for clear attribution
    - /tmp spill for output > 100 000 bytes (security tools produce large output)
    - POSIX exit code 124 on timeout
    - RAI_SHELL_ALLOW_LIST enforcement
    - asyncio.to_thread() async path (no deprecated get_event_loop)

    Argument mapping:
        execute.command  → bash.command       (identical)
        execute.timeout  → bash.timeout       (None → 600)
        (absent)         → bash.working_dir = ""  (inherit CWD)
    """

    def _run_bash(self, args: dict[str, Any]) -> str:
        from rai.tools.core.bash import BashTool  # lazy — avoids circular at import time
        command: str = args.get("command") or ""
        timeout: int = int(args.get("timeout") or 600)
        return BashTool()._run(command, timeout, "")

    async def _arun_bash(self, args: dict[str, Any]) -> str:
        from rai.tools.core.bash import BashTool
        command: str = args.get("command") or ""
        timeout: int = int(args.get("timeout") or 600)
        return await BashTool()._arun(command, timeout, "")

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        if request.tool_call.get("name") != "execute":
            return handler(request)
        result = self._run_bash(request.tool_call.get("args") or {})
        return ToolMessage(content=result, tool_call_id=request.tool_call["id"])

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if request.tool_call.get("name") != "execute":
            return await handler(request)
        result = await self._arun_bash(request.tool_call.get("args") or {})
        return ToolMessage(content=result, tool_call_id=request.tool_call["id"])
