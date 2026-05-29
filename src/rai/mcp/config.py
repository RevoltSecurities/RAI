"""MCP config loading, validation, and discovery."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STREAMABLE_HTTP_ALIASES = {"http", "streamable-http", "streamable_http"}
_SUPPORTED_REMOTE_TYPES = {"sse"} | _STREAMABLE_HTTP_ALIASES


def _normalize_transport(t: str) -> str:
    """Canonicalize all streamable-http spellings to ``"streamable-http"``."""
    return "streamable-http" if t in _STREAMABLE_HTTP_ALIASES else t


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def _resolve_transport(server_config: dict[str, Any]) -> str:
    t = server_config.get("type")
    if t is not None:
        return _normalize_transport(t)
    t = server_config.get("transport")
    if t is not None:
        return _normalize_transport(t)
    # Auto-detect: explicit url field without transport → streamable-http
    if "url" in server_config and "command" not in server_config:
        return "streamable-http"
    # Auto-detect: command field that is a URL → streamable-http
    cmd = server_config.get("command", "")
    if isinstance(cmd, str) and cmd.startswith(("http://", "https://")):
        return "streamable-http"
    return "stdio"


def _validate_server_config(name: str, cfg: dict[str, Any]) -> None:
    if not isinstance(cfg, dict):
        raise TypeError(f"Server '{name}' config must be a dict")
    transport = _resolve_transport(cfg)
    if transport in _SUPPORTED_REMOTE_TYPES:
        # Accept 'url' field or 'command' that was auto-detected as a URL.
        _url = cfg.get("url") or (cfg.get("command") if isinstance(cfg.get("command"), str) and cfg.get("command", "").startswith(("http://", "https://")) else None)
        if not _url:
            raise ValueError(f"Server '{name}' ({transport}) missing 'url'")
        if "headers" in cfg and not isinstance(cfg["headers"], dict):
            raise TypeError(f"Server '{name}' headers must be a dict")
    elif transport == "stdio":
        if "command" not in cfg:
            raise ValueError(f"Server '{name}' (stdio) missing 'command'")
        if "args" in cfg and not isinstance(cfg["args"], list):
            raise TypeError(f"Server '{name}' args must be a list")
        if "env" in cfg and not isinstance(cfg["env"], dict):
            raise TypeError(f"Server '{name}' env must be a dict")
    else:
        raise ValueError(
            f"Server '{name}' unsupported transport '{transport}'. "
            "Use stdio, sse, http, streamable-http, or streamable_http."
        )


def load_mcp_config(path: str | Path) -> dict[str, Any]:
    """Load and validate a .mcp.json config file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"MCP config not found: {path}")
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON in {path}: {e.msg}", e.doc, e.pos) from e

    if "mcpServers" not in data:
        raise ValueError(f"MCP config {path} must have 'mcpServers' key")
    if not isinstance(data["mcpServers"], dict):
        raise TypeError("'mcpServers' must be a dict")
    for sname, scfg in data["mcpServers"].items():
        _validate_server_config(sname, scfg)
    return data


def merge_mcp_configs(configs: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multiple configs; later entries override earlier for the same server name."""
    merged: dict[str, Any] = {}
    for cfg in configs:
        servers = cfg.get("mcpServers")
        if isinstance(servers, dict):
            merged.update(servers)
    return {"mcpServers": merged}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_mcp_configs(cwd: Path | None = None, agent_name: str = "") -> list[Path]:
    """Return existing .mcp.json paths in lowest-to-highest precedence order.

    Search order:
      1. ~/.rai/.mcp.json                    (user-level global, always trusted)
      2. ~/.rai/agents/<name>/mcp.json       (per-agent config, when agent_name is given)
      3. <cwd>/.rai/.mcp.json               (project RAI config)
      4. <cwd>/.mcp.json                    (claude-code compat, project-level)
    """
    base = (cwd or Path.cwd()).resolve()
    candidates: list[Path] = [Path.home() / ".rai" / ".mcp.json"]

    if agent_name:
        from rai.config.settings import settings as _settings
        candidates.append(_settings.agent_dir(agent_name) / "mcp.json")

    candidates += [
        base / ".rai" / ".mcp.json",
        base / ".mcp.json",
    ]

    found: list[Path] = []
    for p in candidates:
        try:
            if p.is_file():
                found.append(p)
        except OSError:
            logger.warning("Could not check MCP config %s", p, exc_info=True)
    return found
