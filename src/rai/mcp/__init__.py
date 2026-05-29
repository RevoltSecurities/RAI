"""RAI MCP package — Model Context Protocol tool loading."""

from rai.mcp.session import MCPToolInfo, MCPServerInfo, MCPSessionManager
from rai.mcp.config import (
    load_mcp_config,
    merge_mcp_configs,
    discover_mcp_configs,
)
from rai.mcp.loader import (
    _load_tools_from_config,
    load_subagent_mcp_tools,
    load_subagents_mcp_tools_map,
    resolve_and_load_mcp_tools,
)

__all__ = [
    "MCPToolInfo",
    "MCPServerInfo",
    "MCPSessionManager",
    "load_mcp_config",
    "merge_mcp_configs",
    "discover_mcp_configs",
    "_load_tools_from_config",
    "load_subagent_mcp_tools",
    "load_subagents_mcp_tools_map",
    "resolve_and_load_mcp_tools",
]
