"""AGENTS.md parser for RAI subagent coordination.

Parses a multi-agent definition file where each agent is separated by
``---`` markers (YAML frontmatter style).  Example::

    ---
    name: web-scanner
    description: Specialized web application vulnerability scanner
    model: inherit
    api_key: ""
    base_url: ""
    ---

    You are a specialized web application vulnerability scanner...

    ---
    name: code-auditor
    description: Static security code reviewer
    model: openai/gpt-4o
    ---

    You are a security code auditor...

Each ``---`` separator line must be *exactly* ``---`` (no trailing whitespace
is required, but the line must contain nothing else).  The file is split into
sections by these lines; the sections alternate between YAML config blocks
and system-prompt text blocks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AgentMDEntry:
    """A single agent definition parsed from an AGENTS.md file."""

    name: str
    description: str
    system_prompt: str
    model: str = "inherit"          # "inherit" → use parent's model
    api_key: str = ""
    base_url: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _split_sections(text: str) -> list[str]:
    """Split *text* by lines that are exactly ``---``.

    Returns a list of section strings (may be empty strings for adjacent
    separators or a leading separator).
    """
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.rstrip() == "---":
            sections.append("\n".join(current))
            current = []
        else:
            current.append(line)
    sections.append("\n".join(current))
    return sections


def _is_blank_or_comment(section: str) -> bool:
    """Return True if *section* contains only blank lines or ``#`` comments."""
    for line in section.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return False
    return True


def _parse_yaml_config(block: str) -> dict:
    """Parse a minimal YAML-like config block (key: value pairs only).

    We intentionally avoid importing PyYAML (which may not be present) and
    instead implement a simple key: value parser that covers the supported
    fields.  Multi-line values and complex YAML constructs are *not* supported
    in the config block — use the system_prompt section for free-form text.
    """
    result: dict = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            key, _, raw_value = stripped.partition(":")
            key = key.strip()
            value = raw_value.strip()
            # Strip surrounding quotes if present
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            result[key] = value
    return result


def parse_agents_md(path: Path) -> list[AgentMDEntry]:
    """Parse *path* as an AGENTS.md file and return a list of AgentMDEntry.

    Sections are split by lines containing exactly ``---``.  Leading blank /
    comment-only sections are skipped.  Remaining sections are consumed in
    pairs: ``(yaml_config, system_prompt_text)``.  Incomplete trailing pairs
    (config without a prompt, or an orphaned prompt) are handled gracefully.

    Args:
        path: Filesystem path to the AGENTS.md file.

    Returns:
        List of parsed AgentMDEntry objects.  Returns an empty list if the
        file does not exist or contains no valid agent definitions.
    """
    if not path.exists():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read AGENTS.md at %s: %s", path, exc)
        return []

    sections = _split_sections(text)

    # Drop leading blank/comment preamble sections
    while sections and _is_blank_or_comment(sections[0]):
        sections.pop(0)

    entries: list[AgentMDEntry] = []

    # Consume in pairs: (config, prompt)
    i = 0
    while i < len(sections):
        config_block = sections[i]
        i += 1

        # If this section is blank/comment we skip it (handles trailing ``---``)
        if _is_blank_or_comment(config_block):
            continue

        # Try to parse as YAML config
        cfg = _parse_yaml_config(config_block)
        if "name" not in cfg:
            # Not a config block — treat as orphaned prompt, skip with warning
            logger.debug(
                "AGENTS.md section at index %d has no 'name' field; skipping.", i - 1
            )
            continue

        # Next section is the system prompt
        prompt_text = ""
        if i < len(sections):
            prompt_text = sections[i].strip()
            i += 1

        name = cfg.get("name", "").strip()
        if not name:
            logger.warning("AGENTS.md entry missing 'name'; skipping.")
            continue

        description = cfg.get("description", "").strip()
        model = cfg.get("model", "inherit").strip() or "inherit"
        api_key = cfg.get("api_key", "").strip()
        base_url = cfg.get("base_url", "").strip()

        # Collect any unknown fields into extra
        known = {"name", "description", "model", "api_key", "base_url"}
        extra = {k: v for k, v in cfg.items() if k not in known}

        entry = AgentMDEntry(
            name=name,
            description=description,
            system_prompt=prompt_text,
            model=model,
            api_key=api_key,
            base_url=base_url,
            extra=extra,
        )
        entries.append(entry)
        logger.debug("Loaded subagent '%s' from AGENTS.md at %s", name, path)

    return entries


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------

_TEMPLATE = """\
# AGENTS.md — Subagent definitions for {agent_name}
#
# Each agent is separated by a line containing exactly ---
# The first section after --- is YAML frontmatter (config).
# The second section is the system prompt for that agent.
#
# Supported config fields:
#   name        (required) unique identifier for the subagent
#   description (required) one-line description shown in /agents listing
#   model       model string, or "inherit" / empty to use the parent agent's model
#   api_key     API key, or "inherit" / empty to inherit from parent
#   base_url    base URL, or "inherit" / empty to inherit from parent (e.g. local model)
#
# Per-subagent tools:
#   Place ~/.rai/agents/<name>/mcp.json next to the subagent's own agent dir
#   to give it specialized MCP tools loaded automatically at startup.
#   Use @<name> syntax in the TUI to delegate tasks directly to a subagent.

---
name: example-subagent
description: An example subagent — replace with your own definition
model: inherit
api_key: inherit
base_url: inherit
---

You are a specialized security assistant. Your role is to help with
specific security tasks as directed by the parent agent.

Replace this system prompt with your subagent's actual instructions.
"""


def scaffold_agents_md(agent_name: str) -> Path:
    """Create a template AGENTS.md for *agent_name* if one does not exist.

    The file is written to ``~/.rai/agents/<agent_name>/AGENTS.md``.

    Args:
        agent_name: Agent identifier.

    Returns:
        Path to the AGENTS.md file (whether newly created or pre-existing).
    """
    agents_md_path = Path.home() / ".rai" / "agents" / agent_name / "AGENTS.md"
    agents_md_path.parent.mkdir(parents=True, exist_ok=True)

    if not agents_md_path.exists():
        agents_md_path.write_text(
            _TEMPLATE.format(agent_name=agent_name), encoding="utf-8"
        )
        logger.info("Created template AGENTS.md at %s", agents_md_path)
    else:
        logger.debug("AGENTS.md already exists at %s; not overwriting.", agents_md_path)

    return agents_md_path
