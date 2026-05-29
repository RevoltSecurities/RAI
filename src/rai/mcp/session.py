"""MCP session manager and data types."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_mcp_adapters.client import MultiServerMCPClient


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class MCPToolInfo:
    name: str
    description: str


@dataclass
class MCPServerInfo:
    name: str
    transport: str
    tools: list[MCPToolInfo] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------


class MCPSessionManager:
    """Manages persistent MCP client sessions (same pattern as deepagents-cli)."""

    def __init__(self) -> None:
        self.client: MultiServerMCPClient | None = None
        self.exit_stack = AsyncExitStack()

    async def cleanup(self) -> None:
        await self.exit_stack.aclose()
