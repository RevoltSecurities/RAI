"""Built-in LangGraph tools for RAI agents.

- **BashTool** — run shell commands with env isolation, stderr labelling,
  output truncation, allowlist enforcement, and structured exit-code reporting.

Env isolation follows the deepagents LocalShellBackend pattern: os.environ is
inherited but credential-bearing keys (matching *_API_KEY, *_SECRET, etc.) are
stripped before the subprocess is spawned so agents cannot read or exfiltrate
them through shell commands.

Subagent dispatch (parallel and background) is handled automatically by
the deepagents SDK ``task`` tool which ``create_deep_agent()`` injects when
subagents are configured.  Do NOT add stub wrappers here.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CWD ContextVar — set by execute_run() at run start
# ---------------------------------------------------------------------------

_cwd_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "rai_bash_cwd", default=""
)


def set_bash_cwd(cwd: str) -> contextvars.Token:
    """Set the default working directory for BashTool in the current run.

    Returns the contextvars Token so the caller can reset it in finally.
    """
    return _cwd_ctx.set(cwd)


def reset_bash_cwd(token: contextvars.Token) -> None:
    """Reset the CWD contextvar to its previous value."""
    _cwd_ctx.reset(token)


def get_bash_cwd() -> str:
    """Get the current run's CWD (empty string if not set)."""
    return _cwd_ctx.get()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_INLINE_CHARS = 5_000            # anything larger spills to /tmp
_SPILL_PREVIEW_CHARS = 500
_MAX_TIMEOUT_SECONDS = 3600          # hard cap — prevents runaway blocking calls
_TIMEOUT_EXIT_CODE = 124             # POSIX standard for timeout

# Credential key patterns to strip from the subprocess environment.
# Narrows to well-known patterns — tool env vars (SHODAN_*, etc.) are preserved.
_CREDENTIAL_SUFFIX = re.compile(
    r"(_API_KEY|_APIKEY|_SECRET_KEY|_SECRET|_TOKEN|_PASSWORD|_PASSWD|_PRIVATE_KEY)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------


def _sanitized_env() -> dict[str, str]:
    """Return os.environ with credential-bearing keys removed.

    Security tool env vars (e.g. SHODAN_API_KEY, GITHUB_TOKEN) are stripped
    so that a shell command cannot read and exfiltrate them.  PATH, HOME, USER
    and all non-credential vars are preserved so pentesting tools still work.
    """
    return {k: v for k, v in os.environ.items() if not _CREDENTIAL_SUFFIX.search(k)}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class BashInput(BaseModel):
    command: str = Field(
        ...,
        description=(
            "Shell command to execute. Supports full shell syntax: pipes (|), "
            "chaining (&&, ||, ;), redirection (>, >>), subshells ($(cmd)), "
            "globs, env vars, here-docs, and multi-line scripts."
        ),
    )
    timeout: int = Field(
        600,
        description=(
            f"Max seconds to wait (default 600; max {_MAX_TIMEOUT_SECONDS}). "
            "Increase for long-running scans (nmap, nuclei, ffuf)."
        ),
    )
    working_dir: str = Field(
        "",
        description=(
            "Working directory for command execution. "
            "Leave empty to use the session's startup directory (auto-resolved). "
            "Only set explicitly when you need to run in a different path."
        ),
    )


# ---------------------------------------------------------------------------
# BashTool
# ---------------------------------------------------------------------------


class BashTool(BaseTool):
    """Execute a shell command on the host system.

    Security properties (matching deepagents LocalShellBackend):
    - Credential env vars (*_API_KEY, *_SECRET, *_TOKEN, …) are stripped before
      the subprocess is spawned so the agent cannot read or exfiltrate them.
    - stderr lines are prefixed with ``[stderr]`` for clear attribution.
    - Output exceeding 100 000 bytes is spilled to /tmp and the path + preview
      is returned so the LLM context window is not overwhelmed.
    - Timeout exit code 124 (POSIX standard).
    - Optional command-level allowlist via RAI_SHELL_ALLOW_LIST env var.
    """

    name: str = "bash"
    description: str = (
        "Run any shell command on the host. Full shell syntax is supported: "
        "pipes (cmd1 | cmd2), chaining (&&, ||), semicolons, redirection, "
        "subshells ($(cmd)), globs, env vars, here-docs, multi-line scripts. "
        "stdout and stderr are returned combined; stderr lines are prefixed "
        "with [stderr]. "
        f"Output > {_MAX_INLINE_CHARS:,} chars is written to /tmp and the path "
        "is returned — use bash to read it for the full content."
    )
    args_schema: type[BaseModel] = BashInput

    # ------------------------------------------------------------------
    # Allowlist check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_allowlist(command: str) -> str | None:
        """Return an error string if the command is blocked by RAI_SHELL_ALLOW_LIST,
        or None if the command is allowed."""
        from rai.config.settings import (
            settings, SHELL_ALLOW_ALL, is_shell_command_allowed,
            extract_command_binary,
        )

        allow = settings.shell_allow_list
        if allow is SHELL_ALLOW_ALL:
            return None
        if not is_shell_command_allowed(command, allow):  # type: ignore[arg-type]
            binary = extract_command_binary(command) or command[:30]
            return (
                f"[blocked] '{binary}' is not in RAI_SHELL_ALLOW_LIST. "
                f"Allowed: {', '.join(allow)}"  # type: ignore[arg-type]
            )
        return None

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def _run(
        self,
        command: str,
        timeout: int = 600,
        working_dir: str = "",
        **kwargs: Any,
    ) -> str:
        # Validate command
        if not command or not isinstance(command, str) or not command.strip():
            return "[exit 1]\nError: command must be a non-empty string."

        # Allowlist enforcement
        blocked = self._check_allowlist(command)
        if blocked:
            return blocked

        # Resolve CWD: explicit > ContextVar > process CWD
        explicit_cwd = working_dir.strip()
        _cwd_applied = False
        if explicit_cwd:
            cwd = explicit_cwd
        else:
            ctx_cwd = _cwd_ctx.get()
            cwd = ctx_cwd or None
            _cwd_applied = bool(ctx_cwd)
        effective_timeout = min(max(1, timeout), _MAX_TIMEOUT_SECONDS)

        try:
            proc = subprocess.run(
                command,
                shell=True,          # noqa: S602 — intentional for LLM-controlled shell
                check=False,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=cwd,
                env=_sanitized_env(),
            )
        except subprocess.TimeoutExpired:
            return (
                f"[exit {_TIMEOUT_EXIT_CODE}]\n"
                f"Command timed out after {effective_timeout}s. "
                "For long-running tools (nmap, nuclei) increase the timeout parameter."
            )
        except Exception as exc:
            return f"[exit 1]\nError spawning process: {exc}"

        # Build output: stdout then [stderr]-prefixed stderr
        parts: list[str] = []
        if proc.stdout:
            parts.append(proc.stdout)
        if proc.stderr:
            stderr_lines = proc.stderr.rstrip("\n").split("\n")
            parts.extend(f"[stderr] {line}" for line in stderr_lines)

        output = "\n".join(parts) if parts else "<no output>"

        # Spill large output to /tmp so agent context is not overwhelmed
        if len(output) > _MAX_INLINE_CHARS:
            output_bytes = output.encode("utf-8", errors="replace")
            h = hashlib.md5(output_bytes, usedforsecurity=False).hexdigest()[:8]
            tmp_path = Path(f"/tmp/rai_out_{h}.txt")
            try:
                tmp_path.write_bytes(output_bytes)
                preview = output[:_SPILL_PREVIEW_CHARS]
                return (
                    f"Result too large ({len(output):,} chars). "
                    f"Read from: {tmp_path}\n\n"
                    f"Preview (first {_SPILL_PREVIEW_CHARS} chars):\n{preview}"
                )
            except OSError as exc:
                logger.warning("Could not spill large output to %s: %s", tmp_path, exc)
                # Fall through — truncate inline
                output = output[:_MAX_INLINE_CHARS] + f"\n... (output truncated at {_MAX_INLINE_CHARS:,} chars)"

        # Append exit code on non-zero
        if proc.returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {proc.returncode}"

        # Prepend CWD indicator when auto-resolved from harness context
        if _cwd_applied:
            output = f"[cwd: {cwd}]\n{output}"

        return output

    async def _arun(
        self,
        command: str,
        timeout: int = 600,
        working_dir: str = "",
        **kwargs: Any,
    ) -> str:
        return await asyncio.to_thread(self._run, command, timeout, working_dir)


# ---------------------------------------------------------------------------
# SetCwdTool — persistent working directory switch
# ---------------------------------------------------------------------------


class SetCwdInput(BaseModel):
    path: str = Field(
        ...,
        description=(
            "Absolute or relative path to set as the new working directory. "
            "Relative paths are resolved against the current CWD. "
            "The directory must exist."
        ),
    )


class SetCwdTool(BaseTool):
    """Change the working directory for all subsequent bash commands in this run.

    Equivalent to 'cd' — persistently updates the session's CWD.
    All future bash commands that don't specify an explicit working_dir
    will run in the new directory.
    """

    name: str = "set_cwd"
    description: str = (
        "Change working directory for this session. All subsequent bash commands "
        "will run in the new directory. Use this instead of 'cd' since each bash "
        "call is a separate subprocess. The path must exist."
    )
    args_schema: type[BaseModel] = SetCwdInput

    def _run(self, path: str, **kwargs: Any) -> str:
        current = _cwd_ctx.get() or os.getcwd()
        resolved = Path(path) if Path(path).is_absolute() else Path(current) / path
        resolved = resolved.resolve()

        if not resolved.is_dir():
            return f"[error] Directory does not exist: {resolved}"

        _cwd_ctx.set(str(resolved))
        return f"[cwd: {resolved}]"

    async def _arun(self, path: str, **kwargs: Any) -> str:
        return await asyncio.to_thread(self._run, path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_builtin_tools() -> list[BaseTool]:
    """Return all built-in RAI tools ready to be added to the agent's tool list."""
    return [BashTool(), SetCwdTool()]
