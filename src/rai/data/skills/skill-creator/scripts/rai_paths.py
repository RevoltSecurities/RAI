#!/usr/bin/env python3
"""RAI path resolver — cross-platform (macOS, Linux, Windows).

Usage as a script:
    python rai_paths.py              # print JSON with all key paths
    python rai_paths.py --skills     # print user skills dir
    python rai_paths.py --agent rai  # print agent dir for 'rai'

Importable by other scripts in this directory:
    from rai_paths import get_rai_home, get_skills_dir, get_agent_md_path
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def get_rai_home() -> Path:
    """Return the RAI home directory for the current OS.

    - macOS / Linux : ~/.rai/
    - Windows       : %USERPROFILE%\\.rai\\  (falls back to Path.home())
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("USERPROFILE") or os.environ.get("HOMEPATH") or "")
        if not base or not base.exists():
            base = Path.home()
    else:
        base = Path.home()
    return base / ".rai"


def get_skills_dir(agent: str | None = None) -> Path:
    """Return the skills directory.

    - agent=None  → ~/.rai/skills/           (user-level, all agents)
    - agent=<name>→ ~/.rai/agents/<name>/skills/  (agent-specific)
    """
    if agent:
        return get_rai_home() / "agents" / agent / "skills"
    return get_rai_home() / "skills"


def get_agents_root() -> Path:
    return get_rai_home() / "agents"


def get_agent_dir(agent: str) -> Path:
    return get_agents_root() / agent


def get_agent_md_path(agent: str) -> Path:
    """~/.rai/agents/<agent>/AGENTS.md"""
    return get_agent_dir(agent) / "AGENTS.md"


def get_memory_dir(agent: str) -> Path:
    return get_agent_dir(agent) / "memory"


def get_mcp_config(scope: str = "agent", agent: str = "rai") -> Path:
    """Return the MCP config path.

    - scope='global' → ~/.rai/.mcp.json
    - scope='agent'  → ~/.rai/agents/<agent>/mcp.json
    """
    if scope == "global":
        return get_rai_home() / ".mcp.json"
    return get_agent_dir(agent) / "mcp.json"


def paths_info(agent: str = "rai") -> dict[str, str]:
    return {
        "rai_home": str(get_rai_home()),
        "skills_user": str(get_skills_dir()),
        "skills_agent": str(get_skills_dir(agent=agent)),
        "agents_root": str(get_agents_root()),
        "agent_dir": str(get_agent_dir(agent)),
        "agent_md": str(get_agent_md_path(agent)),
        "memory_dir": str(get_memory_dir(agent)),
        "mcp_global": str(get_mcp_config("global")),
        "mcp_agent": str(get_mcp_config("agent", agent)),
        "platform": sys.platform,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAI path resolver")
    parser.add_argument("--agent", default="rai", help="Agent name (default: rai)")
    parser.add_argument("--skills", action="store_true", help="Print user skills dir")
    parser.add_argument("--agent-skills", action="store_true", help="Print agent skills dir")
    parser.add_argument("--agents-root", action="store_true", help="Print agents root dir")
    args = parser.parse_args()

    if args.skills:
        print(get_skills_dir())
    elif args.agent_skills:
        print(get_skills_dir(agent=args.agent))
    elif args.agents_root:
        print(get_agents_root())
    else:
        print(json.dumps(paths_info(args.agent), indent=2))
