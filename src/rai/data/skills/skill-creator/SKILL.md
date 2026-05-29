---
name: skill-creator
description: "Build new RAI skills, agents, scripts, and references end-to-end — model-agnostic, cybersecurity-focused, correct RAI paths on any OS."
license: MIT
compatibility: RAI
metadata:
  author: rai
  version: "1.0"
---

# Skill Creator

## Overview

This skill turns any request like "create a SQL injection skill" or "make me a
JWT auditor agent" into a fully structured RAI artifact saved to the correct
path on the current OS.

RAI is **model-agnostic** — it works with Anthropic, OpenAI, Google, Ollama,
Groq, and any LiteLLM-compatible endpoint.  Nothing in the artifacts you
create should reference a specific provider unless the user asks for it.

---

## What This Skill Can Create

| Artifact | What it is | Where it lives |
|----------|-----------|----------------|
| **Skill** | SKILL.md + optional scripts/ + references/ | `~/.rai/skills/<name>/` or `~/.rai/agents/<agent>/skills/<name>/` |
| **Agent** | AGENTS.md entry + standalone agent dir | `~/.rai/agents/<name>/AGENTS.md` |
| **Script** | Standalone Python/bash helper | Inside a skill's `scripts/` dir |
| **Reference** | Markdown doc, cheat-sheet, payload list | Inside a skill's `references/` dir |

---

## Path Resolution (All OS)

Always resolve RAI paths using the bundled helper before writing any file.
The skill directory is available at runtime — find it with:

```bash
# The skill-creator skill dir (set once, reuse throughout the session)
SKILL_DIR=$(python3 -c "
from pathlib import Path
import subprocess, sys

# Find where this skill is installed
for base in [
    Path.home() / '.rai' / 'skills' / 'skill-creator',
    Path.home() / '.rai' / 'agents' / 'rai' / 'skills' / 'skill-creator',
]:
    if base.exists():
        print(base)
        break
")

# Now you can call the scripts
python3 "$SKILL_DIR/scripts/rai_paths.py"           # prints all key paths as JSON
python3 "$SKILL_DIR/scripts/rai_paths.py" --skills  # prints user skills dir
```

Or resolve paths directly in Python without a subprocess:

```python
import sys
from pathlib import Path

# Locate rai_paths.py
for base in [
    Path.home() / ".rai" / "skills" / "skill-creator" / "scripts",
    Path.home() / ".rai" / "agents" / "rai" / "skills" / "skill-creator" / "scripts",
]:
    if (base / "rai_paths.py").exists():
        sys.path.insert(0, str(base))
        break

from rai_paths import get_skills_dir, get_agent_md_path, paths_info
print(paths_info())  # shows all key RAI paths for this OS
```

---

## Instructions

### Phase 1 — Understand the Request

Ask (all at once, not one by one) if not clear from context:

1. **What to create**: skill / agent / script / reference — or a combination?
2. **Name**: `lowercase-with-hyphens` (e.g. `sqli-scan`, `jwt-auditor`, `s3-enum`)
3. **Goal**: one sentence — what should the artifact do?
4. **Scope**: offensive / defensive / OSINT / CTF-lab / audit
5. **Tools needed** (for skills/agents): which RAI tools will it rely on?
   - `bash` — nmap, nuclei, ffuf, sqlmap, gobuster, curl, subfinder, httpx
   - `http_request` — raw HTTP probing, custom header crafting
   - `web_search` / `web_fetch` — CVE research, OSINT, advisory lookup
   - `findings_add` — persist confirmed vulnerabilities (mandatory for any vuln-finding skill)
   - `write_file` / `read_file` — save reports, wordlists, payloads
6. **Save location**: user-level (`~/.rai/skills/`) or agent-specific (`--agent <name>`)?
7. **Supporting files needed**: scripts? references? example payloads?

---

### Phase 2 — Create a Skill

```bash
# Locate the skill-creator scripts dir
SCRIPTS=$(python3 -c "
from pathlib import Path
for p in [Path.home()/'.rai'/'skills'/'skill-creator'/'scripts',
          Path.home()/'.rai'/'agents'/'rai'/'skills'/'skill-creator'/'scripts']:
    if p.exists(): print(p); break
")

# Create the skill (add --with-scripts and/or --with-references as needed)
python3 "$SCRIPTS/create_skill.py" <name> \
    --description "<one-sentence description>" \
    [--agent <agent-name>]       \  # omit for user-level
    [--with-scripts]             \  # adds scripts/ subdirectory
    [--with-references]          \  # adds references/ subdirectory
    [--force]                       # overwrite if exists
```

After the scaffold is created, open the SKILL.md and fill it in:

```bash
SKILL_PATH=$(python3 "$SCRIPTS/rai_paths.py" --skills)/<name>/SKILL.md
# Edit the file at $SKILL_PATH — replace all TODO sections
```

Use `references/skill_template.md` as a guide for section structure.

**Every cybersecurity skill must include:**
1. A scope-confirmation step (ask before any active technique)
2. Specific tool invocations (not generic "run a scanner")
3. A `findings_add` step for any confirmed vulnerability
4. An examples section with at least one realistic invocation

---

### Phase 3 — Create an Agent

```bash
python3 "$SCRIPTS/create_agent.py" <name> \
    --description "<one-sentence description>" \
    --for-agent rai           \  # parent agent (default: rai)
    [--model inherit]            # or: openai/gpt-4o, anthropic:claude-opus-4-7, etc.
```

This:
- Appends a `--- ... ---` block to `~/.rai/agents/rai/AGENTS.md`
- Creates `~/.rai/agents/<name>/AGENTS.md` (standalone, editable)
- Creates `~/.rai/agents/<name>/memory/` dir

After creation, open the standalone AGENTS.md and add the methodology section.
Use `references/agent_template.md` as a guide.

**Invoke the new agent in the RAI TUI with:** `@<name> <task>`

---

### Phase 4 — Create a Script

When a skill needs an automation helper:

```python
from pathlib import Path
import sys

# Resolve skills dir
for base in [
    Path.home() / ".rai" / "skills" / "skill-creator" / "scripts",
    Path.home() / ".rai" / "agents" / "rai" / "skills" / "skill-creator" / "scripts",
]:
    if (base / "rai_paths.py").exists():
        sys.path.insert(0, str(base))
        break

from rai_paths import get_skills_dir

skill_scripts = get_skills_dir() / "<skill-name>" / "scripts"
skill_scripts.mkdir(parents=True, exist_ok=True)
(skill_scripts / "<script-name>.py").write_text("""
#!/usr/bin/env python3
# <description>
...""", encoding="utf-8")
```

Script conventions:
- `run_<action>.py` — main automation (argparse CLI, runnable standalone)
- `parse_<output>.py` — output parsers
- `payload_<type>.txt` — payload/wordlist files

---

### Phase 5 — Create a Reference

```python
ref_dir = get_skills_dir() / "<skill-name>" / "references"
ref_dir.mkdir(parents=True, exist_ok=True)
(ref_dir / "<topic>.md").write_text("<content>", encoding="utf-8")
```

Reference file conventions:
- `*.md` — markdown docs the agent can `read_file` at runtime
- `*.txt` — plain-text payload lists, wordlists
- `examples/` — worked examples of the skill in action (see `references/examples/web-recon.md`)

---

### Phase 6 — Test Loop

After creating any artifact:

1. Simulate the invocation: show the user 2-3 example prompts and walk through what the agent would do.
2. Ask: "Does this match what you expected? Any changes?"
3. If yes → done. If no → edit the file and repeat.
4. For skills: verify the slash command resolves by noting the path `~/.rai/skills/<name>/SKILL.md` exists.

---

## Best Practices

### For Skills
- **Scope check is non-negotiable** — every offensive/active skill must ask for authorization first.
- **Specific over vague** — name the exact binary and flags, not "use a web scanner".
- **findings_add is mandatory** for any skill that discovers vulnerabilities.
- **Model-agnostic wording** — do not mention Claude, GPT, Gemini, or any model name in skill instructions.

### For Agents
- **Narrow specialization** — a JWT auditor that does JWT really well beats a generic hacker agent.
- **model: inherit by default** — only override when the user explicitly wants a different model.
- **Tool section is explicit** — list the exact RAI tool names the agent should use.

### For Scripts
- **Argparse CLI** — every script should be runnable standalone with `--help`.
- **Import rai_paths** — always resolve RAI paths through `rai_paths.py`, never hardcode `~/.rai`.
- **Exit codes** — `sys.exit(0)` on success, `sys.exit(1)` on error.

### General
- Never hardcode model names or provider-specific configurations.
- Always use `rai_paths.py` for path resolution — it handles macOS, Linux, and Windows correctly.
- Check the `references/` directory of this skill for templates and examples before drafting from scratch.
