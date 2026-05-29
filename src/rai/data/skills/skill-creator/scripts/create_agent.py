#!/usr/bin/env python3
"""Add a new subagent entry to ~/.rai/agents/<parent>/AGENTS.md.

Usage:
    python create_agent.py jwt-auditor \
        --description "JWT token analysis and attack specialist" \
        --for-agent rai

    python create_agent.py cloud-enum \
        --description "AWS/GCP/Azure asset enumeration" \
        --model openai/gpt-4o \
        --for-agent rai

The script appends to the parent agent's AGENTS.md without touching existing
entries.  It also creates the child agent's own directory and a standalone
AGENTS.md so it can later be addressed with @jwt-auditor in the TUI.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from rai_paths import get_agent_md_path, get_agent_dir

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_AGENTS_MD_ENTRY = """
---
name: {name}
description: {description}
model: {model}
api_key: inherit
base_url: inherit
---

You are **{title}**, a specialist agent within the RAI cybersecurity platform.

## Role

{description}

## Capabilities

[TODO: List the agent's specific capabilities and tools it should use.]
- Use `bash` for: [command-line tools relevant to this specialization]
- Use `http_request` for: [HTTP-based probing or API calls]
- Use `web_search` for: [research, CVE lookups, OSINT]
- Use `findings_add` for: recording confirmed vulnerabilities

## Methodology

1. Confirm the target is in scope before any active technique.
2. [Step-by-step methodology for this specialization.]
3. Record all findings with `findings_add`.

## Output Format

[Describe how this agent should present its results.]
"""

_STANDALONE_AGENTS_MD = """\
# AGENTS.md — {name}
# Edit this file to customise the agent's config and system prompt.
# Add more --- blocks below to define sub-agents for {name}.

---
name: {name}
description: {description}
model: {model}
api_key: inherit
base_url: inherit
---

You are **{title}**, a specialist agent within the RAI cybersecurity platform.

## Role

{description}

## Capabilities

[TODO: List the agent's specific capabilities and tools it should use.]

## Methodology

1. Confirm the target is in scope.
2. [Add step-by-step methodology.]
3. Record all findings with `findings_add`.
"""

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        print(f"[error] Invalid agent name '{name}'. Use lowercase-with-hyphens.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_agent(
    name: str,
    description: str,
    model: str = "inherit",
    for_agent: str = "rai",
) -> None:
    _validate_name(name)
    title = name.replace("-", " ").title()

    # 1. Append to parent AGENTS.md
    parent_md = get_agent_md_path(for_agent)

    if not parent_md.exists():
        parent_md.parent.mkdir(parents=True, exist_ok=True)
        header = f"# AGENTS.md — {for_agent}\n\n"
        parent_md.write_text(header, encoding="utf-8")
        print(f"[created] {parent_md}")

    existing = parent_md.read_text(encoding="utf-8")
    if f"\nname: {name}\n" in existing or existing.startswith(f"name: {name}\n"):
        print(f"[skip] Agent '{name}' already present in {parent_md}")
    else:
        entry = _AGENTS_MD_ENTRY.format(
            name=name,
            description=description,
            model=model,
            title=title,
        )
        with parent_md.open("a", encoding="utf-8") as f:
            f.write(entry)
        print(f"[ok] Appended '{name}' to {parent_md}")

    # 2. Create standalone AGENTS.md for the child agent's own directory
    child_md = get_agent_md_path(name)
    if child_md.exists():
        print(f"[skip] {child_md} already exists")
    else:
        child_md.parent.mkdir(parents=True, exist_ok=True)
        child_md.write_text(
            _STANDALONE_AGENTS_MD.format(
                name=name,
                description=description,
                model=model,
                title=title,
            ),
            encoding="utf-8",
        )
        print(f"[ok] Created {child_md}")

    # 3. Create memory dir for the child agent
    mem_dir = get_agent_dir(name) / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    print(f"[ok] Memory dir: {mem_dir}")

    print(f"\nAgent '{name}' is ready. Address it in the RAI TUI with: @{name}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Add a new subagent to an AGENTS.md")
    parser.add_argument("name", help="Agent name (lowercase-with-hyphens, e.g. jwt-auditor)")
    parser.add_argument("--description", "-d", required=True, help="One-sentence description")
    parser.add_argument("--model", "-m", default="inherit",
                        help="Model (default: inherit). Examples: openai/gpt-4o, anthropic:claude-sonnet-4-6")
    parser.add_argument("--for-agent", "-a", default="rai",
                        help="Parent agent whose AGENTS.md to append to (default: rai)")
    args = parser.parse_args()

    create_agent(
        name=args.name,
        description=args.description,
        model=args.model,
        for_agent=args.for_agent,
    )


if __name__ == "__main__":
    main()
