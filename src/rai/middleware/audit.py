"""Audit log middleware — logs every tool call to an append-only log file."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langgraph.prebuilt.tool_node import ToolCallRequest
    from langgraph.types import Command

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit log middleware
# ---------------------------------------------------------------------------


class AuditLogMiddleware(AgentMiddleware):
    """Write every tool call (name + args) to an append-only audit log.

    The log format is newline-delimited JSON, one record per tool call.
    This middleware is read-only from the agent's perspective: it never
    modifies the request or response, only observes.
    """

    def __init__(self, log_path: str | Path) -> None:
        super().__init__()
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, record: dict[str, Any]) -> None:
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            logger.warning("AuditLogMiddleware: failed to write to %s", self._log_path)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        self._write({
            "ts": datetime.now(UTC).isoformat(),
            "tool": tool_call.get("name"),
            "args": tool_call.get("args"),
            "id": tool_call.get("id"),
        })
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "tool": tool_call.get("name"),
            "args": tool_call.get("args"),
            "id": tool_call.get("id"),
        }
        await asyncio.to_thread(self._write, record)
        return await handler(request)
