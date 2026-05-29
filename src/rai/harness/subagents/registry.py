"""Process-local registries and context variable for the HTTP subagent harness."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from rai.harness.sse import RunEventBus


# ── SubagentMeta ─────────────────────────────────────────────────────────────

class SubagentMeta(TypedDict, total=False):
    task_id: str
    agent_name: str
    parent_run_id: str
    parent_thread_id: str
    status: str          # running | interrupted | completed | failed | cancelled | timeout
    created_at: str
    input: str
    output: str | None
    output_file: str
    label: str | None
    pipeline_id: str | None
    depends_on: list[str] | None


# ── RunContext ────────────────────────────────────────────────────────────────

class RunContext(TypedDict):
    """Injected by execute_run() before _stream_once(); read by tools at call time."""
    run_id: str
    thread_id: str
    parent_bus: Any   # RunEventBus — avoid circular import at module level
    checkpointer: Any
    parent_api_key: str    # fallback for subagents that have no own api_key
    parent_base_url: str   # fallback for subagents that have no own base_url
    agent_name: str        # parent agent's own name — used by list_available_agents


# ── Process-local state ───────────────────────────────────────────────────────

_SUBAGENT_REGISTRY: dict[str, SubagentMeta] = {}         # task_id → metadata
_SUBAGENT_BUSES:    dict[str, Any] = {}                  # task_id → RunEventBus
_SUBAGENT_TASKS:    dict[str, asyncio.Task] = {}         # task_id → asyncio.Task
_SUBAGENT_OUTPUTS:  dict[str, asyncio.Queue[str]] = {}   # task_id → output queue
_SUBAGENT_HITL:     dict[str, asyncio.Future[dict]] = {} # task_id → HITL approval future
_SUBAGENT_GRAPHS:   dict[str, tuple[Any, dict]] = {}     # task_id → (compiled_graph, stream_config)
_SUBAGENT_LOCK = threading.Lock()

# Set by app.py lifespan via set_task_store(); read by executor.py for DB persistence.
_TASK_STORE: Any = None


def set_task_store(store: Any) -> None:
    """Wire the global TaskStore reference used by executor.py."""
    global _TASK_STORE  # noqa: PLW0603
    _TASK_STORE = store


# ── Context variable ──────────────────────────────────────────────────────────

# Set by execute_run() via contextvars.ContextVar.set() before calling _stream_once().
# Each asyncio.Task inherits a copy → concurrent runs on different threads are isolated.
_RUN_CONTEXT: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "_RUN_CONTEXT", default=None
)
