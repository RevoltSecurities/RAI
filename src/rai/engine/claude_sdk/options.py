"""Build ClaudeAgentOptions from RAI config.

Priority resolution (highest wins):
  1. Explicit arguments passed to build_claude_options()
  2. ~/.rai/agents/{name}/prompt.md  (system prompt)
  3. Bundled RAI prompt (system prompt)
  4. ~/.rai/agents/{name}/AGENTS.md subagent definitions
  5. ~/.rai/agents/{name}/.mcp.json + ~/.rai/.mcp.json  (MCP)
  6. load_agent_config(agent_name) model  (model)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MODEL_PREFIX_STRIP = (
    "litellm:anthropic/",
    "litellm:anthropic:",
    "litellm:",
    "anthropic/",
    "anthropic:",
    "openai/",
    "openai:",
)

_MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
    "haiku": "claude-haiku-4-5-20251001",
}


def _translate_model(model_str: str) -> str | None:
    """Strip provider prefix and return bare Claude model ID; None for non-Claude."""
    if not model_str or model_str.lower() in {"inherit", ""}:
        return None
    s = model_str.strip()
    for prefix in _MODEL_PREFIX_STRIP:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s in _MODEL_ALIASES:
        return _MODEL_ALIASES[s]
    if s.startswith("claude-"):
        return s
    return None


def _load_mcp_json(path: Path) -> dict[str, Any]:
    """Load a .mcp.json file and return the mcpServers dict (or empty dict)."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", data)
        if isinstance(servers, dict):
            return servers
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Could not read MCP config %s: %s", path, exc)
    return {}


def _mcp_entry_to_config(name: str, cfg: Any) -> tuple[str, dict[str, Any]] | None:
    """Convert a raw MCP server entry to (name, McpServerConfig) or None if invalid."""
    if not isinstance(cfg, dict):
        return None
    server_type = cfg.get("type", "stdio")
    if server_type in ("sse", "http"):
        url = cfg.get("url", "")
        if not url:
            return None
        result: dict[str, Any] = {"type": server_type, "url": url}
        if "headers" in cfg:
            result["headers"] = cfg["headers"]
        return name, result
    # stdio (default)
    command = cfg.get("command", "")
    if not command:
        return None
    result = {"command": command}
    if "args" in cfg:
        result["args"] = cfg["args"]
    if "env" in cfg:
        result["env"] = cfg["env"]
    return name, result


def _load_mcp_servers(agent_name: str) -> dict[str, Any]:
    """Load and merge agent-level + global MCP configs."""
    from rai.config.settings import settings

    agent_mcp = settings.agent_dir(agent_name) / "mcp.json"
    global_mcp = settings.user_rai_dir / ".mcp.json"

    raw: dict[str, Any] = {}
    raw.update(_load_mcp_json(global_mcp))
    raw.update(_load_mcp_json(agent_mcp))  # agent-level wins

    result: dict[str, Any] = {}
    for name, cfg in raw.items():
        converted = _mcp_entry_to_config(name, cfg)
        if converted:
            result[converted[0]] = converted[1]
        else:
            logger.debug("Skipping invalid MCP server entry '%s'", name)
    return result


def build_claude_options(
    agent_name: str = "rai",
    *,
    model: str | None = None,
    effort: str | None = None,
    system_prompt: str | None = None,
    permission_mode: str = "bypassPermissions",
    extra_hooks: dict | None = None,
    include_partial_messages: bool = True,
    session_id: str | None = None,
    resume: str | None = None,
    max_turns: int | None = None,
    mcp_servers: dict | None = None,
    agents: dict | None = None,
    can_use_tool: Any | None = None,
):
    """Build ClaudeAgentOptions from RAI config for the given agent_name.

    Returns a ClaudeAgentOptions instance ready to pass to ClaudeSDKClient or
    RunnableClaudeAgent.
    """
    from claude_agent_sdk.types import ClaudeAgentOptions
    from rai.config.agent import load_agent_config
    from rai.engine.factory import get_agent_prompt

    # ---- system prompt ----
    # Priority: explicit arg > bundled data/prompts/{name}-claude/ > bundled data/prompts/{name}/
    # NOTE: disk override (~/.rai/agents/{name}/prompt.md) is NOT checked here — that's
    # for rai chat (HTTP harness). Claude SDK always uses the bundled -claude variant
    # so the prompt matches Claude Code's native tool set.
    if system_prompt is None:
        try:
            data_dir = Path(__file__).parent.parent.parent / "data" / "prompts"
            claude_variant = data_dir / f"{agent_name}-claude" / "prompt.md"
            if claude_variant.exists():
                system_prompt = claude_variant.read_text(encoding="utf-8")
            else:
                system_prompt = get_agent_prompt(agent_name)
        except Exception as exc:
            logger.warning("Could not load system prompt for '%s': %s", agent_name, exc)
            system_prompt = ""

    # ---- subagents ----
    if agents is None:
        try:
            from rai.engine.claude_sdk.subagents import load_claude_subagents
            agents = load_claude_subagents(agent_name) or None
        except Exception as exc:
            logger.warning("Could not load subagents for '%s': %s", agent_name, exc)
            agents = None

    # ---- MCP ----
    if mcp_servers is None:
        try:
            mcp_servers = _load_mcp_servers(agent_name) or None
        except Exception as exc:
            logger.warning("Could not load MCP servers for '%s': %s", agent_name, exc)
            mcp_servers = None

    # ---- model ----
    resolved_model: str | None = None
    rate_limit_profile: str = "normal"
    if model is not None:
        resolved_model = _translate_model(model)
    else:
        try:
            cfg = load_agent_config(agent_name)
            if cfg.model:
                resolved_model = _translate_model(cfg.model)
            if cfg.rate_limit_profile:
                rate_limit_profile = cfg.rate_limit_profile
        except Exception:
            pass

    # ---- hooks ----
    try:
        from rai.engine.claude_sdk.hooks import build_default_hooks
        hooks = build_default_hooks(agent_name, rate_limit_profile)
    except Exception as exc:
        logger.warning("Could not build default hooks: %s", exc)
        hooks = None

    if extra_hooks and hooks is not None:
        for event, matchers in extra_hooks.items():
            if event in hooks:
                hooks[event].extend(matchers)
            else:
                hooks[event] = matchers
    elif extra_hooks:
        hooks = extra_hooks

    opts = ClaudeAgentOptions(
        system_prompt=system_prompt or None,
        model=resolved_model,
        effort=effort,
        permission_mode=permission_mode,
        agents=agents,
        mcp_servers=mcp_servers or {},
        hooks=hooks,
        include_partial_messages=include_partial_messages,
        session_id=session_id,
        resume=resume,
        max_turns=max_turns,
    )
    if can_use_tool is not None:
        opts.can_use_tool = can_use_tool
    return opts
