"""AGENTS.md → AgentDefinition converter for claude-agent-sdk."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_INHERIT = {"inherit", ""}

_MODEL_PREFIX_STRIP = (
    "litellm:anthropic/",
    "litellm:anthropic:",
    "litellm:",
    "anthropic/",
    "anthropic:",
    "openai/",
    "openai:",
)


def _translate_model(model_str: str) -> str | None:
    """Strip provider prefix and return bare Claude model ID.

    Returns None for non-Claude models or empty/inherit values so the
    claude-agent-sdk CLI uses its own default.
    """
    if not model_str or model_str in _INHERIT:
        return None

    s = model_str.strip()
    for prefix in _MODEL_PREFIX_STRIP:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break

    # Common shorthand aliases
    _ALIASES: dict[str, str] = {
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-8",
        "haiku": "claude-haiku-4-5-20251001",
    }
    if s in _ALIASES:
        return _ALIASES[s]

    if s.startswith("claude-"):
        return s

    # Not a recognisable Claude model — let CLI pick the default
    return None


# The "rai" agent is always the main/core agent — never register it as a subagent.
# Its system prompt would conflict with the Claude SDK's native prompt injection.
_ALWAYS_EXCLUDE: frozenset[str] = frozenset({"rai"})


def _load_subagent_mcp(subdir: Path) -> list[str | dict] | None:
    """Build the mcpServers list for an AgentDefinition from two sources:

    1. ~/.rai/agents/{name}/.mcp.json — each server becomes an inline
       {server_name: config} dict so the subagent gets its own dedicated tools.
    2. ``mcp_servers:`` key in the AGENTS.md extra fields — comma-separated
       server name strings that reference servers already configured in the
       parent session (e.g. global ~/.rai/.mcp.json entries).

    Returns None when neither source has anything so AgentDefinition.mcpServers
    stays None and the SDK omits the field from the initialize wire message.
    """
    entries: list[str | dict] = []

    # Source 1: per-subagent .mcp.json
    mcp_json = subdir / "mcp.json"
    if mcp_json.exists():
        try:
            data = json.loads(mcp_json.read_text(encoding="utf-8"))
            servers: dict = data.get("mcpServers", data) if isinstance(data, dict) else {}
            for name, cfg in servers.items():
                if isinstance(cfg, dict):
                    entries.append({name: cfg})
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Could not read %s: %s", mcp_json, exc)

    return entries if entries else None


def _parse_mcp_server_names(raw: str) -> list[str]:
    """Parse a comma-separated list of MCP server name references."""
    return [s.strip() for s in raw.split(",") if s.strip()]


def load_claude_subagents(
    agent_name: str,
    *,
    exclude: frozenset[str] | None = None,
) -> dict:
    """Scan ~/.rai/agents/ and return dict[name, AgentDefinition] for every subagent.

    Reuses parse_agents_md() and the same directory-scan logic as load_subagents_for().
    Each subdirectory under ~/.rai/agents/ (except agent_name itself and the entries in
    _ALWAYS_EXCLUDE) that contains AGENTS.md becomes an AgentDefinition entry.

    Args:
        agent_name: The calling agent's own directory name — always skipped.
        exclude: Additional names to skip (merged with _ALWAYS_EXCLUDE).
    """
    from claude_agent_sdk.types import AgentDefinition
    from rai.agents.parser import parse_agents_md
    from rai.config.settings import settings

    skip: frozenset[str] = _ALWAYS_EXCLUDE | ({agent_name} if agent_name else frozenset())
    if exclude:
        skip = skip | exclude

    agents_root = settings.agents_dir
    result: dict[str, AgentDefinition] = {}

    if not agents_root.exists():
        return result

    for subdir in sorted(agents_root.iterdir()):
        if not subdir.is_dir() or subdir.name in skip:
            continue

        agents_md = subdir / "AGENTS.md"
        if not agents_md.exists():
            continue

        entries = parse_agents_md(agents_md)
        if not entries:
            continue

        entry = next(
            (e for e in entries if e.name == subdir.name),
            entries[0],
        )

        if not entry.description or not entry.system_prompt:
            logger.debug("Skipping subagent '%s': missing description or prompt", subdir.name)
            continue

        max_turns_raw = entry.extra.get("max_turns")
        max_turns: int | None = None
        if max_turns_raw:
            try:
                max_turns = int(max_turns_raw)
            except (ValueError, TypeError):
                pass

        # MCP: load per-subagent .mcp.json → inline dicts
        mcp_entries: list[str | dict] | None = _load_subagent_mcp(subdir)

        # MCP: also honour mcp_servers: key in AGENTS.md frontmatter → name refs
        mcp_refs_raw = entry.extra.get("mcp_servers", "")
        if mcp_refs_raw:
            refs = _parse_mcp_server_names(mcp_refs_raw)
            if refs:
                if mcp_entries is None:
                    mcp_entries = refs
                else:
                    mcp_entries = mcp_entries + refs

        result[entry.name] = AgentDefinition(
            description=entry.description,
            prompt=entry.system_prompt,
            model=_translate_model(entry.model),
            maxTurns=max_turns,
            effort=entry.extra.get("effort") or None,
            permissionMode=entry.extra.get("permission_mode") or None,
            mcpServers=mcp_entries,
        )
        logger.debug(
            "Loaded claude subagent '%s' (mcp_servers=%s)",
            entry.name,
            len(mcp_entries) if mcp_entries else 0,
        )

    return result
