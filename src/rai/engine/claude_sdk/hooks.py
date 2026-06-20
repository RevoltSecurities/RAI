"""RAI hook factories for claude-agent-sdk.

Hooks replace the middleware stack for lightweight cross-cutting concerns:
- PreToolUse  → rate limiting
- PostToolUse → audit logging
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Per-tool delays in seconds keyed by rate limit profile.
# aggressive = minimal delays, normal = moderate, stealth = longer pauses
_RATE_DELAYS: dict[str, dict[str, float]] = {
    "aggressive": {
        "Bash": 0.1,
        "default": 0.05,
    },
    "normal": {
        "Bash": 0.3,
        "default": 0.1,
    },
    "stealth": {
        "Bash": 1.5,
        "default": 0.5,
    },
}


def make_rate_limit_hook(profile: str = "normal"):
    """Return a PreToolUse HookCallback that applies per-tool delays."""

    delays = _RATE_DELAYS.get(profile, _RATE_DELAYS["normal"])

    async def _rate_limit_hook(input_data: Any, tool_use_id: str | None, context: Any):
        tool_name: str = input_data.get("tool_name", "")
        delay = delays.get(tool_name, delays["default"])
        if delay > 0:
            await asyncio.sleep(delay)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }

    return _rate_limit_hook


def _append_audit_line(log_path: Path, line: str) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        logger.debug("Audit log write failed: %s", exc)


def make_audit_hook(agent_name: str):
    """Return a PostToolUse HookCallback that logs to ~/.rai/audit.log (non-blocking)."""
    from rai.config.settings import settings

    log_path = settings.audit_log_path

    async def _audit_hook(input_data: Any, tool_use_id: str | None, context: Any):
        try:
            record = {
                "ts": time.time(),
                "agent": agent_name,
                "tool": input_data.get("tool_name", ""),
                "input": input_data.get("tool_input", {}),
            }
            line = json.dumps(record, default=str)
            await asyncio.to_thread(_append_audit_line, log_path, line)
        except Exception as exc:
            logger.debug("Audit hook error: %s", exc)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
            }
        }

    return _audit_hook


def make_findings_hook():
    """Return a PostToolUse HookCallback placeholder for future findings enrichment."""

    async def _findings_hook(input_data: Any, tool_use_id: str | None, context: Any):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
            }
        }

    return _findings_hook


def build_default_hooks(
    agent_name: str,
    rate_limit_profile: str = "normal",
) -> dict:
    """Build the default RAI hook dict for ClaudeAgentOptions.hooks."""
    from claude_agent_sdk.types import HookMatcher

    return {
        "PreToolUse": [
            HookMatcher(hooks=[make_rate_limit_hook(rate_limit_profile)]),
        ],
        "PostToolUse": [
            HookMatcher(hooks=[make_audit_hook(agent_name)]),
        ],
    }
