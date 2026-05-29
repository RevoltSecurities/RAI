"""Typed wrappers over background.py process-local registries for route handlers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from rai.agents.background import (
    _NOTIF_LOCK,
    _PENDING_NOTIFICATIONS,
    _PIPELINE_GROUPS,
    _TASK_AGENT_NAMES,
    _TASK_REGISTRY,
    _TASK_RUNNABLES,
    _TERMINAL_STATUSES,
    LocalAsyncTask,
)

logger = logging.getLogger(__name__)


def get_all_live_tasks() -> dict[str, dict[str, str]]:
    """Return a snapshot of all currently running tasks (under lock)."""
    with _NOTIF_LOCK:
        return {tid: {"task_id": tid, "agent_name": name, "status": "running"}
                for tid, name in _TASK_AGENT_NAMES.items()}


def get_pending_notifs_snapshot() -> dict[str, dict[str, str]]:
    """Return a non-destructive snapshot of _PENDING_NOTIFICATIONS (under lock)."""
    with _NOTIF_LOCK:
        return dict(_PENDING_NOTIFICATIONS)


def get_tasks_for_thread(thread_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract task list from a LangGraph state snapshot dict."""
    local_tasks: dict[str, LocalAsyncTask] = thread_snapshot.get("local_async_tasks") or {}
    if not local_tasks:
        return []

    live_ids: set[str]
    with _NOTIF_LOCK:
        live_ids = set(_TASK_AGENT_NAMES.keys())

    results = []
    for task_id, task in local_tasks.items():
        entry = dict(task)
        # Overlay live status if the task is still in the registry
        if task_id in live_ids and entry.get("status") == "running":
            entry["status"] = "running"
        # Try to load output from file for terminal tasks
        if entry.get("status") in _TERMINAL_STATUSES:
            output_file = entry.get("output_file", "")
            if output_file and Path(output_file).exists():
                try:
                    data = json.loads(Path(output_file).read_text())
                    entry["output"] = data.get("output")
                except (json.JSONDecodeError, OSError):
                    pass
        results.append(entry)
    return results


def build_pipeline_batches(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct pipeline DAG batch topology from task depends_on fields."""
    if not tasks:
        return []

    # Group by pipeline_id
    pipeline_tasks = [t for t in tasks if t.get("pipeline_id")]
    if not pipeline_tasks:
        return []

    label_to_task: dict[str, dict[str, Any]] = {
        t["label"]: t for t in pipeline_tasks if t.get("label")
    }

    # Topological sort into batches
    completed_labels: set[str] = set()
    remaining = list(pipeline_tasks)
    batches: list[dict[str, Any]] = []
    batch_num = 1

    while remaining:
        # Tasks whose dependencies are all in completed_labels
        ready = [
            t for t in remaining
            if all(dep in completed_labels for dep in (t.get("depends_on") or []))
        ]
        if not ready:
            # Cycle or unresolved — dump rest in one batch
            ready = remaining

        labels = [t.get("label", t["task_id"]) for t in ready]
        statuses = [t.get("status", "unknown") for t in ready]

        if all(s in _TERMINAL_STATUSES for s in statuses):
            batch_status = "completed" if all(s == "success" for s in statuses) else "failed"
        elif any(s == "running" for s in statuses):
            batch_status = "running"
        else:
            batch_status = "pending"

        depends: list[str] = []
        for t in ready:
            depends.extend(t.get("depends_on") or [])

        batches.append({
            "batch_num": batch_num,
            "labels": labels,
            "status": batch_status,
            "depends_on": list(dict.fromkeys(depends)),
        })

        completed_labels.update(labels)
        remaining = [t for t in remaining if t not in ready]
        batch_num += 1

    return batches


async def get_task_live_progress(task_id: str) -> str | None:
    """Return live progress for a running task via _TASK_RUNNABLES aget_state."""
    entry = _TASK_RUNNABLES.get(task_id)
    if not entry:
        return None
    runnable, _ = entry
    try:
        from rai.sessions.store import build_stream_config
        config = build_stream_config(task_id, "subagent", cwd=".")
        snapshot = await runnable.aget_state(config)
        msgs = snapshot.values.get("messages", [])
        recent = msgs[-6:] if len(msgs) > 6 else msgs
        lines = []
        for m in recent:
            role = getattr(m, "type", "?")
            content = getattr(m, "content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in content
                )
            lines.append(f"[{role}] {str(content)[:200]}")
        next_nodes = list(snapshot.next) if snapshot.next else []
        return "\n".join(lines) + (f"\nnext: {next_nodes}" if next_nodes else "")
    except Exception as exc:
        logger.debug("get_task_live_progress failed for %s: %s", task_id, exc)
        return None


def get_pipeline_status(tasks: list[dict[str, Any]], pipeline_id: str) -> str:
    """Derive overall pipeline status from its tasks."""
    pipeline_tasks = [t for t in tasks if t.get("pipeline_id") == pipeline_id]
    if not pipeline_tasks:
        return "unknown"
    statuses = [t.get("status", "unknown") for t in pipeline_tasks]
    if any(s == "error" for s in statuses):
        return "failed"
    if any(s == "running" for s in statuses):
        return "running"
    if all(s in _TERMINAL_STATUSES for s in statuses):
        return "completed"
    if any(s == "pending" for s in statuses):
        return "pending"
    return "unknown"
