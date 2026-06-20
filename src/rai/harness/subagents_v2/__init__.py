"""Async subagent system v2 — clean HTTP-based architecture.

Replaces the process-local shared state + watcher loop pattern with:
- SubagentService singleton for lifecycle management
- asyncio.Queue for completion notifications
- Command(update={"async_tasks": {...}}) for graph state persistence
- <task-notification> injection pattern (like Claude Code)
"""

from rai.harness.subagents_v2.middleware import (
    AsyncRaiSubagentsMiddleware,
    build_subagent_system_section,
)
from rai.harness.subagents_v2.models import (
    AsyncSubAgentState,
    AsyncTask,
    CompletionNotification,
)
from rai.harness.subagents_v2.service import SubagentService
from rai.harness.subagents_v2.tools import build_subagent_tools

__all__ = [
    "AsyncRaiSubagentsMiddleware",
    "AsyncSubAgentState",
    "AsyncTask",
    "CompletionNotification",
    "SubagentService",
    "build_subagent_system_section",
    "build_subagent_tools",
]
