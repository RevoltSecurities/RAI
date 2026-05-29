"""rai.sdk.mcp — MCP tool loading and subagent MCP map helpers.

    from rai.sdk.mcp import load_mcp_tools, load_subagents_mcp_tools_map
"""

from rai.mcp.loader import resolve_and_load_mcp_tools as load_mcp_tools
from rai.mcp.loader import load_subagents_mcp_tools_map

__all__ = ["load_mcp_tools", "load_subagents_mcp_tools_map"]
