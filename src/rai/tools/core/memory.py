"""Typed memory tools for RAI agents.

Agents use these instead of write_file / edit_file to read and update their
memory without needing to know filesystem paths.  The tools resolve the correct
path from agent_name and target that are bound at creation time via
get_memory_tools(agent_name, target).

Five tools:
  memory_files_list — list available memory files + MEMORY.md index
  memory_read       — read a named memory file
  memory_write      — replace or append to a named memory file
  memory_update     — exact-string replacement in a named memory file
  memory_path       — resolve absolute path (named or custom file) for direct write_file/edit_file use

Scope:
  "global" → ~/.rai/user/<file>.md            (shared across ALL agents — read/write to teach every agent)
  "agent"  → ~/.rai/agents/<name>/memory/<file>.md  (this agent only)
  "target" → ~/.rai/targets/<target>/memory/<file>.md  (requires active target)

Valid file names per scope:
  global : profile, preferences, context
  agent  : user, feedback, engagement, target_overview, findings, methodology
  target : engagement, recon, findings, notes, methodology

Use scope="global" when:
  - You learn something about the user that should apply to ALL agents
    (e.g. role, expertise, preferred output style, current project)
  - The user corrects a behaviour that should never happen in any agent
  - You want to share recon / context between different specialist agents
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# File registries — short name → filename on disk
# ---------------------------------------------------------------------------

# Shared across every agent — ~/.rai/user/
_GLOBAL_FILES: dict[str, str] = {
    "profile":     "profile.md",
    "preferences": "preferences.md",
    "context":     "context.md",
}

_AGENT_FILES: dict[str, str] = {
    "user":            "user.md",
    "feedback":        "feedback.md",
    "engagement":      "engagement.md",
    "target_overview": "target.md",
    "findings":        "findings.md",
    "methodology":     "methodology.md",
}

_TARGET_FILES: dict[str, str] = {
    "engagement":  "engagement.md",
    "recon":       "recon.md",
    "findings":    "findings.md",
    "notes":       "notes.md",
    "methodology": "methodology.md",
}

_SCOPE_LABELS = {"global", "agent", "target"}


def _resolve_path(file: str, scope: str, agent_name: str, target: str) -> Path | str:
    """Return the resolved Path for the named memory file, or an error string."""
    from rai.config.settings import settings

    scope = scope.strip().lower()
    if scope not in _SCOPE_LABELS:
        return f"Invalid scope '{scope}'. Must be 'global', 'agent', or 'target'."

    if scope == "global":
        registry = _GLOBAL_FILES
        if file not in registry:
            valid = ", ".join(sorted(registry))
            return (
                f"Unknown global memory file '{file}'. Valid names: {valid}. "
                "Use scope='global' for profile/preferences/context shared across all agents."
            )
        return settings.global_user_dir / registry[file]

    if scope == "target":
        if not target:
            return "No target is active. Start rai with --target <name> to use target-scoped memory."
        registry = _TARGET_FILES
        if file not in registry:
            valid = ", ".join(sorted(registry))
            return f"Unknown target memory file '{file}'. Valid names: {valid}"
        return settings.target_memory_dir(target) / registry[file]

    # agent scope
    registry = _AGENT_FILES
    if file not in registry:
        valid = ", ".join(sorted(registry))
        return f"Unknown agent memory file '{file}'. Valid names: {valid}"
    return settings.memory_dir(agent_name) / registry[file]


# ---------------------------------------------------------------------------
# memory_read
# ---------------------------------------------------------------------------


class MemoryReadInput(BaseModel):
    file: str = Field(
        description=(
            "Memory file to read. "
            "Global scope: profile, preferences, context. "
            "Agent scope: user, feedback, engagement, target_overview, findings, methodology. "
            "Target scope: engagement, recon, findings, notes, methodology."
        )
    )
    scope: str = Field(
        default="agent",
        description=(
            "'global' for shared user profile (all agents). "
            "'agent' for this agent's memory. "
            "'target' for target-scoped memory."
        ),
    )
    offset: int = Field(
        default=0,
        description=(
            "Character offset to start reading from (default 0 = beginning). "
            "Use to paginate large files: pass the offset from the truncation notice."
        ),
    )
    max_chars: int = Field(
        default=20_000,
        description=(
            "Maximum characters to return (default 20000 ≈ 5000 tokens). "
            "Increase only when you need more context in a single call."
        ),
    )


class MemoryReadTool(BaseTool):
    """Read a named memory file without needing to know its path."""

    name: str = "memory_read"
    description: str = (
        "Read a memory file by name. "
        "Returns up to max_chars characters (default 20000 ≈ 5000 tokens). "
        "If the file is larger, a truncation notice is appended with the next offset — "
        "call again with offset=<next> to read subsequent pages. "
        "scope='global' reads the shared user profile (profile/preferences/context). "
        "scope='agent' reads this agent's memory (user, feedback, methodology, …). "
        "scope='target' reads target-specific memory (recon, findings, notes, methodology)."
    )
    args_schema: ClassVar[type[BaseModel]] = MemoryReadInput

    agent_name: str = ""
    target: str = ""

    def _run(
        self,
        file: str,
        scope: str = "agent",
        offset: int = 0,
        max_chars: int = 20_000,
        **kwargs: Any,
    ) -> str:
        result = _resolve_path(file, scope, self.agent_name, self.target)
        if isinstance(result, str):
            return result
        path: Path = result
        if not path.exists():
            return f"Memory file does not exist yet: {path}"
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            return f"Failed to read {path}: {e}"

        total = len(content)
        chunk = content[offset : offset + max_chars]
        if offset + max_chars < total:
            remaining = total - (offset + max_chars)
            next_offset = offset + max_chars
            chunk += (
                f"\n\n[Showing {max_chars} chars from offset {offset} of {total} total. "
                f"{remaining} chars remaining. "
                f"Call memory_read(file='{file}', scope='{scope}', offset={next_offset}) to read more.]"
            )
        return chunk


# ---------------------------------------------------------------------------
# memory_write
# ---------------------------------------------------------------------------


class MemoryWriteInput(BaseModel):
    file: str = Field(
        description=(
            "Memory file to write. "
            "Global scope: profile, preferences, context. "
            "Agent scope: user, feedback, engagement, target_overview, findings, methodology. "
            "Target scope: engagement, recon, findings, notes, methodology."
        )
    )
    content: str = Field(
        default="",
        description=(
            "Content to write or append inline. "
            "For content larger than ~500 lines, write to a /tmp file with bash first "
            "and use content_file= instead to avoid tool call size limits."
        ),
    )
    content_file: str = Field(
        default="",
        description=(
            "Path to a file whose content will be written or appended. "
            "Use this for large content (scripts, reports, exploit code) that would "
            "exceed tool call size limits if passed inline via content=. "
            "Workflow: bash → write to /tmp/something.md, then memory_write(content_file='/tmp/something.md'). "
            "Takes precedence over content= when both are provided."
        ),
    )
    scope: str = Field(
        default="agent",
        description=(
            "'global' for shared user profile seen by ALL agents (profile/preferences/context). "
            "'agent' for this agent's memory. "
            "'target' for target-scoped memory."
        ),
    )
    mode: str = Field(
        default="append",
        description=(
            "'append' adds content at the end of the file (default — preserves existing notes). "
            "'replace' overwrites the entire file."
        ),
    )


class MemoryWriteTool(BaseTool):
    """Write or append to a named memory file without needing to know its path."""

    name: str = "memory_write"
    description: str = (
        "Write or append to a memory file by name. "
        "scope='global' writes to the shared user profile (profile/preferences/context) — "
        "the change will be seen by EVERY agent in future sessions. "
        "Use this when you learn something about the user that should apply universally "
        "(role, preferences, ongoing project, feedback on your behaviour). "
        "scope='agent' writes to this agent's private memory. "
        "scope='target' writes to target-specific memory. "
        "Default mode is 'append' — preserves existing content. "
        "For large content (> ~500 lines): write to /tmp first with bash, "
        "then call memory_write(content_file='/tmp/yourfile.md')."
    )
    args_schema: ClassVar[type[BaseModel]] = MemoryWriteInput

    agent_name: str = ""
    target: str = ""

    def _run(
        self,
        file: str,
        content: str = "",
        content_file: str = "",
        scope: str = "agent",
        mode: str = "append",
        **kwargs: Any,
    ) -> str:
        result = _resolve_path(file, scope, self.agent_name, self.target)
        if isinstance(result, str):
            return result
        path: Path = result

        mode = mode.strip().lower()
        if mode not in {"append", "replace"}:
            return f"Invalid mode '{mode}'. Must be 'append' or 'replace'."

        # content_file takes precedence — read actual content from disk
        if content_file:
            src = Path(content_file).expanduser().resolve()
            _allowed = (
                Path("/tmp").resolve(),
                Path(tempfile.gettempdir()).resolve(),
                Path("~/.rai").expanduser().resolve(),
            )
            if not any(str(src).startswith(str(p)) for p in _allowed):
                return (
                    f"content_file path '{content_file}' is not allowed. "
                    "Use a path under /tmp/ or ~/.rai/ only."
                )
            if not src.exists():
                return f"content_file not found: {content_file}"
            try:
                content = src.read_text(encoding="utf-8")
            except OSError as e:
                return f"Failed to read content_file {content_file}: {e}"

        if not content:
            return (
                "memory_write failed: content arrived empty. "
                "This usually means the content exceeded the model's output token limit "
                "and was truncated mid-value by Bedrock before reaching the tool. "
                "Try writing in smaller sections (≤200 lines each), "
                "or write the content to /tmp/ with bash first "
                "and call memory_write(content_file='/tmp/yourfile.md')."
            )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            source = f"from {content_file}" if content_file else "inline"
            if mode == "replace":
                path.write_text(content, encoding="utf-8")
                total = len(content)
                return (
                    f"Replaced {path.name} ({len(content):,} chars written, {source}). "
                    f"File now {total:,} chars (~{total // 4:,} tokens)."
                )

            # append — add a blank line separator before the new content
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            separator = "\n" if existing.endswith("\n") else "\n\n"
            updated = existing + separator + content
            path.write_text(updated, encoding="utf-8")
            added = len(content)
            total = len(updated)
            return (
                f"Appended to {path.name} ({added:,} chars added, {source}). "
                f"File now {total:,} chars (~{total // 4:,} tokens)."
            )
        except OSError as e:
            return f"Failed to write {path}: {e}"


# ---------------------------------------------------------------------------
# memory_update
# ---------------------------------------------------------------------------


class MemoryUpdateInput(BaseModel):
    file: str = Field(
        description=(
            "Memory file to update. "
            "Global scope: profile, preferences, context. "
            "Agent scope: user, feedback, engagement, target_overview, findings, methodology. "
            "Target scope: engagement, recon, findings, notes, methodology."
        )
    )
    old_text: str = Field(
        description="Exact text to find in the file. Must match character-for-character."
    )
    new_text: str = Field(description="Replacement text.")
    scope: str = Field(
        default="agent",
        description=(
            "'global' for shared user profile (all agents). "
            "'agent' for this agent's memory. "
            "'target' for target-scoped memory."
        ),
    )


class MemoryUpdateTool(BaseTool):
    """Replace an exact string in a named memory file without needing to know its path."""

    name: str = "memory_update"
    description: str = (
        "Replace an exact string in a memory file. "
        "old_text must match the file content exactly (whitespace included). "
        "If old_text is not found, returns the first 400 chars of the file so you can correct it. "
        "scope='global' edits the shared user profile seen by all agents. "
        "scope='agent' edits this agent's memory. "
        "scope='target' edits target-specific memory."
    )
    args_schema: ClassVar[type[BaseModel]] = MemoryUpdateInput

    agent_name: str = ""
    target: str = ""

    def _run(
        self,
        file: str,
        old_text: str,
        new_text: str,
        scope: str = "agent",
        **kwargs: Any,
    ) -> str:
        result = _resolve_path(file, scope, self.agent_name, self.target)
        if isinstance(result, str):
            return result
        path: Path = result

        if not path.exists():
            return f"Memory file does not exist yet: {path}"

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            return f"Failed to read {path}: {e}"

        if old_text not in content:
            preview = content[:400].replace("\n", "↵")
            return (
                f"old_text not found in {path.name}. "
                f"Use memory_read to check the current content first.\n"
                f"File preview (first 400 chars):\n{preview}"
            )

        updated = content.replace(old_text, new_text, 1)
        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as e:
            return f"Failed to write {path}: {e}"

        lines_changed = abs(new_text.count("\n") - old_text.count("\n"))
        old_size = len(content)
        new_size = len(updated)
        return (
            f"Updated {path.name} ({lines_changed} line(s) changed). "
            f"Size: {old_size:,} → {new_size:,} chars (~{new_size // 4:,} tokens)."
        )


# ---------------------------------------------------------------------------
# memory_files_list
# ---------------------------------------------------------------------------


class MemoryFilesListInput(BaseModel):
    scope: str = Field(
        default="all",
        description=(
            "'global' — list shared user profile files (profile/preferences/context). "
            "'agent' — list this agent's memory files. "
            "'target' — list target-scoped memory files (requires active target). "
            "'all' — list all three scopes (default)."
        ),
    )


class MemoryFilesListTool(BaseTool):
    """List available memory files and show the MEMORY.md index for each scope."""

    name: str = "memory_files_list"
    description: str = (
        "List all memory files available to this agent and show their MEMORY.md index. "
        "Use this before memory_read/memory_write to discover what files exist and what they contain. "
        "scope='global' for shared user profile, scope='agent' for this agent's memory, "
        "scope='target' for target-specific memory, scope='all' (default) for everything."
    )
    args_schema: ClassVar[type[BaseModel]] = MemoryFilesListInput

    agent_name: str = ""
    target: str = ""

    def _run(self, scope: str = "all", **kwargs: Any) -> str:
        from rai.config.settings import settings

        scope = scope.strip().lower()
        if scope not in {"global", "agent", "target", "all"}:
            return f"Invalid scope '{scope}'. Must be 'global', 'agent', 'target', or 'all'."

        sections: list[str] = []

        if scope in {"global", "all"}:
            sections.append(self._describe_scope(
                label="Global user profile — shared across ALL agents",
                mem_dir=settings.global_user_dir,
                index_path=settings.global_memory_index_path,
                registry=_GLOBAL_FILES,
            ))

        if scope in {"agent", "all"}:
            sections.append(self._describe_scope(
                label=f"Agent memory — {self.agent_name}",
                mem_dir=settings.memory_dir(self.agent_name),
                index_path=settings.memory_index_path(self.agent_name),
                registry=_AGENT_FILES,
            ))

        if scope in {"target", "all"}:
            if not self.target:
                sections.append(
                    "Target memory: no target is active. "
                    "Start rai with --target <name> to use target-scoped memory."
                )
            else:
                sections.append(self._describe_scope(
                    label=f"Target memory — {self.target}",
                    mem_dir=settings.target_memory_dir(self.target),
                    index_path=settings.target_memory_index_path(self.target),
                    registry=_TARGET_FILES,
                ))

        return "\n\n".join(sections)

    @staticmethod
    def _describe_scope(
        label: str,
        mem_dir: Path,
        index_path: Path,
        registry: dict[str, str],
    ) -> str:
        lines = [f"## {label}"]

        # MEMORY.md index
        if index_path.exists():
            try:
                lines.append(index_path.read_text(encoding="utf-8").strip())
            except OSError:
                lines.append("(could not read MEMORY.md)")
        else:
            lines.append("MEMORY.md not yet created.")

        # File list
        lines.append("\n### Files")
        for short_name, filename in registry.items():
            path = mem_dir / filename
            if path.exists():
                size = path.stat().st_size
                empty = size < 120  # templates are ~100 bytes of frontmatter only
                status = "empty (template only)" if empty else f"{size} bytes"
                lines.append(f"  {short_name:<16} ✔  {status}")
            else:
                lines.append(f"  {short_name:<16} —  not created yet")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# memory_path
# ---------------------------------------------------------------------------

import re as _re

_SAFE_FILENAME = _re.compile(r'^[a-zA-Z0-9_\-]+$')


class MemoryPathInput(BaseModel):
    file: str = Field(
        description=(
            "Named memory file (e.g. 'methodology', 'profile') OR a custom filename "
            "without extension (e.g. 'ssl_notes', 'jwt_bypass'). "
            "Custom names must be alphanumeric with underscores/hyphens only — "
            "a .md extension is added automatically. "
            "Global scope names: profile, preferences, context. "
            "Agent scope names: user, feedback, engagement, target_overview, findings, methodology. "
            "Target scope names: engagement, recon, findings, notes, methodology. "
            "Custom names are only allowed for agent and target scopes."
        )
    )
    scope: str = Field(
        default="agent",
        description=(
            "'global' for shared user profile dir (all agents). "
            "'agent' for this agent's memory dir. "
            "'target' for target-scoped memory dir."
        ),
    )
    create_if_missing: bool = Field(
        default=False,
        description=(
            "If True and the file does not exist, create it as an empty .md file "
            "so write_file / edit_file can operate on it immediately."
        ),
    )


class MemoryPathTool(BaseTool):
    """Resolve the absolute path of a named or custom memory file.

    Use this when you need to pass the file path to write_file or edit_file
    directly.  For named files (methodology, recon, etc.) the path is looked up
    from the registry.  For custom files the path is constructed inside the
    scope's memory directory — the directory is always created so write_file
    will not fail with a missing-parent error.
    """

    name: str = "memory_path"
    description: str = (
        "Return the absolute filesystem path of a named or custom memory file. "
        "Use this to get a path you can pass directly to write_file or edit_file. "
        "For known file names (methodology, recon, notes, …) returns the canonical path. "
        "For custom names (e.g. 'ssl_notes', 'jwt_bypass') creates the path inside the "
        "scope's memory directory. "
        "Set create_if_missing=True to create an empty file immediately."
    )
    args_schema: ClassVar[type[BaseModel]] = MemoryPathInput

    agent_name: str = ""
    target: str = ""

    def _run(
        self,
        file: str,
        scope: str = "agent",
        create_if_missing: bool = False,
        **kwargs: Any,
    ) -> str:
        from rai.config.settings import settings

        scope = scope.strip().lower()
        if scope not in _SCOPE_LABELS:
            return f"Invalid scope '{scope}'. Must be 'global', 'agent', or 'target'."

        if scope == "target" and not self.target:
            return (
                "No target is active. "
                "Start rai with --target <name> to use target-scoped memory."
            )

        # Resolve memory directory and registry for the scope
        if scope == "global":
            mem_dir = settings.global_user_dir
            registry = _GLOBAL_FILES
            allow_custom = False  # global files are fixed — no custom files in the shared profile
        elif scope == "target":
            mem_dir = settings.target_memory_dir(self.target)
            registry = _TARGET_FILES
            allow_custom = True
        else:
            mem_dir = settings.memory_dir(self.agent_name)
            registry = _AGENT_FILES
            allow_custom = True

        # Resolve filename — named registry entry or validated custom name
        if file in registry:
            filename = registry[file]
        elif allow_custom:
            # Strip .md suffix if provided, validate bare name, then re-add
            bare = file.removesuffix(".md")
            if not _SAFE_FILENAME.match(bare):
                return (
                    f"Invalid custom file name '{file}'. "
                    "Use only letters, digits, underscores, and hyphens (e.g. 'ssl_notes')."
                )
            filename = f"{bare}.md"
        else:
            valid = ", ".join(sorted(registry))
            return (
                f"Unknown global memory file '{file}'. Valid names: {valid}. "
                "Custom file names are not allowed in the global scope."
            )

        path = mem_dir / filename

        # Always ensure the directory exists so write_file won't fail
        try:
            mem_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"Failed to create memory directory {mem_dir}: {e}"

        # Optionally create empty file
        if create_if_missing and not path.exists():
            try:
                path.write_text(
                    f"---\ndescription: {file}\n---\n\n",
                    encoding="utf-8",
                )
                status = "created (empty)"
            except OSError as e:
                return f"Path resolved to {path} but could not create file: {e}"
        else:
            if path.exists():
                status = f"exists — {path.stat().st_size} bytes"
            else:
                status = "does not exist yet — use write_file to create it"

        return f"{path}\n[{status}]"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_memory_tools(agent_name: str, target: str = "") -> list[BaseTool]:
    """Return memory tools pre-wired with agent_name and target context.

    Call this in create_rai_agent() after agent_name and target are resolved,
    then extend the tools list:
        tools.extend(get_memory_tools(agent_name, target))
    """
    return [
        MemoryFilesListTool(agent_name=agent_name, target=target),
        MemoryReadTool(agent_name=agent_name, target=target),
        MemoryWriteTool(agent_name=agent_name, target=target),
        MemoryUpdateTool(agent_name=agent_name, target=target),
        MemoryPathTool(agent_name=agent_name, target=target),
    ]
