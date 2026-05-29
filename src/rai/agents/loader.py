"""AGENTS.md loader — converts parsed entries to deepagents SubAgent dicts."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INHERIT = "inherit"


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def to_deepagents_subagent(
    entry,
    parent_model: str,
    *,
    parent_api_key: str = "",
    parent_base_url: str = "",
) -> dict:
    """Convert an AgentMDEntry to a SubAgent-compatible dict.

    ``model``, ``api_key``, and ``base_url`` all support inheritance:
    an empty value or the literal string ``"inherit"`` means "use the
    parent's value".

    Args:
        entry: Parsed agent entry.
        parent_model: Parent agent's model string.
        parent_api_key: Parent agent's API key — used when entry.api_key is
            empty or ``"inherit"``.
        parent_base_url: Parent agent's base URL — used when entry.base_url is
            empty or ``"inherit"``.

    Returns:
        SubAgent-compatible dict.
    """
    def _resolve(value: str, parent: str) -> str:
        return parent if (not value or value == _INHERIT) else value

    effective_model = _resolve(entry.model, parent_model)
    effective_api_key = _resolve(entry.api_key, parent_api_key)
    effective_base_url = _resolve(entry.base_url, parent_base_url)

    subagent: dict = {
        "name": entry.name,
        "description": entry.description,
        "system_prompt": entry.system_prompt,
    }

    # Only include model when it differs from the parent (deepagents SDK
    # uses parent model by default when the key is absent).
    if effective_model and effective_model != parent_model:
        subagent["model"] = effective_model

    if effective_api_key:
        subagent["api_key"] = effective_api_key
    if effective_base_url:
        subagent["base_url"] = effective_base_url

    return subagent


def subagent_mcp_config_path(subagent_name: str) -> Path:
    """Return the per-subagent MCP config path: ``~/.rai/agents/<name>/mcp.json``."""
    from rai.config.settings import settings
    return settings.agent_dir(subagent_name) / "mcp.json"


def load_subagents_for(
    agent_name: str,
    *,
    parent_api_key: str = "",
    parent_base_url: str = "",
) -> list[dict]:
    """Load subagents by scanning ``~/.rai/agents/`` for individual AGENTS.md files.

    Every subdirectory under ``~/.rai/agents/`` except *agent_name* itself is a
    potential subagent.  Its ``AGENTS.md`` is authoritative — users edit
    ``~/.rai/agents/<name>/AGENTS.md`` directly to change that agent's behaviour.

    Matching rule: the first entry whose ``name`` field equals the directory name
    is used; if none matches, the first entry is used (handles hand-edited files).

    Args:
        agent_name: The calling agent's own directory name — skipped during scan.
        parent_api_key: Inherited API key for entries with ``api_key: inherit``.
        parent_base_url: Inherited base URL for entries with ``base_url: inherit``.

    Returns:
        List of SubAgent-compatible dicts, sorted by subagent name.
    """
    from rai.config.settings import settings
    from rai.agents.parser import parse_agents_md

    agents_root = settings.agents_dir
    result: list[dict] = []

    if not agents_root.exists():
        return result

    for subdir in sorted(agents_root.iterdir()):
        if not subdir.is_dir() or subdir.name == agent_name:
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
        result.append(to_deepagents_subagent(
            entry,
            parent_model="",
            parent_api_key=parent_api_key,
            parent_base_url=parent_base_url,
        ))

    return result
