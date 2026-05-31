"""MCP async tool loading functions."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rai.mcp.session import MCPServerInfo, MCPSessionManager, MCPToolInfo
from rai.mcp.config import (
    _resolve_transport,
    _SUPPORTED_REMOTE_TYPES,
    load_mcp_config,
    merge_mcp_configs,
    discover_mcp_configs,
)

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


def _check_stdio_server(name: str, cfg: dict[str, Any]) -> None:
    command = cfg.get("command")
    if not command:
        raise RuntimeError(f"MCP server '{name}': missing 'command'")
    if not shutil.which(command):
        raise RuntimeError(
            f"MCP server '{name}': command '{command}' not found on PATH. "
            "Install it or check your MCP config."
        )


async def _check_remote_server(name: str, cfg: dict[str, Any]) -> bool:
    """Return True if SSL verification had to be skipped (self-signed / proxy cert)."""
    import httpx

    url = cfg.get("url") or (cfg.get("command") if isinstance(cfg.get("command"), str) and cfg.get("command", "").startswith(("http://", "https://")) else None)
    if not url:
        raise RuntimeError(f"MCP server '{name}': missing 'url'")
    try:
        async with httpx.AsyncClient() as client:
            await client.head(url, timeout=5)
        return False
    except httpx.ConnectError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or "SSL" in str(e):
            # Retry without SSL verification (corporate proxy / self-signed cert).
            logger.debug("MCP server '%s': SSL verify failed, retrying without verification", name)
            try:
                async with httpx.AsyncClient(verify=False) as client:
                    await client.head(url, timeout=5)
                logger.warning(
                    "MCP server '%s': SSL certificate could not be verified (self-signed or proxy cert). "
                    "Connecting with verify=False.",
                    name,
                )
                return True
            except (httpx.TransportError, httpx.InvalidURL, OSError) as e2:
                raise RuntimeError(f"MCP server '{name}': URL '{url}' unreachable: {e2}") from e2
        raise RuntimeError(f"MCP server '{name}': URL '{url}' unreachable: {e}") from e
    except (httpx.TransportError, httpx.InvalidURL, OSError) as e:
        raise RuntimeError(f"MCP server '{name}': URL '{url}' unreachable: {e}") from e


# ---------------------------------------------------------------------------
# Tool loading
# ---------------------------------------------------------------------------


async def _load_tools_from_config(
    config: dict[str, Any],
) -> tuple[list[BaseTool], MCPSessionManager, list[MCPServerInfo]]:
    """Build MCP connections and load tools with per-server fault isolation.

    Each server is checked and loaded independently.  A failing server is
    logged as a warning and skipped — it never aborts the remaining servers,
    agents, subagents, or startup.  Only raises if every configured server
    failed to load.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.sessions import (
        SSEConnection,
        StdioConnection,
        StreamableHttpConnection,
    )
    from langchain_mcp_adapters.tools import load_mcp_tools

    servers: dict[str, Any] = config["mcpServers"]
    if not servers:
        return [], MCPSessionManager(), []

    total = len(servers)

    # ── Phase 1: per-server pre-flight ────────────────────────────────────
    healthy: dict[str, Any] = {}
    ssl_skip: set[str] = set()  # servers that need verify=False due to proxy/self-signed cert
    for sname, scfg in servers.items():
        transport = _resolve_transport(scfg)
        try:
            if transport in _SUPPORTED_REMOTE_TYPES:
                needed_ssl_skip = await _check_remote_server(sname, scfg)
                if needed_ssl_skip:
                    ssl_skip.add(sname)
            else:
                _check_stdio_server(sname, scfg)
            healthy[sname] = scfg
        except RuntimeError as exc:
            logger.warning("MCP server '%s' failed pre-flight check — skipping: %s", sname, exc)

    if not healthy:
        raise RuntimeError(
            f"All {total} MCP server(s) failed pre-flight checks. "
            "Check commands are on PATH and remote URLs are reachable."
        )

    # ── Phase 2: build connections only for healthy servers ───────────────
    def _unverified_client_factory(
        headers: dict | None = None,
        timeout: Any = None,
        auth: Any = None,
    ) -> Any:
        import httpx
        kwargs: dict[str, Any] = {"verify": False}
        if headers:
            kwargs["headers"] = headers
        if timeout is not None:
            kwargs["timeout"] = timeout
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    connections: dict[str, Any] = {}
    for sname, scfg in healthy.items():
        transport = _resolve_transport(scfg)
        no_verify = sname in ssl_skip or not scfg.get("verify_ssl", True)
        if transport == "streamable-http":
            _url = scfg.get("url") or scfg.get("command", "")
            conn: Any = StreamableHttpConnection(transport="streamable_http", url=_url)
            if "headers" in scfg:
                conn["headers"] = scfg["headers"]
            if no_verify:
                conn["httpx_client_factory"] = _unverified_client_factory
        elif transport == "sse":
            _url = scfg.get("url") or scfg.get("command", "")
            conn = SSEConnection(transport="sse", url=_url)
            if "headers" in scfg:
                conn["headers"] = scfg["headers"]
            if no_verify:
                conn["httpx_client_factory"] = _unverified_client_factory
        else:
            conn = StdioConnection(
                command=scfg["command"],
                args=scfg.get("args", []),
                env=scfg.get("env") or None,
                transport="stdio",
            )
        connections[sname] = conn

    manager = MCPSessionManager()
    try:
        client = MultiServerMCPClient(connections=connections)
        manager.client = client
    except Exception as exc:
        await manager.cleanup()
        raise RuntimeError(f"Failed to initialize MCP client: {exc}") from exc

    # ── Phase 3: per-server session open + tool load ───────────────────────
    all_tools: list[BaseTool] = []
    server_infos: list[MCPServerInfo] = []
    failed_sessions: list[str] = []

    for sname, scfg in healthy.items():
        try:
            session = await manager.exit_stack.enter_async_context(
                client.session(sname)
            )
            tools = await load_mcp_tools(session, server_name=sname, tool_name_prefix=True)
            all_tools.extend(tools)
            server_infos.append(MCPServerInfo(
                name=sname,
                transport=_resolve_transport(scfg),
                tools=[MCPToolInfo(name=t.name, description=t.description or "") for t in tools],
            ))
            logger.info("MCP server '%s': loaded %d tool(s)", sname, len(tools))
        except Exception as exc:
            failed_sessions.append(sname)
            # Unwrap ExceptionGroup (Python 3.11+ TaskGroup wraps sub-errors)
            # so the log shows the real cause, not just "1 sub-exception".
            causes: list[str] = []
            queue = [exc]
            while queue:
                e = queue.pop()
                if hasattr(e, "exceptions") and e.exceptions:
                    queue.extend(e.exceptions)
                else:
                    causes.append(f"{type(e).__name__}: {e}")
            detail = "; ".join(causes) if causes else str(exc)
            logger.warning(
                "MCP server '%s' failed to open session — skipping: %s. "
                "Remaining servers are unaffected.",
                sname, detail,
            )

    if failed_sessions:
        logger.warning(
            "%d/%d MCP server(s) skipped due to errors: %s",
            len(failed_sessions), total, ", ".join(failed_sessions),
        )

    if not all_tools and healthy:
        await manager.cleanup()
        raise RuntimeError(
            f"All {len(healthy)} MCP server(s) passed pre-flight but failed "
            "to open sessions. Check MCP server logs for details."
        )

    # Sort for stable tool ordering (cache-friendly for prompt caching)
    all_tools.sort(key=lambda t: t.name)
    logger.info(
        "MCP ready: %d tool(s) from %d/%d server(s)",
        len(all_tools), len(server_infos), total,
    )
    return all_tools, manager, server_infos


async def load_subagent_mcp_tools(
    subagent_name: str,
) -> tuple[list[BaseTool], MCPSessionManager | None]:
    """Load MCP tools for a named subagent from ``~/.rai/agents/<name>/mcp.json``.

    Returns ``(tools, session_manager)``.  Both are empty/None when no config
    exists or loading fails — errors are logged as warnings so they never
    crash the parent agent startup.
    """
    from rai.agents.loader import subagent_mcp_config_path

    mcp_path = subagent_mcp_config_path(subagent_name)
    if not mcp_path.exists():
        return [], None

    try:
        config = load_mcp_config(mcp_path)
    except Exception as exc:
        logger.warning(
            "Could not load MCP config for subagent '%s' at %s: %s",
            subagent_name, mcp_path, exc,
        )
        return [], None

    try:
        tools, manager, server_infos = await _load_tools_from_config(config)
        logger.debug(
            "Loaded %d MCP tool(s) for subagent '%s' from %d server(s)",
            len(tools), subagent_name, len(server_infos),
        )
        return tools, manager
    except Exception as exc:
        logger.warning(
            "Failed to load MCP tools for subagent '%s': %s",
            subagent_name, exc,
        )
        return [], None


async def load_subagents_mcp_tools_map(
    subagent_names: list[str],
) -> tuple[dict[str, list[BaseTool]], list[MCPSessionManager]]:
    """Load MCP tools for multiple subagents concurrently.

    Args:
        subagent_names: List of subagent names to load tools for.

    Returns:
        ``(tools_map, sessions)`` where ``tools_map`` maps subagent name →
        list of BaseTool, and ``sessions`` is a flat list of all
        MCPSessionManager instances that must be cleaned up on shutdown.
    """
    import asyncio

    results = await asyncio.gather(
        *[load_subagent_mcp_tools(name) for name in subagent_names],
        return_exceptions=True,
    )

    tools_map: dict[str, list[BaseTool]] = {}
    sessions: list[MCPSessionManager] = []

    for name, result in zip(subagent_names, results):
        if isinstance(result, Exception):
            logger.warning("Subagent '%s' MCP load error: %s", name, result)
            continue
        tools, manager = result
        if tools:
            tools_map[name] = tools
        if manager is not None:
            sessions.append(manager)

    return tools_map, sessions


async def resolve_and_load_mcp_tools(
    *,
    explicit_config_path: str | None = None,
    no_mcp: bool = False,
    cwd: Path | None = None,
    agent_name: str = "",
) -> tuple[list[BaseTool], MCPSessionManager | None, list[MCPServerInfo]]:
    """Auto-discover MCP configs and load all tools.

    Args:
        explicit_config_path: Extra config to layer on top (highest precedence, errors fatal).
        no_mcp: Disable MCP loading entirely.
        cwd: Working directory for project-level config discovery.
        agent_name: When set, also loads ~/.rai/agents/<name>/mcp.json.

    Returns:
        (tools, session_manager, server_infos). Empty tuple when no config found.
    """
    if no_mcp:
        return [], None, []

    config_paths = discover_mcp_configs(cwd=cwd, agent_name=agent_name)
    configs: list[dict[str, Any]] = []

    for p in config_paths:
        try:
            configs.append(load_mcp_config(p))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Skipping invalid MCP config %s: %s", p, e)

    if explicit_config_path:
        configs.append(load_mcp_config(explicit_config_path))

    if not configs:
        return [], None, []

    merged = merge_mcp_configs(configs)
    if not merged.get("mcpServers"):
        return [], None, []

    try:
        return await _load_tools_from_config(merged)
    except RuntimeError as exc:
        logger.error(
            "MCP loading failed entirely — starting without MCP tools. "
            "Fix the server config and restart. Details: %s",
            exc,
        )
        return [], None, []
