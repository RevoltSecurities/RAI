"""RAI configuration and path management.

Mirrors deepagents_cli/config.py but uses ~/.rai/ as the namespace.
All agent directories, skills, memories, MCP configs, and audit logs
live under ~/.rai/ so RAI never pollutes ~/.deepagents/.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Memory file templates
# ---------------------------------------------------------------------------

_MEMORY_INDEX = """\
# RAI Memory — {agent_name}

<!-- Global preferences — shared across all targets. Agent reads MEMORY.md first. -->

- [user.md](memory/user.md) — user preferences and communication style
- [feedback.md](memory/feedback.md) — corrections and validated approaches
- [engagement.md](memory/engagement.md) — scope, RoE, active engagement context
- [target.md](memory/target.md) — discovered assets: subdomains, IPs, services, tech
- [findings.md](memory/findings.md) — confirmed vulnerabilities from past sessions
- [methodology.md](memory/methodology.md) — general attack chains and tool combinations (cross-engagement)
"""

_TARGET_MEMORY_INDEX = """\
# RAI Target Memory — {target_name}

<!-- Target-scoped memory. Loaded in addition to global agent memory. -->

- [engagement.md](memory/engagement.md) — scope, RoE, engagement type for this target
- [recon.md](memory/recon.md) — discovered assets: subdomains, IPs, ports, endpoints
- [findings.md](memory/findings.md) — confirmed vulnerabilities for this target
- [notes.md](memory/notes.md) — WAF notes, auth details, misc observations
- [methodology.md](memory/methodology.md) — attack chains and techniques specific to this target
"""

_TARGET_ENGAGEMENT_MEM = """\
---
type: engagement
target: {target_name}
description: Scope, RoE, and engagement type for this specific target
---

<!-- Populate when the user sets scope for this target.
target_url:
scope: []
out_of_scope: []
engagement_type: bug_bounty | pentest | red_team
roe_notes:
started:
-->
"""

_TARGET_RECON_MEM = """\
---
type: recon
target: {target_name}
description: Discovered assets — append-only, never delete
---

<!-- Append as discovered: subdomains, IPs, open ports, services, technologies.
## Subdomains

## IPs / Hosts

## Open Ports / Services

## Technologies

## API Endpoints

## Admin / Login Pages
-->
"""

_TARGET_FINDINGS_MEM = """\
---
type: findings
target: {target_name}
description: Confirmed vulnerabilities for this target
---

<!-- Format: date | severity | title | location | thread_id
-->
"""

_TARGET_NOTES_MEM = """\
---
type: notes
target: {target_name}
description: Misc observations — WAF, auth, rate limits, quirks
---

<!-- Freeform notes that don't fit recon or findings.
-->
"""

_TARGET_METHODOLOGY_MEM = """\
---
type: methodology
target: {target_name}
description: Attack chains, tool flags, and techniques specific to this target
---

<!-- What worked: specific payloads, bypass techniques, useful endpoints. -->
<!-- What didn't: approaches to skip to save time on this target. -->
"""

_USER_MEM = """\
---
type: user
description: User preferences, communication style, working habits
confidence: high
---

<!-- Agent writes notes here about how the user likes to work.
     For preferences that should apply to ALL agents, write to global scope instead:
     memory_write(file="preferences", scope="global", ...) -->
"""

# ---------------------------------------------------------------------------
# Global user profile templates  (~/.rai/user/)
# Loaded into EVERY agent before agent-specific memory so every agent
# already knows who the user is without being told again.
# ---------------------------------------------------------------------------

_GLOBAL_MEMORY_INDEX = """\
# RAI Global User Profile

<!-- Loaded into EVERY agent before agent-specific memory.
     Write here when you want all agents to know something permanently.
     Update with: memory_write(file="profile"|"preferences"|"context", scope="global") -->

- [profile.md](profile.md)     — who you are: role, expertise, background
- [preferences.md](preferences.md) — how you like to work: style, verbosity, tool choices
- [context.md](context.md)     — rolling context: active projects, current focus
"""

_GLOBAL_PROFILE = """\
---
type: global_profile
description: Who the user is — role, expertise, background. Read by every agent at startup.
updated: never
---

<!-- Fill in when you learn about the user. Applies to ALL agents.
role:
expertise:           # e.g. web, network, binary, cloud, mobile
experience_years:
preferred_languages:
certifications:      # OSCP, CEH, PNPT, etc.
context:             # e.g. "bug bounty hunter focused on web apps"
-->
"""

_GLOBAL_PREFERENCES = """\
---
type: global_preferences
description: How the user likes to work — output style, tool preferences, verbosity. Read by every agent.
updated: never
---

<!-- Fill in when the user corrects or praises an approach.
output_style:        # concise | verbose | technical-only
output_format:       # markdown | plain | tables | JSON
verbosity:           # minimal | normal | step-by-step
tool_preferences:    # specific flags or tool choices to always/never use
communication:       # terse | detailed | casual | formal
always_do:
never_do:
-->
"""

_GLOBAL_CONTEXT = """\
---
type: global_context
description: Rolling context — active projects, current focus, ongoing work. Updated frequently.
updated: never
---

<!-- Update this as active work changes. Most frequently written file.
current_project:
active_targets:
current_goal:
recent_context:      # anything recently mentioned that all agents should know
-->
"""

_FEEDBACK_MEM = """\
---
type: feedback
description: What to do / avoid based on past interactions
confidence: high
---

<!-- Format: rule. Why: reason. How to apply: when this kicks in. -->
"""

_ENGAGEMENT_MEM = """\
---
type: engagement
description: Active engagement — target, scope, RoE, engagement type
confidence: high
---

<!-- Populated by agent when user sets target/scope. Persists across sessions. -->
<!-- Example:
target: https://example.com
scope: ["*.example.com", "10.0.0.0/24"]
engagement_type: pentest
roe_notes: No DoS. Business hours only.
started: 2026-04-25
-->
"""

_TARGET_MEM = """\
---
type: target
description: Persistent intelligence discovered across sessions
confidence: medium
---

<!-- Append-only: subdomains, IPs, ports, services, versions, API endpoints. -->
<!-- Agent appends as it discovers assets — never deletes previous entries. -->
"""

_FINDINGS_MEM = """\
---
type: findings
description: Confirmed vulnerabilities from past sessions
confidence: high
---

<!-- Summary of past findings_add entries — survives session resets. -->
<!-- Format: date | severity | title | location | thread_id -->
"""

_METHODOLOGY_MEM = """\
---
type: methodology
description: General attack chains, tool flags, and techniques across all engagements
confidence: medium
---

<!-- General techniques that work broadly — not target-specific. -->
<!-- Target-specific methodology lives in ~/.rai/targets/<target>/memory/methodology.md -->
<!-- What worked: tool flags, payload types, bypass patterns. -->
<!-- What didn't: approaches that consistently waste time. -->
"""

# ---------------------------------------------------------------------------
# Sentinel for shell allow-all mode
# ---------------------------------------------------------------------------


class _ShellAllowAll:
    """Sentinel: shell access is unrestricted (no allow-list filtering)."""

    def __repr__(self) -> str:
        return "SHELL_ALLOW_ALL"


SHELL_ALLOW_ALL = _ShellAllowAll()

SHELL_TOOL_NAMES = frozenset({"execute", "shell", "bash", "run_command"})


_SHELL_KEYWORDS = frozenset({
    "for", "while", "until", "if", "case", "do", "done", "then", "else",
    "elif", "fi", "esac", "in", "function", "select", "time", "coproc",
    "{", "}", "[", "[[", "!", "true", "false", ":", "source", ".",
})
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def extract_command_binary(cmd: str) -> str:
    """Return the effective binary name from *cmd*, skipping comment/blank lines
    and leading env-var assignments (e.g. ``FOO=bar python x.py`` → ``python``)."""
    for line in cmd.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped, posix=True)
        except ValueError:
            tokens = stripped.split()
        for tok in tokens:
            if _ENV_ASSIGN_RE.match(tok):
                continue
            name = Path(tok).name
            if name in _SHELL_KEYWORDS:
                return ""
            return name
    return ""


def is_shell_command_allowed(command: str, allow_list: list[str]) -> bool:
    """Return True if the effective binary of *command* is in *allow_list*."""
    binary = extract_command_binary(command)
    if not binary:
        return True
    return binary in allow_list


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------


@dataclass
class RAISettings:
    """All path and runtime settings for RAI.

    Designed to be instantiated once and shared. Paths are lazily created
    on first access via the ensure_* helpers.
    """

    _home: Path = field(default_factory=Path.home)

    # ---- root directory ----

    @property
    def user_rai_dir(self) -> Path:
        """~/.rai — global RAI config root."""
        return self._home / ".rai"

    # ---- agent directories ----

    @property
    def agents_dir(self) -> Path:
        """~/.rai/agents/ — one subdirectory per agent name."""
        return self.user_rai_dir / "agents"

    def agent_dir(self, agent_name: str) -> Path:
        return self.agents_dir / agent_name

    def ensure_agent_dir(self, agent_name: str) -> Path:
        d = self.agent_dir(agent_name)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- memories ----

    def memories_dir(self, agent_name: str) -> Path:
        """~/.rai/agents/<name>/memories/ — short/long/episodic memory files."""
        return self.agent_dir(agent_name) / "memories"

    def ensure_memories_dir(self, agent_name: str) -> Path:
        d = self.memories_dir(agent_name)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def short_term_memory_path(self, agent_name: str) -> Path:
        return self.memories_dir(agent_name) / "short_term.md"

    def long_term_memory_path(self, agent_name: str) -> Path:
        return self.memories_dir(agent_name) / "long_term.md"

    def episodic_memory_path(self, agent_name: str) -> Path:
        return self.memories_dir(agent_name) / "episodic.md"

    def all_memory_paths(self, agent_name: str) -> list[Path]:
        return [
            self.short_term_memory_path(agent_name),
            self.long_term_memory_path(agent_name),
            self.episodic_memory_path(agent_name),
        ]

    def ensure_memory_files(self, agent_name: str) -> list[Path]:
        """Backward-compat wrapper — delegates to ensure_memory_layout()."""
        return self.ensure_memory_layout(agent_name)

    def memory_index_path(self, agent_name: str) -> Path:
        """~/.rai/agents/<name>/MEMORY.md — index of all memory files."""
        return self.agent_dir(agent_name) / "MEMORY.md"

    def memory_dir(self, agent_name: str) -> Path:
        """~/.rai/agents/<name>/memory/ — typed memory files."""
        return self.agent_dir(agent_name) / "memory"

    def ensure_memory_layout(self, agent_name: str) -> list[Path]:
        """Create MEMORY.md index + all typed files. Auto-migrates old memories/ content.

        Returns load order: [MEMORY.md, user.md, feedback.md,
        engagement.md, target.md, findings.md, methodology.md]
        """
        self.ensure_agent_dir(agent_name)
        mem_dir = self.memory_dir(agent_name)
        mem_dir.mkdir(parents=True, exist_ok=True)

        typed_files = {
            "user.md": _USER_MEM,
            "feedback.md": _FEEDBACK_MEM,
            "engagement.md": _ENGAGEMENT_MEM,
            "target.md": _TARGET_MEM,
            "findings.md": _FINDINGS_MEM,
            "methodology.md": _METHODOLOGY_MEM,
        }
        for fname, template in typed_files.items():
            p = mem_dir / fname
            if not p.exists():
                p.write_text(template, encoding="utf-8")

        index_path = self.memory_index_path(agent_name)
        if not index_path.exists():
            index_path.write_text(_MEMORY_INDEX.format(agent_name=agent_name), encoding="utf-8")

        self._migrate_old_memories(agent_name, mem_dir)
        return [index_path] + [mem_dir / f for f in typed_files]

    def _migrate_old_memories(self, agent_name: str, mem_dir: Path) -> None:
        """Copy substantive content from old memories/ files into new typed layout."""
        old_dir = self.memories_dir(agent_name)
        if not old_dir.exists():
            return
        mapping = {
            "short_term.md": "engagement.md",
            "long_term.md": "target.md",
            "episodic.md": "findings.md",
        }
        for old_name, new_name in mapping.items():
            old_p = old_dir / old_name
            new_p = mem_dir / new_name
            if not old_p.exists():
                continue
            content = old_p.read_text(encoding="utf-8")
            substantive = [l for l in content.splitlines() if l.strip() and not l.startswith("#")]
            if substantive:
                existing = new_p.read_text(encoding="utf-8")
                new_p.write_text(
                    existing + f"\n\n<!-- Migrated from {old_name} -->\n" + content,
                    encoding="utf-8",
                )

    # ---- skills ----

    @property
    def user_skills_dir(self) -> Path:
        """~/.rai/skills/ — user-level skills."""
        return self.user_rai_dir / "skills"

    def agent_skills_dir(self, agent_name: str) -> Path:
        """~/.rai/agents/<name>/skills/ — agent-specific skills."""
        return self.agent_dir(agent_name) / "skills"

    def ensure_skills_dirs(self, agent_name: str) -> tuple[Path, Path]:
        user = self.user_skills_dir
        agent = self.agent_skills_dir(agent_name)
        user.mkdir(parents=True, exist_ok=True)
        agent.mkdir(parents=True, exist_ok=True)
        return user, agent

    # ---- subagents (AGENTS.md style) ----

    def agent_md_path(self, agent_name: str) -> Path:
        """~/.rai/agents/<name>/AGENTS.md — agent persona/instructions."""
        return self.agent_dir(agent_name) / "AGENTS.md"

    def ensure_agent_md(self, agent_name: str, default_content: str = "") -> Path:
        p = self.agent_md_path(agent_name)
        self.ensure_agent_dir(agent_name)
        if not p.exists():
            p.write_text(default_content, encoding="utf-8")
        return p

    # ---- MCP config ----

    @property
    def user_mcp_config_path(self) -> Path:
        """~/.rai/.mcp.json — user-level MCP config."""
        return self.user_rai_dir / ".mcp.json"

    # ---- audit log ----

    @property
    def audit_log_path(self) -> Path:
        """~/.rai/audit.log — all tool calls logged here."""
        return self.user_rai_dir / "audit.log"

    def ensure_audit_log(self) -> Path:
        self.user_rai_dir.mkdir(parents=True, exist_ok=True)
        if not self.audit_log_path.exists():
            self.audit_log_path.touch()
        return self.audit_log_path

    # ---- hooks config ----

    @property
    def hooks_config_path(self) -> Path:
        """~/.rai/hooks.json — RAI-native hooks config (Claude Code format)."""
        return self.user_rai_dir / "hooks.json"

    def ensure_hooks_config(self) -> Path:
        """Create ~/.rai/hooks.json with an empty hooks skeleton if absent."""
        import json
        self.user_rai_dir.mkdir(parents=True, exist_ok=True)
        if not self.hooks_config_path.exists():
            self.hooks_config_path.write_text(
                json.dumps({"hooks": {}}, indent=2) + "\n",
                encoding="utf-8",
            )
        return self.hooks_config_path

    # ---- global user profile (shared across ALL agents) ----

    @property
    def global_user_dir(self) -> Path:
        """~/.rai/user/ — user profile loaded by every agent at startup."""
        return self.user_rai_dir / "user"

    @property
    def global_memory_index_path(self) -> Path:
        """~/.rai/user/MEMORY.md — index for the global profile."""
        return self.global_user_dir / "MEMORY.md"

    def ensure_global_user_profile(self) -> list[Path]:
        """Seed ~/.rai/user/ and return load order: [MEMORY.md, profile.md, preferences.md, context.md].

        Called once per agent startup. Profile files are created from templates only
        when absent — existing content is never overwritten.
        Returns paths in load order: index first so MemoryMiddleware shows the index
        at the top of the injected context.
        """
        d = self.global_user_dir
        d.mkdir(parents=True, exist_ok=True)

        files = {
            "profile.md":     _GLOBAL_PROFILE,
            "preferences.md": _GLOBAL_PREFERENCES,
            "context.md":     _GLOBAL_CONTEXT,
        }
        for fname, template in files.items():
            p = d / fname
            if not p.exists():
                p.write_text(template, encoding="utf-8")

        index = self.global_memory_index_path
        if not index.exists():
            index.write_text(_GLOBAL_MEMORY_INDEX, encoding="utf-8")

        return [index] + [d / f for f in files]

    # ---- global target memory (shared across all agents) ----

    def targets_dir(self) -> Path:
        """~/.rai/targets/ — global, shared across all agents."""
        return self.user_rai_dir / "targets"

    def target_dir(self, target: str) -> Path:
        """~/.rai/targets/<target>/"""
        return self.targets_dir() / target

    def target_memory_dir(self, target: str) -> Path:
        """~/.rai/targets/<target>/memory/"""
        return self.target_dir(target) / "memory"

    def target_memory_index_path(self, target: str) -> Path:
        """~/.rai/targets/<target>/MEMORY.md"""
        return self.target_dir(target) / "MEMORY.md"

    def ensure_target_memory(self, target: str) -> list[Path]:
        """Seed target memory dir and return load order: [MEMORY.md, engagement, recon, findings, notes]."""
        mem_dir = self.target_memory_dir(target)
        mem_dir.mkdir(parents=True, exist_ok=True)

        typed_files = {
            "engagement.md":  _TARGET_ENGAGEMENT_MEM.format(target_name=target),
            "recon.md":       _TARGET_RECON_MEM.format(target_name=target),
            "findings.md":    _TARGET_FINDINGS_MEM.format(target_name=target),
            "notes.md":       _TARGET_NOTES_MEM.format(target_name=target),
            "methodology.md": _TARGET_METHODOLOGY_MEM.format(target_name=target),
        }
        for fname, template in typed_files.items():
            p = mem_dir / fname
            if not p.exists():
                p.write_text(template, encoding="utf-8")

        index_path = self.target_memory_index_path(target)
        if not index_path.exists():
            index_path.write_text(_TARGET_MEMORY_INDEX.format(target_name=target), encoding="utf-8")

        return [index_path] + [mem_dir / f for f in typed_files]

    def list_targets(self) -> list[str]:
        """Return sorted list of target names that have been scaffolded."""
        d = self.targets_dir()
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_dir())

    # ---- findings ----

    def findings_path(self, agent_name: str) -> Path:
        """~/.rai/agents/<name>/findings.json — tracked findings."""
        return self.agent_dir(agent_name) / "findings.json"

    # ---- config file ----

    @property
    def config_file(self) -> Path:
        """~/.rai/config.toml — user config."""
        return self.user_rai_dir / "config.toml"

    # ---- model settings (from env or config) ----

    @property
    def model_name(self) -> str | None:
        return os.environ.get("RAI_MODEL") or os.environ.get("DEEPAGENTS_MODEL")

    @property
    def model_provider(self) -> str | None:
        name = self.model_name or ""
        if ":" in name:
            return name.split(":")[0]
        return None

    # ---- rate limiting ----

    @property
    def rate_limit_profile(self) -> str:
        """Rate-limit profile from env (default 'normal')."""
        return os.environ.get("RAI_RATE_LIMIT_PROFILE", "normal")

    # ---- shell allow-list ----

    @property
    def shell_allow_list(self) -> list[str] | _ShellAllowAll:
        raw = os.environ.get("RAI_SHELL_ALLOW_LIST", "")
        if not raw or raw.strip().lower() == "all":
            return SHELL_ALLOW_ALL
        return [x.strip() for x in raw.split(",") if x.strip()]

    # ---- project claude compat dirs ----

    @staticmethod
    def get_project_skills_dir(cwd: Path | None = None) -> Path | None:
        base = cwd or Path.cwd()
        p = base / ".rai" / "skills"
        return p if p.exists() else None

    @staticmethod
    def get_project_claude_skills_dir(cwd: Path | None = None) -> Path | None:
        """Claude Code compat: .claude/skills/."""
        base = cwd or Path.cwd()
        p = base / ".claude" / "skills"
        return p if p.exists() else None

    @staticmethod
    def get_user_claude_skills_dir() -> Path:
        """Claude Code compat: ~/.claude/skills/."""
        return Path.home() / ".claude" / "skills"

    @staticmethod
    def get_project_agent_md_paths(cwd: Path | None = None) -> list[Path]:
        """Project-level AGENTS.md files (claude-code compat)."""
        base = cwd or Path.cwd()
        candidates = [
            base / ".rai" / "AGENTS.md",
            base / "AGENTS.md",
            base / "CLAUDE.md",
            base / ".claude" / "CLAUDE.md",
        ]
        return [p for p in candidates if p.exists()]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_settings_lock = threading.Lock()
_settings_instance: RAISettings | None = None


def get_settings() -> RAISettings:
    """Return the global RAISettings singleton."""
    global _settings_instance
    if _settings_instance is None:
        with _settings_lock:
            if _settings_instance is None:
                _settings_instance = RAISettings()
    return _settings_instance


settings = get_settings()
