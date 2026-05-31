"""RAI hook system — identical to Claude Code hooks for full compatibility.

Config is loaded from two locations and merged (both are optional):
  1. ~/.claude/settings.json   — Claude Code settings (hooks key); read-only
  2. ~/.rai/hooks.json         — RAI-native hooks, same format, higher precedence

Config format (Claude Code compatible):

  {
    "hooks": {
      "PreToolUse": [
        {
          "matcher": "bash",
          "hooks": [{"type": "command", "command": "my-audit.sh"}]
        }
      ],
      "PostToolUse": [
        {
          "matcher": ".*",
          "hooks": [{"type": "command", "command": "log-result.sh"}]
        }
      ],
      "Stop": [
        {
          "hooks": [{"type": "command", "command": "on-stop.sh"}]
        }
      ],
      "PreModelCall":  [...],   // RAI extension — not in Claude Code
      "PostModelCall": [...]    // RAI extension — not in Claude Code
    }
  }

Hook command field:
  - String  → run via ``bash -c "<command>"`` (Claude Code native format)
  - List    → run directly as argv (deepagents-cli compat)

Stdin payload (byte-for-byte identical to Claude Code):

  PreToolUse:
    {"session_id":"…","hook_event_name":"PreToolUse",
     "tool_name":"bash","tool_input":{…}}

  PostToolUse:
    {"session_id":"…","hook_event_name":"PostToolUse",
     "tool_name":"bash","tool_input":{…},
     "tool_response":"…","tool_response_freeform_data":"…"}

  Stop:
    {"session_id":"…","hook_event_name":"Stop","stop_hook_active":false}

  PreModelCall / PostModelCall (RAI extension):
    {"session_id":"…","hook_event_name":"PreModelCall"}

Exit-code contract (PreToolUse only — identical to Claude Code):
  0           → allow the tool to run
  non-zero    → block the tool; hook stdout becomes the reason shown to the agent

  The hook may write JSON to stdout for structured responses:
    {"decision": "block",   "reason": "Command not in allow-list"}
    {"decision": "approve"}
  Plain-text stdout on non-zero exit is also accepted as the block reason.

  On timeout (>30 s) or spawn error the tool is allowed (fail-open) and a
  warning is logged — hooks must never silently break the agent.

PostToolUse / Stop / PreModelCall / PostModelCall are fire-and-forget;
their exit code and stdout are ignored (errors logged only).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event names
# ---------------------------------------------------------------------------

PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
STOP = "Stop"
PRE_MODEL_CALL = "PreModelCall"    # RAI extension
POST_MODEL_CALL = "PostModelCall"  # RAI extension

# All recognised event names — used for validation in get_hooks_for_event
ALL_EVENTS = {PRE_TOOL_USE, POST_TOOL_USE, STOP, PRE_MODEL_CALL, POST_MODEL_CALL}

# Timeout in seconds for blocking hooks (PreToolUse).  Fail-open on timeout.
_PRE_HOOK_TIMEOUT = 30
# Timeout for background (fire-and-forget) hooks.
_BG_HOOK_TIMEOUT = 10

# ---------------------------------------------------------------------------
# Session ID — stable for the lifetime of this process
# ---------------------------------------------------------------------------

_SESSION_ID: str = str(uuid.uuid4())


def get_session_id() -> str:
    return _SESSION_ID


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_config_cache: dict[str, list[dict[str, Any]]] | None = None


def _read_hooks_from_file(path: Path | str) -> dict[str, list[dict[str, Any]]]:
    """Read ``hooks`` dict from a JSON settings/hooks file.  Returns {} on any error."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        if not isinstance(hooks, dict):
            logger.warning("hooks.py: 'hooks' in %s must be a dict, got %s", path, type(hooks).__name__)
            return {}
        return hooks  # type: ignore[return-value]
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("hooks.py: could not read %s: %s", path, exc)
        return {}


def _load_config() -> dict[str, list[dict[str, Any]]]:
    """Load and merge hook config from Claude Code settings + RAI hooks file.

    Precedence (later wins for the same event):
      1. ~/.claude/settings.json  (Claude Code — read-only compat)
      2. ~/.rai/hooks.json        (RAI-native — higher precedence)
    """
    global _config_cache  # noqa: PLW0603
    if _config_cache is not None:
        return _config_cache

    merged: dict[str, list[dict[str, Any]]] = {}

    # 1. Claude Code settings.json
    cc_path = Path.home() / ".claude" / "settings.json"
    for event, entries in _read_hooks_from_file(cc_path).items():
        if isinstance(entries, list):
            merged.setdefault(event, []).extend(entries)

    # 2. RAI-native hooks.json — entries are appended (RAI hooks run after CC hooks)
    rai_path = Path.home() / ".rai" / "hooks.json"
    for event, entries in _read_hooks_from_file(rai_path).items():
        if isinstance(entries, list):
            merged.setdefault(event, []).extend(entries)

    _config_cache = merged
    return _config_cache


def reload_hooks_config() -> None:
    """Force reload of hook config on next dispatch (call after editing hooks.json)."""
    global _config_cache  # noqa: PLW0603
    _config_cache = None


def get_hooks_for_event(event: str) -> list[dict[str, Any]]:
    """Return the list of hook entry dicts for *event*, or [] if none configured."""
    return _load_config().get(event, [])


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------


def _matches(matcher: str | None, tool_name: str) -> bool:
    """Return True if *tool_name* matches the hook entry's *matcher* pattern.

    - None / empty  → matches everything (Claude Code behaviour)
    - Exact string  → case-insensitive equality check first (fast path)
    - Otherwise     → treated as a regex (re.search, case-insensitive)
    """
    if not matcher:
        return True
    if matcher.lower() == tool_name.lower():
        return True
    try:
        return bool(re.search(matcher, tool_name, re.IGNORECASE))
    except re.error:
        return matcher.lower() == tool_name.lower()


# ---------------------------------------------------------------------------
# Command resolution
# ---------------------------------------------------------------------------


def _resolve_argv(command: str | list[str]) -> list[str]:
    """Return an argv list from a hook command value.

    - list  → used as-is (deepagents-cli compat)
    - str   → run via ``bash -c`` on Unix, ``cmd /c`` on Windows (Claude Code native)
    """
    if isinstance(command, list):
        return command
    if sys.platform == "win32":
        return ["cmd", "/c", command]
    return ["bash", "-c", command]


# ---------------------------------------------------------------------------
# Hook result
# ---------------------------------------------------------------------------


@dataclass
class HookDecision:
    """Result of a PreToolUse hook evaluation."""
    blocked: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# Single-hook execution
# ---------------------------------------------------------------------------


def _run_hook(argv: list[str], payload: bytes, *, timeout: int) -> tuple[int, str]:
    """Run one hook command synchronously.

    Returns (exit_code, stdout_text).  On timeout or spawn error returns
    (0, "") so the tool is allowed (fail-open).
    """
    try:
        proc = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout = proc.stdout.decode("utf-8", errors="replace").strip()
        return proc.returncode, stdout
    except subprocess.TimeoutExpired:
        logger.warning("hooks.py: hook timed out after %ds: %s", timeout, argv)
        return 0, ""
    except (FileNotFoundError, PermissionError) as exc:
        logger.warning("hooks.py: hook spawn failed: %s — %s", argv, exc)
        return 0, ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("hooks.py: hook error: %s — %s", argv, exc)
        return 0, ""


def _parse_decision(exit_code: int, stdout: str) -> HookDecision:
    """Interpret hook exit code + stdout into a HookDecision.

    Structured JSON stdout (Claude Code format):
      {"decision": "block",   "reason": "..."}  → blocked regardless of exit code
      {"decision": "approve"}                    → approved regardless of exit code
    Plain text / no JSON → exit code determines block (non-zero = block).
    """
    if stdout:
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                decision = data.get("decision", "")
                if decision == "approve":
                    return HookDecision(blocked=False)
                if decision == "block":
                    return HookDecision(blocked=True, reason=data.get("reason", ""))
        except json.JSONDecodeError:
            pass  # plain text — fall through to exit code logic

    if exit_code != 0:
        return HookDecision(blocked=True, reason=stdout or f"Hook exited {exit_code}")
    return HookDecision(blocked=False)


# ---------------------------------------------------------------------------
# Public dispatch API
# ---------------------------------------------------------------------------


def fire_pre_tool_use(tool_name: str, tool_input: dict[str, Any]) -> HookDecision:
    """Run all matching PreToolUse hooks synchronously.

    Returns the first blocking decision encountered, or an allow decision if
    all hooks pass.  On timeout or spawn error the hook is skipped (fail-open).
    """
    entries = get_hooks_for_event(PRE_TOOL_USE)
    if not entries:
        return HookDecision(blocked=False)

    payload = json.dumps({
        "session_id": _SESSION_ID,
        "hook_event_name": PRE_TOOL_USE,
        "tool_name": tool_name,
        "tool_input": tool_input,
    }).encode()

    for entry in entries:
        matcher = entry.get("matcher")
        if not _matches(matcher, tool_name):
            continue
        for hook in entry.get("hooks", []):
            if hook.get("type", "command") != "command":
                continue
            raw_cmd = hook.get("command")
            if not raw_cmd:
                continue
            argv = _resolve_argv(raw_cmd)
            exit_code, stdout = _run_hook(argv, payload, timeout=_PRE_HOOK_TIMEOUT)
            decision = _parse_decision(exit_code, stdout)
            if decision.blocked:
                logger.info("hooks.py: PreToolUse blocked '%s': %s", tool_name, decision.reason)
                return decision

    return HookDecision(blocked=False)


async def afire_pre_tool_use(tool_name: str, tool_input: dict[str, Any]) -> HookDecision:
    """Async wrapper — runs fire_pre_tool_use in a thread."""
    return await asyncio.to_thread(fire_pre_tool_use, tool_name, tool_input)


def _fire_bg_event(event: str, payload: dict[str, Any], tool_name: str = "") -> None:
    """Fire all hooks for *event* synchronously (intended to be called in a thread)."""
    entries = get_hooks_for_event(event)
    if not entries:
        return

    payload_bytes = json.dumps({"session_id": _SESSION_ID, "hook_event_name": event, **payload}).encode()

    for entry in entries:
        if tool_name:
            matcher = entry.get("matcher")
            if not _matches(matcher, tool_name):
                continue
        for hook in entry.get("hooks", []):
            if hook.get("type", "command") != "command":
                continue
            raw_cmd = hook.get("command")
            if not raw_cmd:
                continue
            _run_hook(_resolve_argv(raw_cmd), payload_bytes, timeout=_BG_HOOK_TIMEOUT)


def fire_post_tool_use_bg(
    tool_name: str,
    tool_input: dict[str, Any],
    tool_response: str,
) -> None:
    """Fire PostToolUse hooks in the background (fire-and-forget, never blocks)."""
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": tool_response,
        "tool_response_freeform_data": tool_response,
    }
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            asyncio.to_thread(_fire_bg_event, POST_TOOL_USE, payload, tool_name)
        )
        # Keep a strong reference so GC doesn't collect the task
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except RuntimeError:
        # No running loop — run synchronously (e.g. during tests)
        _fire_bg_event(POST_TOOL_USE, payload, tool_name)


def fire_stop_bg(stop_hook_active: bool = False) -> None:
    """Fire Stop hooks in the background."""
    payload = {"stop_hook_active": stop_hook_active}
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            asyncio.to_thread(_fire_bg_event, STOP, payload)
        )
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except RuntimeError:
        _fire_bg_event(STOP, payload)


def fire_model_event_bg(event: str) -> None:
    """Fire PreModelCall or PostModelCall hooks in the background (RAI extension)."""
    if event not in (PRE_MODEL_CALL, POST_MODEL_CALL):
        return
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            asyncio.to_thread(_fire_bg_event, event, {})
        )
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except RuntimeError:
        _fire_bg_event(event, {})


# Strong references to background tasks to prevent GC
_bg_tasks: set[asyncio.Task[Any]] = set()
