#!/usr/bin/env python3
"""Create a new RAI skill at the correct path.

Usage:
    python create_skill.py <name> --description "What this skill does"
    python create_skill.py web-recon --description "Passive + active web recon" --with-scripts --with-references
    python create_skill.py sqli-scan --description "SQL injection testing" --agent pentest
    python create_skill.py lfi-test  --description "LFI path traversal checks" --project

The script resolves the correct RAI path for the current OS automatically.
It never overwrites an existing skill — use --force to overwrite.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# Allow running from any directory — rai_paths.py is a sibling
sys.path.insert(0, str(Path(__file__).parent))
from rai_paths import get_skills_dir

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_SKILL_MD = """\
---
name: {name}
description: "{description}"
license: MIT
compatibility: RAI
metadata:
  author: {author}
  version: "1.0"
# allowed-tools:
#   - bash
#   - http_request
#   - web_search
#   - findings_add
---

# {title}

## Overview

[TODO: 1-2 sentences describing what this skill enables and when to use it.]

## When to Use

- Trigger phrase: `/{name}`
- Situations: [describe the scenarios that should activate this skill]

## Instructions

### Step 1: Scope Confirmation

Always confirm the target is in scope before running any active technique.
> "Is `<target>` authorised for this engagement?"

### Step 2: [Core Action]

[Describe the primary action — specific commands, HTTP requests, or research steps.]

```bash
# Example command — replace with actual tool and flags
nmap -sV -sC -p- --open <target>
```

### Step 3: Analyse Results

[What to look for, how to filter noise, what constitutes a finding.]

### Step 4: Record Findings

For each confirmed finding, use `findings_add`:
- **title**: short descriptive title
- **severity**: critical / high / medium / low / info
- **description**: what was found and where
- **evidence**: relevant output snippet or URL

## Output Format

[Describe expected output — e.g., markdown table, JSON blob, PoC script.]

## Security Notes

- This skill is for **authorised** security testing only.
- [Any additional constraints, rate-limiting, or legal notes.]

## Examples

### Example: [Scenario Name]

**User:** `/{name} https://target.example.com`

**What the agent does:**
1. [Step 1]
2. [Step 2]

**Expected output:** [describe]
"""

_SCRIPTS_README = """\
# Scripts for `{name}`

Place supporting Python or shell scripts here.
The agent can invoke them via `bash` or `http_request`.

## Naming convention

- `run_<action>.py`  — main automation scripts
- `parse_<output>.py` — output parsers / result formatters
- `payload_<type>.txt` — payload lists

## Path resolution

`rai_paths.py` (in the parent skill-creator's scripts/) resolves all RAI
directories cross-platform. Copy it here if this skill needs path resolution.
"""

_REFERENCES_README = """\
# References for `{name}`

Place supporting documentation here:
- CVE / advisory links
- Technique writeups
- Payload cheat-sheets
- Tool man pages / flag references
- Example request/response captures

## File conventions

- `*.md`   — markdown references the agent can read_file
- `*.txt`  — plain-text payloads / wordlists the agent can pass to tools
- `*.json` — structured reference data
"""

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        print(f"[error] Invalid skill name '{name}'.")
        print("        Names must be lowercase alphanumeric with single hyphens (e.g. sqli-scan).")
        sys.exit(1)
    if len(name) > 64:
        print(f"[error] Skill name too long (max 64 chars).")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_skill(
    name: str,
    description: str,
    author: str = "rai",
    agent: str | None = None,
    project_cwd: Path | None = None,
    with_scripts: bool = False,
    with_references: bool = False,
    force: bool = False,
) -> Path:
    _validate_name(name)

    if project_cwd is not None:
        skills_root = project_cwd / ".rai" / "skills"
    else:
        skills_root = get_skills_dir(agent=agent)

    skill_dir = skills_root / name
    skill_md = skill_dir / "SKILL.md"

    if skill_md.exists() and not force:
        print(f"[skip] Skill '{name}' already exists at {skill_dir}")
        print(f"       Use --force to overwrite.")
        return skill_dir

    if force and skill_dir.exists():
        shutil.rmtree(skill_dir)

    skill_dir.mkdir(parents=True, exist_ok=True)

    title = name.replace("-", " ").title()
    skill_md.write_text(
        _SKILL_MD.format(name=name, description=description, author=author, title=title),
        encoding="utf-8",
    )
    print(f"[ok] Created {skill_md}")

    if with_scripts:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        readme = scripts_dir / "README.md"
        readme.write_text(_SCRIPTS_README.format(name=name), encoding="utf-8")
        print(f"[ok] Created {scripts_dir}/")

    if with_references:
        ref_dir = skill_dir / "references"
        ref_dir.mkdir(exist_ok=True)
        readme = ref_dir / "README.md"
        readme.write_text(_REFERENCES_README.format(name=name), encoding="utf-8")
        print(f"[ok] Created {ref_dir}/")

    print(f"\nSkill '{name}' is ready. Invoke it with: /{name}")
    print(f"Edit: {skill_md}")
    return skill_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new RAI skill")
    parser.add_argument("name", help="Skill name (lowercase-with-hyphens, e.g. sqli-scan)")
    parser.add_argument("--description", "-d", required=True, help="One-sentence description")
    parser.add_argument("--author", default="rai", help="Author name (default: rai)")
    parser.add_argument("--agent", "-a", default=None, help="Agent-specific skills dir (default: user-level)")
    parser.add_argument("--project", "-p", action="store_true", help="Write to <cwd>/.rai/skills/ instead")
    parser.add_argument("--with-scripts", "-s", action="store_true", help="Create scripts/ subdirectory")
    parser.add_argument("--with-references", "-r", action="store_true", help="Create references/ subdirectory")
    parser.add_argument("--force", "-f", action="store_true", help="Overwrite if skill already exists")
    args = parser.parse_args()

    create_skill(
        name=args.name,
        description=args.description,
        author=args.author,
        agent=args.agent,
        project_cwd=Path.cwd() if args.project else None,
        with_scripts=args.with_scripts,
        with_references=args.with_references,
        force=args.force,
    )


if __name__ == "__main__":
    main()
