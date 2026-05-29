"""Default agents shipped with every RAI installation.

On first run each agent directory under ~/.rai/agents/ is seeded:

  rai           — config-only AGENTS.md (model/api_key/base_url).
                  System prompt is always loaded from the bundled system_prompt.md.

  researcher    — OSINT, recon, HTTP probing, technology fingerprinting
  coder         — Code generation, automation, scripting
  agent-creator — Builds new specialized agents interactively

All subagents inherit the parent's model/api_key/base_url by default.
Users can override by editing ~/.rai/agents/<name>/AGENTS.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "data" / "prompts"


def _load_prompt(agent_name: str) -> str:
    """Read the bundled prompt.md for *agent_name* from data/prompts/<name>/prompt.md."""
    return (_PROMPTS_DIR / agent_name / "prompt.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Default subagent registry
# ---------------------------------------------------------------------------

_DEFAULT_SUBAGENTS: list[dict[str, str]] = [
    {
        "name": "recon",
        "description": "Full attack surface mapping — web, API, cloud, K8s, Docker, Android, network. Always first in every engagement.",
    },
    {
        "name": "researcher",
        "description": "Security intelligence — CVE research, exploit PoC hunting, vulnerability methodology, H1 prior art, threat intel.",
    },
    {
        "name": "coder",
        "description": "Exploit scripts, PoC builders, Nuclei templates, IDOR enumerators, chain exploits, automation tools.",
    },
    {
        "name": "sast-analyzer",
        "description": "Static analysis across source code — semgrep, bandit, gosec, secret scanning, dependency audit, findings triage.",
    },
    {
        "name": "agent-creator",
        "description": "Designs new specialized subagents. Writes prompt to /tmp/agents/, returns file path and SUBAGENT_REGISTRATION block to RAI — RAI calls create_subagent.",
    },
]


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


def ensure_default_agents(agent_name: str) -> bool:
    """Seed ~/.rai/agents/ with the main agent config and default subagents.

    Main agent (~/.rai/agents/<agent_name>/AGENTS.md):
        Written with a config-only YAML block (name, description, model,
        api_key, base_url).  No system prompt — rai always loads its system
        prompt from the bundled system_prompt.md so users cannot accidentally
        break the core agent behaviour by editing AGENTS.md.

    Default subagents (researcher, coder, agent-creator):
        Each gets its own ~/.rai/agents/<name>/AGENTS.md with a config block
        + full system prompt.  Users can freely edit these.

    All writes are idempotent — existing files are never overwritten.

    Returns:
        True if any file was newly created, False otherwise.
    """
    from rai.config.settings import settings

    created = False

    # 1. Main agent — config block only, no system prompt
    main_md_path = settings.agent_md_path(agent_name)
    if not main_md_path.exists():
        settings.ensure_agent_dir(agent_name)
        _write_config_only_md(
            agent_name=agent_name,
            description=f"RAI agentic AI assistant ({agent_name})",
        )
        created = True

    # 2. Default subagents — config block + system prompt in individual dirs
    for sa in _DEFAULT_SUBAGENTS:
        try:
            settings.ensure_memory_files(sa["name"])
            _write_agent_md(
                agent_name=sa["name"],
                description=sa["description"],
                system_prompt=_load_prompt(sa["name"]),
            )
            if not created:
                created = True
        except OSError as exc:
            logger.debug("Could not seed subagent '%s': %s", sa["name"], exc)

    return created


def _write_config_only_md(agent_name: str, description: str) -> None:
    """Write a config-only AGENTS.md for the main agent (no system prompt).

    The system prompt is intentionally omitted — it is always loaded from
    the bundled system_prompt.md to prevent accidental corruption of the
    core agent behaviour.
    """
    from rai.config.settings import settings

    path = settings.agent_md_path(agent_name)
    if path.exists():
        return

    lines = [
        f"# AGENTS.md — {agent_name}",
        "# Edit model / api_key / base_url to override the defaults for this agent.",
        "# The system prompt is managed by RAI and loaded from its bundled template.",
        "",
        "---",
        f"name: {agent_name}",
        f"description: {description}",
        "model: inherit",
        "api_key: inherit",
        "base_url: inherit",
        "---",
        "",
    ]

    try:
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Seeded config AGENTS.md for '%s' at %s", agent_name, path)
    except OSError as exc:
        logger.warning("Could not write AGENTS.md for '%s': %s", agent_name, exc)


def _write_agent_md(
    agent_name: str,
    description: str,
    system_prompt: str,
    model: str = "inherit",
    api_key: str = "inherit",
    base_url: str = "inherit",
) -> None:
    """Write a full AGENTS.md (config + system prompt) for a subagent.

    Skipped if the file already exists (preserves user edits).
    """
    from rai.config.settings import settings

    path = settings.agent_md_path(agent_name)
    if path.exists():
        logger.debug("AGENTS.md already exists for '%s'; skipping", agent_name)
        return

    settings.ensure_agent_dir(agent_name)

    lines = [
        f"# AGENTS.md — {agent_name}",
        "# Edit this file to customise the agent's config and system prompt.",
        "",
        "---",
        f"name: {agent_name}",
        f"description: {description}",
        f"model: {model}",
        f"api_key: {api_key}",
        f"base_url: {base_url}",
        "---",
        "",
        system_prompt.strip(),
        "",
    ]

    try:
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Seeded AGENTS.md for '%s' at %s", agent_name, path)
    except OSError as exc:
        logger.warning("Could not write AGENTS.md for '%s': %s", agent_name, exc)


# Backward-compat alias — tui/app.py and tools.py import this name
_write_individual_agent_md = _write_agent_md
