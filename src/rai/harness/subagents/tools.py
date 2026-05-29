"""LangChain @tool functions injected into HTTP-mode parent agents.

Replaces the deepagents start_agent_task / start_pipeline family with
full-capability HTTP versions that:
  - Use the same tool names as deepagents so the LLM needs no relearning
  - Stream tokens per-subagent to /subagents/{id}/stream
  - Fan subagent events to the parent run's SSE bus
  - Support per-subagent HITL via POST /subagents/{id}/interrupt
  - Auto-notify parent on completion (via _PENDING_NOTIFICATIONS)
  - Accept label OR task_id for all single-agent operations
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

from langchain_core.tools import tool

from rai.harness.subagents.executor import launch_subagent, launch_subagent_resume
from rai.harness.subagents.registry import (
    _RUN_CONTEXT,
    _SUBAGENT_GRAPHS,
    _SUBAGENT_LOCK,
    _SUBAGENT_OUTPUTS,
    _SUBAGENT_REGISTRY,
    _SUBAGENT_TASKS,
)

logger = logging.getLogger(__name__)


def _get_run_context() -> dict:
    ctx = _RUN_CONTEXT.get()
    if ctx is None:
        raise RuntimeError(
            "start_agent_task / run_agent_sync / start_pipeline called "
            "outside an HTTP run context. These tools only work when the agent is "
            "served via `rai http serve`."
        )
    return ctx


def _get_available_agents(parent_name: str) -> dict[str, str]:
    """Return {name: description} for all configured subagents of parent_name."""
    from rai.agents.loader import load_subagents_for
    try:
        return {sa["name"]: sa.get("description", "") for sa in load_subagents_for(parent_name)}
    except Exception:
        return {}


def _check_agent_name(agent: str, ctx: dict) -> str | None:
    """Return an error string if agent name is unknown, else None.

    'rai' is always valid (base agent fallback). Any other name is checked
    against the subagents configured for the parent agent.
    """
    if not agent or agent == "rai":
        return None
    parent = ctx.get("agent_name", "rai")
    available = _get_available_agents(parent)
    if agent not in available:
        names = ", ".join(sorted(available)) if available else "none configured"
        return (
            f"Unknown agent '{agent}'. Available: {names}. "
            f"Call list_available_agents() to see names and descriptions."
        )
    return None


def _assign_label(requested: str, run_id: str) -> str:
    """Return a unique label for this run, auto-suffixing on collision.

    'coder' already taken → 'coder-2'; 'coder-2' taken → 'coder-3', etc.
    Must be called while holding _SUBAGENT_LOCK.
    """
    existing = {m.get("label") for m in _SUBAGENT_REGISTRY.values() if m.get("parent_run_id") == run_id}
    if requested not in existing:
        return requested
    n = 2
    while True:
        candidate = f"{requested}-{n}"
        if candidate not in existing:
            return candidate
        n += 1


def _resolve_task(label_or_id: str, run_id: str) -> "tuple[str, dict] | tuple[None, str]":
    """Find a subagent by task_id OR label, scoped to run_id.

    Returns (task_id, meta) on success, (None, error_message) on failure.
    When multiple agents share the same label an error is returned listing
    all matches so the caller can retry with an unambiguous task_id.
    """
    label_or_id = label_or_id.strip()
    with _SUBAGENT_LOCK:
        # Direct task_id match — always unambiguous
        meta = _SUBAGENT_REGISTRY.get(label_or_id)
        if meta is not None:
            return label_or_id, meta

        # Collect all scoped matches (same run_id)
        scoped = [(tid, m) for tid, m in _SUBAGENT_REGISTRY.items()
                  if m.get("label") == label_or_id and m.get("parent_run_id") == run_id]
        if len(scoped) == 1:
            return scoped[0]
        if len(scoped) > 1:
            matches = [{"task_id": tid, "label": m.get("label"), "status": m.get("status"), "created_at": m.get("created_at", "")}
                       for tid, m in scoped]
            matches.sort(key=lambda x: x["created_at"])
            ids = ", ".join(f"'{x['task_id'][:12]}' ({x['status']})" for x in matches)
            return None, (
                f"Label '{label_or_id}' matches {len(scoped)} agents in this run: {ids}. "
                f"Use the task_id directly to target a specific one."
            )

        # Unscoped fallback — cross-run label match
        unscoped = [(tid, m) for tid, m in _SUBAGENT_REGISTRY.items()
                    if m.get("label") == label_or_id]
        if len(unscoped) == 1:
            return unscoped[0]
        if len(unscoped) > 1:
            matches = [{"task_id": tid, "label": m.get("label"), "status": m.get("status"), "created_at": m.get("created_at", "")}
                       for tid, m in unscoped]
            matches.sort(key=lambda x: x["created_at"])
            ids = ", ".join(f"'{x['task_id'][:12]}' ({x['status']})" for x in matches)
            return None, (
                f"Label '{label_or_id}' matches {len(unscoped)} agents across runs: {ids}. "
                f"Use the task_id directly to target a specific one."
            )

    return None, f"No subagent found with task_id or label '{label_or_id}'"


# ── Topological batch planner ─────────────────────────────────────────────────

def _plan_batches(tasks: list[dict]) -> list[list[dict]]:
    """Group tasks into ordered batches using depends_on labels.

    Each batch can run in parallel; later batches wait for earlier ones.
    Tasks with no depends_on are in batch 0.
    """
    label_map = {t["label"]: t for t in tasks if t.get("label")}
    completed: set[str] = set()
    remaining = list(tasks)
    batches: list[list[dict]] = []

    while remaining:
        ready = []
        blocked = []
        for t in remaining:
            deps = t.get("depends_on") or []
            if all(d in completed for d in deps):
                ready.append(t)
            else:
                blocked.append(t)

        if not ready:
            # Circular dependency or missing label — add all remaining as one batch
            logger.warning("Pipeline: circular dependency or unresolved labels, running rest in one batch")
            batches.append(blocked)
            break

        batches.append(ready)
        for t in ready:
            if t.get("label"):
                completed.add(t["label"])
        remaining = blocked

    return batches


# ── Pipeline orchestrator ─────────────────────────────────────────────────────

async def _run_pipeline(
    pipeline_id: str,
    batches: list[list[dict]],
    parent_run_id: str,
    parent_thread_id: str,
    parent_bus: Any,
    checkpointer: Any,
    parent_api_key: str = "",
    parent_base_url: str = "",
) -> None:
    """Run pipeline batches in order. Skips remaining batches if any task fails."""
    from rai.harness.subagents.registry import _SUBAGENT_OUTPUTS
    try:
        total = sum(len(b) for b in batches)
        succeeded = 0
        failed = 0
        skipped = 0
        aborted = False

        for batch_num, batch in enumerate(batches, 1):
            if aborted:
                # Mark all tasks in remaining batches as skipped
                for task_spec in batch:
                    task_id = uuid4().hex
                    with _SUBAGENT_LOCK:
                        _SUBAGENT_REGISTRY[task_id] = {
                            "task_id": task_id,
                            "agent_name": task_spec.get("agent", "unknown"),
                            "parent_run_id": parent_run_id,
                            "parent_thread_id": parent_thread_id,
                            "status": "skipped",
                            "input": task_spec.get("prompt", ""),
                            "label": task_spec.get("label"),
                            "pipeline_id": pipeline_id,
                        }
                    skipped += 1
                continue

            # Spawn all tasks in this batch
            batch_task_ids: list[str] = []
            for task_spec in batch:
                task_id = uuid4().hex

                launch_subagent(
                    task_id=task_id,
                    agent_name=task_spec.get("agent", "rai"),
                    model=task_spec.get("model", ""),
                    input_message=task_spec.get("prompt", ""),
                    parent_run_id=parent_run_id,
                    parent_thread_id=parent_thread_id,
                    parent_bus=parent_bus,
                    checkpointer=checkpointer,
                    system_prompt=task_spec.get("system_prompt"),
                    label=task_spec.get("label"),
                    pipeline_id=pipeline_id,
                    depends_on=task_spec.get("depends_on"),
                    parent_api_key=parent_api_key,
                    parent_base_url=parent_base_url,
                )
                batch_task_ids.append(task_id)

            await parent_bus.publish("pipeline_batch_started", {
                "pipeline_id": pipeline_id,
                "batch_num": batch_num,
                "task_ids": batch_task_ids,
                "labels": [t.get("label") for t in batch if t.get("label")],
            })

            # Wait for all batch tasks to finish (output queues receive result on completion)
            results = await asyncio.gather(
                *[
                    asyncio.wait_for(
                        _SUBAGENT_OUTPUTS[tid].get(),
                        timeout=3600.0,
                    )
                    for tid in batch_task_ids
                ],
                return_exceptions=True,
            )

            batch_failed = False
            for tid, result in zip(batch_task_ids, results):
                meta = _SUBAGENT_REGISTRY.get(tid, {})
                final_status = meta.get("status", "unknown")
                if isinstance(result, Exception) or final_status in ("failed", "cancelled", "timeout"):
                    failed += 1
                    batch_failed = True
                else:
                    succeeded += 1

            completed_labels = [t.get("label") for t in batch if t.get("label")]
            await parent_bus.publish("pipeline_batch", {
                "pipeline_id": pipeline_id,
                "batch_num": batch_num,
                "completed": [tid for tid in batch_task_ids],
                "failed": failed,
                "labels": completed_labels,
            })

            if batch_failed:
                aborted = True

        pipeline_status = "failed" if failed > 0 else "completed"
        await parent_bus.publish("pipeline_end", {
            "pipeline_id": pipeline_id,
            "status": pipeline_status,
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
        })
    except Exception as exc:
        logger.exception("Pipeline %s failed: %s", pipeline_id, exc)
        await parent_bus.publish("pipeline_error", {
            "pipeline_id": pipeline_id,
            "message": str(exc),
        })


# ── Tools ─────────────────────────────────────────────────────────────────────

@tool
async def start_agent_task(
    prompt: str,
    agent: str = "rai",
    label: str = "",
    system_prompt: str = "",
    model: str = "",
) -> str:
    """Spawn a subagent in the background and return immediately.

    Use this when you want to run a task concurrently without blocking your
    current work (like Claude's background agent spawn with Ctrl+B).

    The subagent streams its own events to /subagents/{task_id}/stream.
    You will be automatically notified when it finishes — no polling needed.

    Args:
        prompt: Task description for the subagent.
        agent: Agent name (must be registered on this server, or use 'rai').
        label: Optional human-readable label for the task.
        system_prompt: Optional system prompt override for the subagent.
        model: Optional model override (e.g. 'anthropic:claude-sonnet-4-6').

    Returns:
        JSON with task_id and stream_url.
    """
    ctx = _get_run_context()

    err = _check_agent_name(agent, ctx)
    if err:
        return json.dumps({"error": err})

    task_id = uuid4().hex

    # Auto-suffix label on collision so each agent in this run has a unique label
    if label:
        with _SUBAGENT_LOCK:
            assigned_label: str | None = _assign_label(label, ctx["run_id"])
    else:
        assigned_label = None

    launch_subagent(
        task_id=task_id,
        agent_name=agent,
        model=model,
        input_message=prompt,
        parent_run_id=ctx["run_id"],
        parent_thread_id=ctx["thread_id"],
        parent_bus=ctx["parent_bus"],
        checkpointer=ctx["checkpointer"],
        system_prompt=system_prompt or None,
        label=assigned_label,
        parent_api_key=ctx.get("parent_api_key", ""),
        parent_base_url=ctx.get("parent_base_url", ""),
    )

    ref = assigned_label or task_id
    result: dict = {
        "task_id": task_id,
        "status": "running",
        "agent": agent,
        "label": assigned_label,
        "hint": f"Use check_agent_task('{ref}') to poll status.",
        "stream_url": f"/subagents/{task_id}/stream",
        "interrupt_url": f"/subagents/{task_id}/interrupt",
    }
    if assigned_label and assigned_label != label:
        result["label_note"] = f"Label '{label}' was already in use — assigned '{assigned_label}' instead."
    return json.dumps(result)


@tool
async def run_agent_sync(
    prompt: str,
    agent: str = "rai",
    timeout: int = 300,
    system_prompt: str = "",
    model: str = "",
) -> str:
    """Run a subagent synchronously — blocks until the subagent finishes.

    Use this when you need the subagent's output before continuing.
    The subagent still streams its events to /subagents/{task_id}/stream
    and HITL approvals are still available via /subagents/{task_id}/interrupt.

    Args:
        prompt: Task description for the subagent.
        agent: Agent name (must be registered, or use 'rai').
        timeout: Maximum seconds to wait (default 300).
        system_prompt: Optional system prompt override.
        model: Optional model override.

    Returns:
        The subagent's final output text.
    """
    ctx = _get_run_context()

    err = _check_agent_name(agent, ctx)
    if err:
        return err

    task_id = uuid4().hex

    launch_subagent(
        task_id=task_id,
        agent_name=agent,
        model=model,
        input_message=prompt,
        parent_run_id=ctx["run_id"],
        parent_thread_id=ctx["thread_id"],
        parent_bus=ctx["parent_bus"],
        checkpointer=ctx["checkpointer"],
        system_prompt=system_prompt or None,
        parent_api_key=ctx.get("parent_api_key", ""),
        parent_base_url=ctx.get("parent_base_url", ""),
    )

    with _SUBAGENT_LOCK:
        out_q = _SUBAGENT_OUTPUTS.get(task_id)
    if out_q is None:
        return "(subagent output queue unavailable)"

    try:
        output = await asyncio.wait_for(out_q.get(), timeout=float(timeout))
        return output or "(subagent produced no output)"
    except asyncio.TimeoutError:
        return f"(subagent {task_id[:12]} timed out after {timeout}s)"


@tool
async def start_pipeline(
    tasks: list[dict],
    pipeline_id: str = "",
) -> str:
    """Launch a DAG pipeline of subagents with dependency ordering.

    Tasks with no depends_on run in the first batch. Subsequent batches
    run after their dependencies complete. Failed tasks abort later batches.

    Args:
        tasks: List of task specs. Each spec:
            {
              "agent": "rai",           # agent name
              "prompt": "do X",          # task input
              "label": "recon",          # unique label for depends_on references
              "depends_on": ["recon"],   # labels that must complete first (optional)
              "system_prompt": "...",    # optional system prompt override
              "model": "..."             # optional model override
            }
        pipeline_id: Optional pipeline identifier. Auto-generated if empty.

    Returns:
        JSON with pipeline_id, total task count, and batch topology.
    """
    ctx = _get_run_context()
    pid = pipeline_id or uuid4().hex
    batches = _plan_batches(tasks)

    # Fire pipeline orchestrator as background task
    asyncio.create_task(
        _run_pipeline(
            pipeline_id=pid,
            batches=batches,
            parent_run_id=ctx["run_id"],
            parent_thread_id=ctx["thread_id"],
            parent_bus=ctx["parent_bus"],
            checkpointer=ctx["checkpointer"],
            parent_api_key=ctx.get("parent_api_key", ""),
            parent_base_url=ctx.get("parent_base_url", ""),
        ),
        name=f"pipeline-{pid[:8]}",
    )

    batch_topology = [
        {
            "batch_num": i + 1,
            "labels": [t.get("label") for t in batch if t.get("label")],
            "agents": [t.get("agent", "rai") for t in batch],
            "depends_on": list({
                dep
                for t in batch
                for dep in (t.get("depends_on") or [])
            }),
        }
        for i, batch in enumerate(batches)
    ]

    return json.dumps({
        "pipeline_id": pid,
        "total_tasks": len(tasks),
        "batch_count": len(batches),
        "batches": batch_topology,
    })


@tool
async def check_agent_task(task_id_or_label: str) -> str:
    """Check the status and output of an HTTP-spawned subagent.

    Accepts either the task_id returned by start_agent_task OR the label
    you passed to it (e.g. 'researcher', 'exploit-writer').

    Args:
        task_id_or_label: The task_id hex string OR the label you chose.

    Returns:
        JSON with status, output (when done), agent name, and label.
    """
    ctx = _get_run_context()
    task_id, result = _resolve_task(task_id_or_label, ctx["run_id"])
    if task_id is None:
        return json.dumps({"error": result})
    status = result.get("status", "unknown")
    return json.dumps({
        "task_id": task_id,
        "agent_name": result.get("agent_name", ""),
        "status": status,
        "label": result.get("label"),
        "pipeline_id": result.get("pipeline_id"),
        "created_at": result.get("created_at", ""),
        "output": result.get("output") or "(still running — poll again shortly)",
        "hint": (
            "Use update_agent_task to send a follow-up."
            if status == "completed" else
            "Still running — call get_task_progress to see what it's doing."
        ),
    })


@tool
async def list_agent_tasks() -> str:
    """List all HTTP-spawned subagents for the current parent run.

    No parameters needed — automatically shows all agents you spawned
    in this conversation run, with their current status and output.

    Returns:
        JSON array of all agents: task_id, label, status, output preview.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    with _SUBAGENT_LOCK:
        agents = [
            {
                "task_id": tid,
                "agent_name": m.get("agent_name", ""),
                "status": m.get("status", "unknown"),
                "label": m.get("label"),
                "pipeline_id": m.get("pipeline_id"),
                "created_at": m.get("created_at", ""),
                "output_preview": (m.get("output") or "")[:300],
            }
            for tid, m in _SUBAGENT_REGISTRY.items()
            if m.get("parent_run_id") == run_id
        ]
    agents.sort(key=lambda x: x.get("created_at", ""))
    return json.dumps({"count": len(agents), "agents": agents})


@tool
async def get_task_progress(task_id_or_label: str) -> str:
    """Get live progress of a running HTTP subagent from its LangGraph checkpoint.

    Shows every tool call and partial AI response the subagent has made so far.
    Safe to call while the agent is running — does not interrupt it.

    Args:
        task_id_or_label: The task_id OR label you gave to start_agent_task.

    Returns:
        Formatted text showing tool calls, results, and last AI output.
    """
    ctx = _get_run_context()
    task_id, result = _resolve_task(task_id_or_label, ctx["run_id"])
    if task_id is None:
        return result  # type: ignore[return-value]

    with _SUBAGENT_LOCK:
        graph_info = _SUBAGENT_GRAPHS.get(task_id)

    if graph_info is None:
        status = result.get("status", "unknown")
        if status in ("completed", "failed", "cancelled", "timeout"):
            return f"Agent '{task_id_or_label}' ({status}). Output: {(result.get('output') or '')[:400]}"
        return f"Agent '{task_id_or_label}' is {status} — graph not compiled yet, try in a moment."

    graph, config = graph_info
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        return f"Could not read checkpoint for '{task_id_or_label}': {exc}"

    if not snapshot or not snapshot.values:
        return f"Agent '{task_id_or_label}' is {result.get('status')} — no checkpoint written yet."

    messages = snapshot.values.get("messages", [])
    if not messages:
        return f"Agent '{task_id_or_label}': running, no messages yet."

    def _trunc(text: str, n: int = 100) -> str:
        text = str(text).strip()
        lines = text.splitlines()
        first = lines[0][:n]
        return first + (f" (+{len(lines)-1} lines)" if len(lines) > 1 else ("…" if len(text) > n else ""))

    lines = [
        f"Progress — '{task_id_or_label}' (agent: {result.get('agent_name', '?')}) "
        f"| {len(messages)} messages | status: {result.get('status', '?')}",
    ]
    for msg in messages[-8:]:
        mtype = type(msg).__name__
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("name", "?") if isinstance(tc, dict) else getattr(tc, "name", "?")
                args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                lines.append(f"  → {name}({_trunc(str(args), 120)})")
        else:
            raw = getattr(msg, "content", "")
            if isinstance(raw, list):
                raw = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
            if not str(raw).strip():
                continue
            prefix = {"ToolMessage": "←", "HumanMessage": "task"}.get(mtype, "ai")
            lines.append(f"  {prefix}: {_trunc(raw, 180)}")
    return "\n".join(lines)


@tool
async def update_agent_task(task_id_or_label: str, message: str) -> str:
    """Send a follow-up instruction to an HTTP subagent after its turn completes.

    The subagent resumes from its LangGraph checkpoint — full conversation
    history is preserved. You will be notified when the new turn finishes.

    Args:
        task_id_or_label: The task_id OR label you gave to start_agent_task.
        message: Follow-up instruction or question to send to the subagent.

    Returns:
        JSON confirming the new turn was launched.
    """
    ctx = _get_run_context()
    task_id, meta = _resolve_task(task_id_or_label, ctx["run_id"])
    if task_id is None:
        return json.dumps({"error": meta})

    status = meta.get("status", "unknown")
    if status == "running":
        return json.dumps({
            "error": "Subagent is still running. Call check_agent_task to wait for completion first.",
            "task_id": task_id, "status": status,
        })
    if status == "interrupted":
        return json.dumps({
            "error": "Subagent is waiting for HITL approval. Resolve the interrupt first.",
            "task_id": task_id, "status": status,
        })

    try:
        launch_subagent_resume(task_id=task_id, message=message, parent_bus=ctx["parent_bus"])
    except KeyError:
        return json.dumps({"error": f"Cannot resume '{task_id_or_label}': graph not available (process may have restarted)."})

    return json.dumps({
        "task_id": task_id,
        "label": meta.get("label"),
        "status": "running",
        "message": "Follow-up delivered — subagent resumed from checkpoint. You will be notified on completion.",
    })


@tool
async def cancel_agent_task(task_id_or_label: str) -> str:
    """Cancel a running HTTP subagent.

    Args:
        task_id_or_label: The task_id OR label you gave to start_agent_task.

    Returns:
        JSON confirming cancellation.
    """
    ctx = _get_run_context()
    task_id, meta = _resolve_task(task_id_or_label, ctx["run_id"])
    if task_id is None:
        return json.dumps({"error": meta})

    status = meta.get("status", "unknown")
    if status not in ("running", "interrupted"):
        return json.dumps({
            "task_id": task_id, "label": meta.get("label"),
            "status": status, "cancelled": False,
            "message": f"Already in terminal state '{status}'.",
        })

    with _SUBAGENT_LOCK:
        task = _SUBAGENT_TASKS.get(task_id)
    if task and not task.done():
        task.cancel()
    with _SUBAGENT_LOCK:
        if task_id in _SUBAGENT_REGISTRY:
            _SUBAGENT_REGISTRY[task_id]["status"] = "cancelled"

    return json.dumps({
        "task_id": task_id, "label": meta.get("label"),
        "status": "cancelled", "cancelled": True,
    })


@tool
async def start_parallel_agents(
    tasks: str,
    agent: str = "rai",
    model: str = "",
) -> str:
    """Spawn multiple subagents in parallel and return immediately.

    All agents start at the same time. Use check_agent_task or get_agent_response
    to wait for individual results.

    Args:
        tasks: JSON array of task objects. Each object must have 'prompt' (str)
               and optionally 'label' (str), 'agent' (str), 'model' (str),
               'system_prompt' (str). Example:
               '[{"prompt": "Do X", "label": "worker-1"}, {"prompt": "Do Y", "label": "worker-2"}]'
        agent: Default agent name if not specified per-task.
        model: Default model override if not specified per-task.

    Returns:
        JSON array of spawned tasks with task_id and label for each.
    """
    ctx = _get_run_context()
    try:
        task_list = json.loads(tasks)
        if not isinstance(task_list, list):
            return json.dumps({"error": "tasks must be a JSON array"})
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid JSON for tasks: {exc}"})

    # Validate all agent names up front before spawning anything
    for spec in task_list:
        if not isinstance(spec, dict):
            continue
        effective_agent = spec.get("agent") or agent
        err = _check_agent_name(effective_agent, ctx)
        if err:
            return json.dumps({"error": err})

    spawned = []
    for spec in task_list:
        if not isinstance(spec, dict) or not spec.get("prompt"):
            continue

        task_id = uuid4().hex
        requested_label = spec.get("label", "")
        effective_agent = spec.get("agent") or agent
        effective_model = spec.get("model") or model
        effective_system_prompt = spec.get("system_prompt") or None

        if requested_label:
            with _SUBAGENT_LOCK:
                assigned_label: str | None = _assign_label(requested_label, ctx["run_id"])
        else:
            assigned_label = None

        launch_subagent(
            task_id=task_id,
            agent_name=effective_agent,
            model=effective_model,
            input_message=spec["prompt"],
            parent_run_id=ctx["run_id"],
            parent_thread_id=ctx["thread_id"],
            parent_bus=ctx["parent_bus"],
            checkpointer=ctx["checkpointer"],
            system_prompt=effective_system_prompt,
            label=assigned_label,
            parent_api_key=ctx.get("parent_api_key", ""),
            parent_base_url=ctx.get("parent_base_url", ""),
        )

        entry: dict = {
            "task_id": task_id,
            "label": assigned_label,
            "agent": effective_agent,
            "status": "running",
        }
        if assigned_label and assigned_label != requested_label:
            entry["label_note"] = f"Label '{requested_label}' was already in use — assigned '{assigned_label}' instead."
        spawned.append(entry)

    ref_list = ", ".join(f"'{e['label'] or e['task_id'][:12]}'" for e in spawned)
    return json.dumps({
        "spawned": spawned,
        "count": len(spawned),
        "hint": f"Use get_agent_response({ref_list}) or check_agent_task per label to wait for results.",
    })


@tool
async def get_agent_response(
    task_id_or_label: str,
    timeout: int = 300,
) -> str:
    """Wait for an HTTP-spawned subagent to finish and return its full output.

    Blocks until the agent transitions out of 'running' / 'interrupted' state,
    then returns the output. Use check_agent_task for a non-blocking status check.

    Args:
        task_id_or_label: The task_id OR label you gave to start_agent_task.
        timeout: Maximum seconds to wait (default 300).

    Returns:
        JSON with final status and full output text.
    """
    ctx = _get_run_context()
    task_id, meta = _resolve_task(task_id_or_label, ctx["run_id"])
    if task_id is None:
        return json.dumps({"error": meta})

    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        with _SUBAGENT_LOCK:
            current = _SUBAGENT_REGISTRY.get(task_id, {})
        status = current.get("status", "unknown")
        if status not in ("running", "interrupted"):
            break
        if asyncio.get_event_loop().time() >= deadline:
            return json.dumps({
                "error": f"Timed out waiting for '{task_id_or_label}' after {timeout}s.",
                "task_id": task_id,
                "status": status,
            })
        await asyncio.sleep(1.0)

    return json.dumps({
        "task_id": task_id,
        "label": current.get("label"),
        "agent_name": current.get("agent_name", ""),
        "status": status,
        "output": current.get("output") or "",
    })


@tool
async def write_lesson(insight: str, category: str = "general") -> str:
    """Record a lesson or insight to persistent agent memory for future runs.

    Use during or after tasks to capture successful techniques, failures,
    target-specific discoveries, and patterns to repeat or avoid. Lessons
    are appended to lessons.md and loaded by the agent on the next run.

    Args:
        insight: The lesson in 1-3 sentences.
        category: "technique", "failure", "discovery", or "general".
    """
    from pathlib import Path as _Path
    from datetime import UTC, datetime as _dt
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    import rai.harness.runner as _runner
    agent_name = (_runner._RUN_REGISTRY.get(run_id) or {}).get("agent_name", "unknown")
    ts = _dt.now(UTC).strftime("%Y-%m-%d")
    lessons_path = _Path.home() / ".rai" / "agents" / agent_name / "memory" / "lessons.md"
    lessons_path.parent.mkdir(parents=True, exist_ok=True)
    with lessons_path.open("a", encoding="utf-8") as f:
        f.write(f"- [{ts}] **[{category}]** {insight}\n")
    return f"Lesson recorded under '{category}'."


@tool
async def list_available_agents() -> str:
    """List all subagents available on this server that can be called with start_agent_task or run_agent_sync.

    Call this before dispatching work to a subagent to confirm the exact agent
    name. Using an unknown name will return an error immediately.

    Returns:
        Formatted list of agent names and their descriptions.
    """
    ctx = _get_run_context()
    parent = ctx.get("agent_name", "rai")
    available = _get_available_agents(parent)
    if not available:
        return f"No subagents configured for '{parent}'. Use agent='rai' to dispatch to the base agent."
    lines = ["Available subagents (pass the name as the 'agent' parameter):\n"]
    for name, desc in sorted(available.items()):
        lines.append(f"  • {name} — {desc}" if desc else f"  • {name}")
    lines.append("\nUse agent='rai' to dispatch to the base RAI agent (no specialisation).")
    return "\n".join(lines)


def get_http_subagent_tools() -> list:
    """Return all HTTP subagent tools to inject into the parent agent.

    These use the same names as deepagents' LocalAsyncAgentMiddleware tools
    so the LLM can use them without relearning. LocalAsyncAgentMiddleware must
    be suppressed in HTTP mode to avoid name collisions.
    """
    from rai.harness.plan.tools import get_plan_tools
    return [
        list_available_agents,
        start_agent_task, run_agent_sync, start_pipeline,
        start_parallel_agents, check_agent_task, list_agent_tasks,
        get_task_progress, update_agent_task, get_agent_response,
        cancel_agent_task, write_lesson,
    ] + get_plan_tools()
