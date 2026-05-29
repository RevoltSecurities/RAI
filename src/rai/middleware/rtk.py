"""RTK (Rust Token Killer) middleware — rewrites bash commands via `rtk rewrite`.

Equivalent to the `rtk hook claude` PreToolUse hook in Claude Code, but
implemented as a native RAI AgentMiddleware so it can actually mutate the
command argument before execution.

RAI's hook system cannot do this because:
  - PreToolUse hooks are block-only (cannot rewrite args)
  - PostToolUse hooks are fire-and-forget (cannot modify the response)

This middleware calls `rtk rewrite <command>` before the bash tool runs.
`rtk rewrite` exits 0 + prints the RTK equivalent, or exits 1 with no output
when the command has no known RTK equivalent — so it's safely no-op for
unsupported commands and when rtk is not installed.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

_RTK_AVAILABLE: bool = shutil.which("rtk") is not None

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.messages import ToolMessage
    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)

_BASH_TOOLS = frozenset({"bash", "execute", "shell"})


def _rtk_rewrite(command: str) -> str | None:
    """Call `rtk rewrite <command>`; return rewritten string or None if unsupported."""
    if not _RTK_AVAILABLE:
        return None
    try:
        proc = subprocess.run(
            ["rtk", "rewrite", command],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # rtk rewrite exits 3 when it rewrites, 1 when no equivalent exists.
        # Trust stdout over exit code: if rtk printed something, use it.
        out = proc.stdout.strip()
        if out and out != command:
            return out
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _apply_rewrite(request: Any) -> None:
    """Rewrite the bash command in-place if rtk has an equivalent."""
    if not _RTK_AVAILABLE:
        return
    tool_name = (request.tool_call.get("name") or "").lower()
    if tool_name not in _BASH_TOOLS:
        return
    args: dict = request.tool_call.get("args") or {}
    command: str = args.get("command") or args.get("cmd") or ""
    if not command:
        return
    rewritten = _rtk_rewrite(command)
    if rewritten:
        logger.debug("RTKMiddleware: %r → %r", command, rewritten)
        try:
            args["command"] = rewritten
        except TypeError:
            pass  # frozen args dict — skip silently


class RTKToolMiddleware(AgentMiddleware):
    """Rewrites bash commands through ``rtk rewrite`` before the tool executes.

    Falls back silently if ``rtk`` is not installed or the command has no
    known RTK equivalent (``rtk rewrite`` exits 1 for unsupported commands).
    """

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        _apply_rewrite(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if not _RTK_AVAILABLE:
            return await handler(request)
        tool_name = (request.tool_call.get("name") or "").lower()
        if tool_name in _BASH_TOOLS:
            args: dict = request.tool_call.get("args") or {}
            command: str = args.get("command") or args.get("cmd") or ""
            if command:
                rewritten = await asyncio.to_thread(_rtk_rewrite, command)
                if rewritten:
                    logger.debug("RTKMiddleware: %r → %r", command, rewritten)
                    try:
                        args["command"] = rewritten
                    except TypeError:
                        pass
        return await handler(request)
