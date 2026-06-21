"""SubagentService — singleton lifecycle manager for async subagents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rai.harness.subagents_v2.models import (
    TERMINAL_STATUSES,
    CompletionNotification,
    _now,
)

logger = logging.getLogger(__name__)


class SubagentService:
    """Singleton that owns all subagent lifecycle state.

    Replaces the scattered process-local dicts (_SUBAGENT_REGISTRY, _TASK_REGISTRY,
    _PENDING_NOTIFICATIONS, etc.) with a single coherent service that both LLM tools
    and FastAPI routes delegate to.
    """

    _instance: SubagentService | None = None

    def __init__(self) -> None:
        # task_id → asyncio.Task running the subagent
        self._active_tasks: dict[str, asyncio.Task] = {}
        # task_id → metadata (status, agent_name, etc.) for quick lookup
        self._task_meta: dict[str, dict[str, Any]] = {}
        # parent_run_id → queue of CompletionNotifications
        self._completion_queues: dict[str, asyncio.Queue[CompletionNotification]] = {}
        # task_id → Future awaiting HITL decision
        self._hitl_futures: dict[str, asyncio.Future[dict]] = {}
        # task_id → RunEventBus (per-subagent SSE)
        self._buses: dict[str, Any] = {}
        # task_id → (compiled_graph, stream_config) for checkpoint resume
        self._graphs: dict[str, tuple[Any, dict]] = {}
        # parent_run_id → list of deferred notifications (while parent at HITL)
        self._deferred: dict[str, list[CompletionNotification]] = {}
        # Infrastructure references (set via initialize())
        self._checkpointer: Any = None
        self._task_store: Any = None

    @classmethod
    def instance(cls) -> SubagentService:
        """Get or create the singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def initialize(
        cls,
        *,
        checkpointer: Any = None,
        task_store: Any = None,
    ) -> SubagentService:
        """Initialize/reset the singleton with infrastructure refs."""
        svc = cls.instance()
        svc._checkpointer = checkpointer
        svc._task_store = task_store
        return svc

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Completion queue management
    # ------------------------------------------------------------------

    def get_or_create_queue(self, parent_run_id: str) -> asyncio.Queue[CompletionNotification]:
        """Get or create the completion queue for a parent run."""
        if parent_run_id not in self._completion_queues:
            self._completion_queues[parent_run_id] = asyncio.Queue()
        return self._completion_queues[parent_run_id]

    async def wait_completion(
        self, parent_run_id: str, timeout: float = 2.0
    ) -> CompletionNotification | None:
        """Wait for a completion notification from any subagent of this run.

        Returns None on timeout (no completions pending).
        """
        q = self._completion_queues.get(parent_run_id)
        if q is None:
            return None
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def push_completion(self, parent_run_id: str, notification: CompletionNotification) -> None:
        """Push a completion notification (called by executor on task finish)."""
        q = self.get_or_create_queue(parent_run_id)
        q.put_nowait(notification)

    # ------------------------------------------------------------------
    # Deferred notifications (when parent is at HITL interrupt)
    # ------------------------------------------------------------------

    def defer_notification(self, parent_run_id: str, notification: CompletionNotification) -> None:
        """Stash a notification for later injection after HITL resolves."""
        self._deferred.setdefault(parent_run_id, []).append(notification)

    def drain_deferred(self, parent_run_id: str) -> list[CompletionNotification]:
        """Pop and return all deferred notifications for a run."""
        return self._deferred.pop(parent_run_id, [])

    # ------------------------------------------------------------------
    # Task lifecycle
    # ------------------------------------------------------------------

    def register_task(
        self,
        task_id: str,
        asyncio_task: asyncio.Task,
        *,
        agent_name: str,
        parent_run_id: str,
        parent_thread_id: str,
        label: str | None = None,
        pipeline_id: str | None = None,
        depends_on: list[str] | None = None,
        input_message: str = "",
        output_file: str = "",
    ) -> None:
        """Register a newly spawned subagent task."""
        self._active_tasks[task_id] = asyncio_task
        self._task_meta[task_id] = {
            "task_id": task_id,
            "agent_name": agent_name,
            "parent_run_id": parent_run_id,
            "parent_thread_id": parent_thread_id,
            "status": "running",
            "label": label,
            "pipeline_id": pipeline_id,
            "depends_on": depends_on,
            "input": input_message,
            "output_file": output_file,
            "created_at": _now(),
        }
        self.get_or_create_queue(parent_run_id)

    def has_active_tasks(self, parent_run_id: str) -> bool:
        """Check if any tasks are still running for this parent run."""
        for meta in self._task_meta.values():
            if (
                meta.get("parent_run_id") == parent_run_id
                and meta.get("status") not in TERMINAL_STATUSES
            ):
                return True
        return False

    def get_task_meta(self, task_id: str) -> dict[str, Any] | None:
        """Get metadata for a task."""
        return self._task_meta.get(task_id)

    def list_tasks_for_run(self, parent_run_id: str) -> list[dict[str, Any]]:
        """List all tasks belonging to a parent run."""
        return [
            m for m in self._task_meta.values()
            if m.get("parent_run_id") == parent_run_id
        ]

    def list_all_tasks(self) -> list[dict[str, Any]]:
        """List all tracked tasks across all runs."""
        return list(self._task_meta.values())

    def resolve_hitl(self, task_id: str, decision: dict) -> None:
        """Resolve a pending HITL future for a task."""
        fut = self._hitl_futures.get(task_id)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    def update_task_status(self, task_id: str, status: str, output_preview: str = "") -> None:
        """Update a task's status (called by executor)."""
        meta = self._task_meta.get(task_id)
        if meta:
            meta["status"] = status
            if output_preview:
                meta["output_preview"] = output_preview

    # ------------------------------------------------------------------
    # HITL
    # ------------------------------------------------------------------

    def create_hitl_future(self, task_id: str) -> asyncio.Future[dict]:
        """Create a future for the subagent to await HITL decision."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        self._hitl_futures[task_id] = fut
        return fut

    async def submit_hitl_decision(self, task_id: str, decision: dict) -> bool:
        """Resolve a pending HITL future. Returns True if resolved."""
        fut = self._hitl_futures.pop(task_id, None)
        if fut and not fut.done():
            fut.set_result(decision)
            return True
        return False

    def has_pending_hitl(self, task_id: str) -> bool:
        """Check if a task has a pending HITL interrupt."""
        fut = self._hitl_futures.get(task_id)
        return fut is not None and not fut.done()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task. Returns True if cancelled."""
        asyncio_task = self._active_tasks.get(task_id)
        if asyncio_task and not asyncio_task.done():
            asyncio_task.cancel()
            self.update_task_status(task_id, "cancelled")
            # Also resolve any pending HITL future so it doesn't hang
            fut = self._hitl_futures.pop(task_id, None)
            if fut and not fut.done():
                fut.cancel()
            return True
        return False

    # ------------------------------------------------------------------
    # Graph/Bus management
    # ------------------------------------------------------------------

    def register_graph(self, task_id: str, graph: Any, config: dict) -> None:
        """Store compiled graph + config for checkpoint resume."""
        self._graphs[task_id] = (graph, config)

    def get_graph(self, task_id: str) -> tuple[Any, dict] | None:
        """Get stored graph and config for a task."""
        return self._graphs.get(task_id)

    def register_bus(self, task_id: str, bus: Any) -> None:
        """Store a per-task RunEventBus."""
        self._buses[task_id] = bus

    def get_bus(self, task_id: str) -> Any | None:
        """Get the SSE bus for a task."""
        return self._buses.get(task_id)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_task(self, task_id: str) -> None:
        """Remove ephemeral state for a completed task (keep meta for queries)."""
        self._active_tasks.pop(task_id, None)
        self._hitl_futures.pop(task_id, None)
        self._buses.pop(task_id, None)
        self._graphs.pop(task_id, None)

    def set_active_task(self, task_id: str, asyncio_task: "asyncio.Task[Any]") -> None:
        """Replace the asyncio.Task for an already-registered task (SubagentUpdate resume path)."""
        self._active_tasks[task_id] = asyncio_task

    def cleanup_run(self, parent_run_id: str) -> None:
        """Cleanup all state for a completed parent run."""
        self._completion_queues.pop(parent_run_id, None)
        self._deferred.pop(parent_run_id, None)
