"""Non-blocking local subagent dispatch for RAI.

Provides tools that launch named subagents as asyncio.Task objects so the
core agent is NOT blocked.  The core agent receives a task_id immediately
and can continue working while subagents execute concurrently.

Output is written to /tmp/rai_task_<id>.json when the task completes.
Task metadata is persisted in LangGraph state so it survives context
compaction and /threads switches.

Tools provided:
  - start_agent_task      — launch one subagent in the background
  - start_parallel_agents — launch multiple subagents at once (no dependency tracking)
  - start_pipeline        — launch a DAG of subagents with depends_on ordering
  - check_agent_task      — get status + output of a task
  - list_agent_tasks      — list all tracked tasks with live status
  - cancel_agent_task     — cancel a running task
  - get_task_progress     — read live checkpoint of a running task
  - update_agent_task     — send a follow-up to a completed subagent (checkpoint resume)
  - get_agent_response    — wait for subagent reply after update_agent_task
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NotRequired, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.agents.middleware.types import AgentMiddleware, AgentState, ContextT, ModelResponse, ResponseT
from langchain.tools import ToolRuntime  # noqa: TC002
from rai.middleware.prompt_cache import RAIPromptCachingMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from deepagents.middleware._utils import append_to_system_message
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable
    from langchain_core.tools import BaseTool
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level task registry (process lifetime, not serialized to LangGraph state)
# ---------------------------------------------------------------------------

_TASK_REGISTRY: dict[str, asyncio.Task[Any]] = {}

# Completed task notifications waiting to be injected into the next LLM call.
# Populated by _on_done callback; consumed (popped) in awrap_model_call.
# Each value: {agent_name, status, output, output_file}
_PENDING_NOTIFICATIONS: dict[str, dict[str, str]] = {}

# Guards all reads and writes to _PENDING_NOTIFICATIONS.
# _on_done is called as an asyncio task done-callback (event-loop thread) but
# may also fire from run_in_executor threads in some executor configurations;
# a threading.Lock is safe from both contexts and from the asyncio watcher loop.
_NOTIF_LOCK: threading.Lock = threading.Lock()

# Maps task_id → agent_name for all live tasks.  Written in _launch_task,
# cleaned in _on_done under _NOTIF_LOCK.  Read by get_running_agent_names()
# for the TUI status panel — zero-cost dict lookup, no I/O.
_TASK_AGENT_NAMES: dict[str, str] = {}

# Task IDs that were proactively cancelled via cancel_agent_task.  When _on_done
# later fires for these (after the asyncio task finally handles CancelledError),
# the notification is skipped to prevent a duplicate LLM re-invoke.
_MANUALLY_CANCELLED: set[str] = set()

# Per-task output queue — subagent puts its response here after each ainvoke.
# get_agent_response blocks on this queue.  Cleaned in _on_done.
_TASK_OUTPUT_QUEUES: dict[str, asyncio.Queue[str]] = {}  # subagent → parent

# Stores (runnable, timeout) per task_id so update_agent_task can re-launch
# a completed subagent from its LangGraph checkpoint.
# Intentionally NOT cleaned in _on_done — survives task completion for restart.
_TASK_RUNNABLES: dict[str, tuple[Any, int]] = {}

# pipeline_id → asyncio.Task wrapping the _run_pipeline orchestrator coroutine.
_PIPELINE_GROUPS: dict[str, asyncio.Task[Any]] = {}

# State keys excluded when building subagent input state
_EXCLUDED_STATE_KEYS = frozenset({
    "messages", "todos", "structured_response",
    "skills_metadata", "memory_contents", "local_async_tasks",
})

_TERMINAL_STATUSES = frozenset({"success", "error", "cancelled", "timeout"})

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class LocalAsyncTask(TypedDict):
    """Serializable metadata for a background agent task.  Stored in LangGraph state."""

    task_id: str
    agent_name: str
    status: str            # running | success | error | cancelled | pending | skipped
    created_at: str        # ISO-8601 UTC
    last_checked_at: str   # ISO-8601 UTC
    output_file: str       # /tmp/rai_task_<id>.json
    label: NotRequired[str]             # unique DAG node name (set by start_pipeline)
    depends_on: NotRequired[list[str]]  # labels that must complete before this task
    pipeline_id: NotRequired[str]       # groups all tasks from one start_pipeline call


def _tasks_reducer(
    existing: dict[str, LocalAsyncTask] | None,
    update: dict[str, LocalAsyncTask],
) -> dict[str, LocalAsyncTask]:
    merged = dict(existing or {})
    merged.update(update)
    return merged


class LocalAsyncAgentState(AgentState):
    """State extension that persists background task metadata across turns."""

    local_async_tasks: Annotated[NotRequired[dict[str, LocalAsyncTask]], _tasks_reducer]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_output(result: Any) -> str:
    """Pull the last message text out of a subagent ainvoke result."""
    if not isinstance(result, dict):
        return str(result)
    messages = result.get("messages", [])
    if not messages:
        return "(no output)"
    last = messages[-1]
    content = getattr(last, "content", None)
    if content is None:
        return "(no output)"
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                parts.append(str(block))
        return " ".join(p for p in parts if p)
    return str(content)


def _read_output_file(out_path: Path) -> dict[str, str]:
    """Read the JSON output file.  Returns {"status": ..., "output": ...}."""
    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "unknown", "output": ""}


def _get_live_status(task_id: str, tracked: LocalAsyncTask) -> tuple[str, str]:
    """Return (status, output) for a task, checking live state where possible."""
    if tracked["status"] in _TERMINAL_STATUSES:
        # Already settled — read output file for the output text
        data = _read_output_file(Path(tracked["output_file"]))
        return tracked["status"], data.get("output", "")

    asyncio_task = _TASK_REGISTRY.get(task_id)
    if asyncio_task is None:
        # Process restarted — read output file
        data = _read_output_file(Path(tracked["output_file"]))
        return data.get("status", "unknown"), data.get("output", "")

    if asyncio_task.done():
        data = _read_output_file(Path(tracked["output_file"]))
        return data.get("status", "unknown"), data.get("output", "")

    return "running", ""


def _resolve_tracked(task_id: str, runtime: ToolRuntime) -> LocalAsyncTask | str:
    """Look up a tracked task from state.  Returns the task or an error string."""
    tasks: dict[str, LocalAsyncTask] = (runtime.state or {}).get("local_async_tasks") or {}
    tracked = tasks.get(task_id.strip())
    if not tracked:
        return f"No tracked task found for task_id: {task_id!r}"
    return tracked


# ---------------------------------------------------------------------------
# Done callback (writes output JSON when asyncio.Task completes)
# ---------------------------------------------------------------------------


def _on_done(t: asyncio.Task[Any], task_id: str, out_path: Path, agent_name: str = "") -> None:
    try:
        result = t.result()
        output = _extract_output(result)
        data: dict[str, str] = {"status": "success", "output": output, "task_id": task_id}
    except asyncio.CancelledError:
        data = {"status": "cancelled", "output": "", "task_id": task_id}
    except asyncio.TimeoutError:
        data = {
            "status": "timeout",
            "output": f"Task timed out after {_DEFAULT_TASK_TIMEOUT}s",
            "task_id": task_id,
        }
    except Exception as exc:
        logger.exception("Background agent task %s failed", task_id)
        data = {"status": "error", "output": str(exc), "task_id": task_id}
    try:
        out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write task output to %s: %s", out_path, exc)
    finally:
        # Pop from _TASK_REGISTRY and write to _PENDING_NOTIFICATIONS atomically
        # under _NOTIF_LOCK.  This prevents _wait_until_all_done from seeing
        # _TASK_REGISTRY empty AND _PENDING_NOTIFICATIONS empty simultaneously
        # between the two writes (TOCTOU false-quiescence).
        with _NOTIF_LOCK:
            # Guard: only remove registry entries if this asyncio.Task is still
            # the live one for this task_id.  get_agent_response / update_agent_task
            # may have relaunched a NEW task (with a new asyncio.Task object and a new
            # out_q) for the same task_id before this done-callback fires.  Without the
            # guard, _on_done would silently pop the new task's queue, causing the
            # parent's await-on-new-out_q to block forever.
            is_current = _TASK_REGISTRY.get(task_id) is t
            if is_current:
                _TASK_REGISTRY.pop(task_id, None)
                _TASK_AGENT_NAMES.pop(task_id, None)
                _TASK_OUTPUT_QUEUES.pop(task_id, None)
            # _TASK_RUNNABLES is intentionally kept — update_agent_task needs it for restart.
            manually_cancelled = task_id in _MANUALLY_CANCELLED
            if is_current:
                _MANUALLY_CANCELLED.discard(task_id)
            # Skip re-posting if cancel_agent_task already pushed the notification
            # (avoids the LLM seeing a duplicate "task cancelled" re-invoke).
            if is_current and not manually_cancelled:
                _PENDING_NOTIFICATIONS[task_id] = {
                    "agent_name": agent_name,
                    "status": data["status"],
                    "output": data.get("output", ""),
                    "output_file": str(out_path),
                }


def pop_pending_notification_text() -> str:
    """Pop all pending subagent notifications and return them as a formatted text block.

    Returns empty string if nothing is pending.
    Safe to call from any context — accesses _PENDING_NOTIFICATIONS under _NOTIF_LOCK.
    """
    with _NOTIF_LOCK:
        if not _PENDING_NOTIFICATIONS:
            return ""
        items = list(_PENDING_NOTIFICATIONS.items())
        _PENDING_NOTIFICATIONS.clear()
    lines: list[str] = []
    for task_id, info in items:
        preview = (info["output"] or "(no output)")[:400]
        status = info.get("status", "")
        if status == "turn_complete":
            lines.append(
                f"[Background agent turn completed]\n"
                f"Agent: {info['agent_name']} | Task ID: {task_id}\n"
                f"Output preview: {preview}\n"
                f"Use get_agent_response(task_id) to read the full reply, "
                f"or update_agent_task(task_id, message) to continue the conversation."
            )
        else:
            lines.append(
                f"[Background agent completed]\n"
                f"Agent: {info['agent_name']} | Task ID: {task_id} | Status: {status}\n"
                f"Output preview: {preview}\n"
                f"Full output: {info['output_file']}"
            )
    return "\n\n".join(lines)


def get_running_agent_names() -> dict[str, str]:
    """Return a snapshot {task_id: agent_name} for all currently running tasks.

    Safe to call from any thread — reads both dicts under _NOTIF_LOCK.
    Returns an empty dict when no tasks are running.
    """
    with _NOTIF_LOCK:
        return {tid: _TASK_AGENT_NAMES.get(tid, tid[:8]) for tid in _TASK_REGISTRY}


# Default background task timeout — prevents nmap/nuclei/long scans running indefinitely.
# Subagent's BashTool has its own per-command timeout (120s default); this caps the
# entire subagent session wall-clock time.
_DEFAULT_TASK_TIMEOUT: int = 3600  # 1 hour


def _launch_task(
    runnable: Runnable,
    state: dict[str, Any],
    task_id: str,
    out_path: Path,
    initial_message: str,
    agent_name: str = "",
    timeout: int = _DEFAULT_TASK_TIMEOUT,
) -> asyncio.Task[Any]:
    """Create an asyncio.Task that runs one subagent turn (fire-and-forget).

    Calls ``runnable.ainvoke`` once with the LangGraph ``thread_id=task_id``
    so checkpoints accumulate.  After the turn completes the result is placed
    in ``_TASK_OUTPUT_QUEUES[task_id]`` and a "turn_complete" notification is
    pushed so the parent LLM is re-invoked.

    Re-engagement is done via ``update_agent_task``, which relaunches this
    function with a new ``initial_message`` and ``state={}``.  LangGraph loads
    the existing checkpoint for ``thread_id=task_id`` and appends the new
    HumanMessage via the ``add_messages`` reducer — full conversation history
    is preserved across all turns.
    """
    subagent_state = {k: v for k, v in state.items() if k not in _EXCLUDED_STATE_KEYS}
    subagent_state["messages"] = [HumanMessage(content=initial_message)]

    out_q: asyncio.Queue[str] = asyncio.Queue()

    with _NOTIF_LOCK:
        _TASK_OUTPUT_QUEUES[task_id] = out_q
        _TASK_RUNNABLES[task_id]     = (runnable, timeout)

    async def _run() -> Any:
        result = await asyncio.wait_for(
            runnable.ainvoke(subagent_state, config={"configurable": {"thread_id": task_id}, "recursion_limit": 50}),
            timeout=timeout,
        )
        output = _extract_output(result)
        out_q.put_nowait(output)
        # Notify parent LLM so it is re-invoked with a [Background agent turn completed]
        # message.  Status "turn_complete" is not terminal — TUI panel stays running.
        with _NOTIF_LOCK:
            _PENDING_NOTIFICATIONS[task_id] = {
                "agent_name": agent_name,
                "status": "turn_complete",
                "output": output,
                "output_file": str(out_path),
            }
        return result

    task: asyncio.Task[Any] = asyncio.create_task(_run(), name=f"rai-agent-{task_id[:8]}")
    task.add_done_callback(functools.partial(_on_done, task_id=task_id, out_path=out_path, agent_name=agent_name))
    _TASK_REGISTRY[task_id]    = task
    _TASK_AGENT_NAMES[task_id] = agent_name
    return task


def _make_task_meta(task_id: str, agent_name: str, out_path: Path) -> LocalAsyncTask:
    now = _now()
    return LocalAsyncTask(
        task_id=task_id,
        agent_name=agent_name,
        status="running",
        created_at=now,
        last_checked_at=now,
        output_file=str(out_path),
    )


def _plan_batches(tasks: list[dict]) -> list[list[dict]]:
    """Return tasks grouped into sequentially-executable batches.

    Tasks within a batch have no inter-dependencies and run in parallel.
    Raises ValueError if a cycle or unknown depends_on label is detected.
    """
    known_labels = {t["label"] for t in tasks if t.get("label")}
    for t in tasks:
        for dep in t.get("depends_on", []):
            if dep not in known_labels:
                raise ValueError(
                    f"Unknown depends_on label '{dep}' in task '{t.get('label')}'"
                )

    remaining = list(tasks)
    scheduled: set[str] = set()
    batches: list[list[dict]] = []

    while remaining:
        ready = [
            t for t in remaining
            if all(dep in scheduled for dep in t.get("depends_on", []))
        ]
        if not ready:
            raise ValueError(
                f"Cycle detected — unresolvable tasks: "
                f"{[t.get('label') for t in remaining]}"
            )
        batches.append(ready)
        scheduled.update(t["label"] for t in ready if t.get("label"))
        remaining = [t for t in remaining if t not in ready]

    return batches


def _prepend_context(prompt: str, prior_results: list[dict]) -> str:
    """Prepend completed prior-batch results to a task prompt.

    Injects the last 4 results only to stay within context budget.
    """
    if not prior_results:
        return prompt
    lines = ["<system-reminder>", "Prior pipeline task results:"]
    for r in prior_results[-4:]:
        label = r.get("label", "unknown")
        status = r.get("status", "?")
        output = (r.get("output", "") or "")[:2000]
        lines.append(f"[{label}] ({status}): {output}")
    lines.append("</system-reminder>")
    return "\n".join(lines) + "\n\n" + prompt


async def _run_pipeline(
    pipeline_id: str,
    batches: list[list[dict]],
    subagent_graphs: dict[str, Any],
    base_state: dict,
    continue_on_error: bool,
    max_failures: int | None,
) -> None:
    """Sequentially execute batches of subagent tasks, parallelising within each batch."""
    completed_labels: set[str] = set()
    failed_labels: set[str] = set()
    prior_results: list[dict] = []
    dependency_skips = 0
    total_batches = len(batches)

    for batch_num, batch in enumerate(batches, start=1):
        runnable_specs: list[dict] = []
        for spec in batch:
            label = spec.get("label", "")
            if any(dep in failed_labels for dep in spec.get("depends_on", [])):
                dependency_skips += 1
                failed_labels.add(label)
                prior_results.append({"label": label, "status": "skipped", "output": ""})
                continue
            runnable_specs.append(spec)

        if not runnable_specs:
            with _NOTIF_LOCK:
                _PENDING_NOTIFICATIONS[f"{pipeline_id}:batch{batch_num}"] = {
                    "agent_name": "pipeline",
                    "status": "batch_skipped",
                    "output": (
                        f"[Pipeline {pipeline_id[:6]}] Batch {batch_num}/{total_batches} "
                        f"entirely skipped due to dependency failures."
                    ),
                    "output_file": "",
                }
            continue

        batch_task_ids: list[str] = []
        batch_labels: list[str] = []

        for spec in runnable_specs:
            agent_name = spec.get("agent", "")
            label = spec.get("label", "")
            prompt = _prepend_context(spec.get("prompt", ""), prior_results)

            if agent_name not in subagent_graphs:
                failed_labels.add(label)
                prior_results.append({
                    "label": label, "status": "error",
                    "output": f"Unknown subagent '{agent_name}'",
                })
                continue

            task_id = uuid.uuid4().hex
            out_path = Path(f"/tmp/rai_task_{task_id[:12]}.json")
            try:
                out_path.write_text(
                    json.dumps({"status": "running", "output": "", "task_id": task_id}),
                    encoding="utf-8",
                )
            except OSError:
                failed_labels.add(label)
                prior_results.append({"label": label, "status": "error", "output": "OSError creating output file"})
                continue

            _launch_task(
                subagent_graphs[agent_name], base_state, task_id,
                out_path, initial_message=prompt, agent_name=agent_name,
            )
            batch_task_ids.append(task_id)
            batch_labels.append(label)

        async_tasks = [
            _TASK_REGISTRY[tid] for tid in batch_task_ids if tid in _TASK_REGISTRY
        ]
        results: list[Any] = (
            await asyncio.gather(*async_tasks, return_exceptions=True)
            if async_tasks else []
        )

        n_ok, n_fail = 0, 0
        for tid, label, result in zip(batch_task_ids, batch_labels, results):
            out_path = Path(f"/tmp/rai_task_{tid[:12]}.json")
            if isinstance(result, Exception):
                status = "cancelled" if isinstance(result, asyncio.CancelledError) else "error"
                output = ""
                failed_labels.add(label)
                n_fail += 1
            else:
                data = _read_output_file(out_path)
                status = data.get("status", "success")
                output = data.get("output", "")
                if status in _TERMINAL_STATUSES - {"success"}:
                    failed_labels.add(label)
                    n_fail += 1
                else:
                    completed_labels.add(label)
                    n_ok += 1
            prior_results.append({"label": label, "status": status, "output": output})

        abort = (not continue_on_error and n_fail > 0) or (
            max_failures is not None and len(failed_labels) >= max_failures
        )

        summary = (
            f"[Pipeline {pipeline_id[:6]}] Batch {batch_num}/{total_batches} complete: "
            f"{n_ok} ok, {n_fail} failed, {dependency_skips} skipped."
        )
        if abort:
            summary += " Pipeline aborted."
        with _NOTIF_LOCK:
            _PENDING_NOTIFICATIONS[f"{pipeline_id}:batch{batch_num}"] = {
                "agent_name": "pipeline",
                "status": "batch_complete",
                "output": summary,
                "output_file": "",
            }

        if abort:
            return

    final = (
        f"[Pipeline {pipeline_id[:6]}] Complete. "
        f"{len(completed_labels)} succeeded, {len(failed_labels)} failed, "
        f"{dependency_skips} dependency-skipped."
    )
    with _NOTIF_LOCK:
        _PENDING_NOTIFICATIONS[f"{pipeline_id}:done"] = {
            "agent_name": "pipeline",
            "status": "pipeline_complete",
            "output": final,
            "output_file": "",
        }


# ---------------------------------------------------------------------------
# Tool builders
# ---------------------------------------------------------------------------

_NO_LOOP_MSG = (
    "Cannot start background task: no asyncio event loop is running. "
    "RAI must be invoked via 'rai chat' (TUI) to use background agents. "
    "Use the 'task' tool instead for synchronous subagent execution."
)


def _build_start_tool(subagent_graphs: dict[str, Runnable]) -> StructuredTool:
    agents_list = ", ".join(f"`{k}`" for k in subagent_graphs)

    def start_agent_task(
        description: Annotated[str, "Detailed task for the subagent to perform autonomously."],
        subagent_type: Annotated[str, "Name of the subagent to launch. Must be one of the available types."],
        runtime: ToolRuntime,
    ) -> str:
        return _NO_LOOP_MSG

    async def astart_agent_task(
        description: Annotated[str, "Detailed task for the subagent to perform autonomously."],
        subagent_type: Annotated[str, "Name of the subagent to launch. Must be one of the available types."],
        runtime: ToolRuntime,
    ) -> str | Command:
        if subagent_type not in subagent_graphs:
            return f"Unknown subagent '{subagent_type}'. Available: {agents_list}"
        if not runtime.tool_call_id:
            return "Internal error: missing tool_call_id"

        task_id = uuid.uuid4().hex
        out_path = Path(f"/tmp/rai_task_{task_id[:12]}.json")

        # Write initial placeholder so the file exists immediately
        try:
            out_path.write_text(
                json.dumps({"status": "running", "output": "", "task_id": task_id}),
                encoding="utf-8",
            )
        except OSError as exc:
            return f"Could not create output file {out_path}: {exc}"

        state = dict(runtime.state or {})
        try:
            _launch_task(subagent_graphs[subagent_type], state, task_id, out_path, initial_message=description, agent_name=subagent_type)
        except RuntimeError as exc:
            return f"asyncio error launching task: {exc}. Use the 'task' tool instead."

        meta = _make_task_meta(task_id, subagent_type, out_path)
        msg = (
            f"Background agent started.\n"
            f"task_id: {task_id}\n"
            f"agent:   {subagent_type}\n"
            f"output:  {out_path}\n"
            f"Use check_agent_task to get status and output."
        )
        return Command(
            update={
                "messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id)],
                "local_async_tasks": {task_id: meta},
            }
        )

    return StructuredTool.from_function(
        name="start_agent_task",
        func=start_agent_task,
        coroutine=astart_agent_task,
        description=(
            "Launch a named subagent as a background task without blocking. "
            f"Available subagents: {agents_list}. "
            "Returns a task_id immediately — the subagent runs concurrently. "
            "Use check_agent_task to poll for status and output."
        ),
    )


def _build_parallel_tool(subagent_graphs: dict[str, Runnable]) -> StructuredTool:
    agents_list = ", ".join(f"`{k}`" for k in subagent_graphs)

    def start_parallel_agents(
        tasks: Annotated[
            list[dict],
            "List of tasks to launch. Each must have 'agent' (subagent name) and 'prompt' (task description).",
        ],
        runtime: ToolRuntime,
    ) -> str:
        return _NO_LOOP_MSG

    async def astart_parallel_agents(
        tasks: Annotated[
            list[dict],
            "List of tasks to launch. Each must have 'agent' (subagent name) and 'prompt' (task description).",
        ],
        runtime: ToolRuntime,
    ) -> str | Command:
        if not tasks:
            return "No tasks provided."
        if not runtime.tool_call_id:
            return "Internal error: missing tool_call_id"

        launched: list[str] = []
        errors: list[str] = []
        new_tasks: dict[str, LocalAsyncTask] = {}

        base_state = dict(runtime.state or {})

        for spec in tasks:
            agent_name = spec.get("agent", "")
            prompt = spec.get("prompt", "")

            if agent_name not in subagent_graphs:
                errors.append(f"Unknown subagent '{agent_name}' — skipped")
                continue

            task_id = uuid.uuid4().hex
            out_path = Path(f"/tmp/rai_task_{task_id[:12]}.json")
            try:
                out_path.write_text(
                    json.dumps({"status": "running", "output": "", "task_id": task_id}),
                    encoding="utf-8",
                )
            except OSError as exc:
                errors.append(f"Could not create output file for '{agent_name}': {exc} — skipped")
                continue

            state = dict(base_state)
            try:
                _launch_task(subagent_graphs[agent_name], state, task_id, out_path, initial_message=prompt, agent_name=agent_name)
            except RuntimeError as exc:
                errors.append(f"asyncio error for '{agent_name}': {exc} — skipped")
                continue

            meta = _make_task_meta(task_id, agent_name, out_path)
            new_tasks[task_id] = meta
            launched.append(f"  task_id: {task_id}  agent: {agent_name}  output: {out_path}")

        lines = [f"Launched {len(launched)} background agent(s):"] + launched
        if errors:
            lines += ["", "Errors:"] + [f"  {e}" for e in errors]
        lines.append("\nUse check_agent_task <task_id> to poll status and output.")

        return Command(
            update={
                "messages": [ToolMessage("\n".join(lines), tool_call_id=runtime.tool_call_id)],
                "local_async_tasks": new_tasks,
            }
        )

    return StructuredTool.from_function(
        name="start_parallel_agents",
        func=start_parallel_agents,
        coroutine=astart_parallel_agents,
        description=(
            "Launch multiple subagents in parallel without blocking. "
            "Each task needs 'agent' (subagent name) and 'prompt' (task description). "
            f"Available subagents: {agents_list}. "
            "Returns all task_ids at once. Use check_agent_task to poll each one."
        ),
    )


def _build_pipeline_tool(subagent_graphs: dict[str, Runnable]) -> StructuredTool:
    agents_list = ", ".join(f"`{k}`" for k in subagent_graphs)

    def start_pipeline(
        tasks: Annotated[
            list[dict],
            (
                "List of task specs. Each must have: 'label' (unique DAG node name), "
                "'agent' (subagent name), 'prompt' (task description). "
                "Optional: 'depends_on' (list of labels that must complete first)."
            ),
        ],
        continue_on_error: Annotated[bool, "Keep running later batches even if a task fails. Default True."] = True,
        max_failures: Annotated[int | None, "Abort the pipeline after this many task failures. None = no limit."] = None,
        runtime: ToolRuntime = None,  # type: ignore[assignment]
    ) -> str:
        return _NO_LOOP_MSG

    async def astart_pipeline(
        tasks: Annotated[
            list[dict],
            (
                "List of task specs. Each must have: 'label' (unique DAG node name), "
                "'agent' (subagent name), 'prompt' (task description). "
                "Optional: 'depends_on' (list of labels that must complete first)."
            ),
        ],
        continue_on_error: Annotated[bool, "Keep running later batches even if a task fails. Default True."] = True,
        max_failures: Annotated[int | None, "Abort the pipeline after this many task failures. None = no limit."] = None,
        runtime: ToolRuntime = None,  # type: ignore[assignment]
    ) -> str | Command:
        if not tasks:
            return "No tasks provided."
        if not runtime or not runtime.tool_call_id:
            return "Internal error: missing tool_call_id"

        # Validate required fields
        for i, spec in enumerate(tasks):
            if not spec.get("label"):
                return f"Task at index {i} is missing required 'label' field."
            if not spec.get("agent"):
                return f"Task '{spec.get('label')}' is missing required 'agent' field."
            if spec["agent"] not in subagent_graphs:
                return f"Task '{spec['label']}': unknown subagent '{spec['agent']}'. Available: {agents_list}"

        # Compute batches (validates depends_on labels and detects cycles)
        try:
            batches = _plan_batches(tasks)
        except ValueError as exc:
            return str(exc)

        pipeline_id = uuid.uuid4().hex
        now = _now()

        # Build human-readable batch plan
        batch_desc = " → ".join(
            f"Batch {i + 1}: [{', '.join(t['label'] for t in b)}]"
            for i, b in enumerate(batches)
        )

        # Build placeholder LocalAsyncTask entries (one per task, status "pending")
        new_tasks: dict[str, LocalAsyncTask] = {}
        label_to_task_id: dict[str, str] = {}
        for spec in tasks:
            task_id = uuid.uuid4().hex
            label = spec["label"]
            label_to_task_id[label] = task_id
            out_path = Path(f"/tmp/rai_task_{task_id[:12]}.json")
            try:
                out_path.write_text(
                    json.dumps({"status": "pending", "output": "", "task_id": task_id}),
                    encoding="utf-8",
                )
            except OSError:
                pass
            new_tasks[task_id] = LocalAsyncTask(
                task_id=task_id,
                agent_name=spec["agent"],
                status="pending",
                created_at=now,
                last_checked_at=now,
                output_file=str(out_path),
                label=label,
                depends_on=list(spec.get("depends_on", [])),
                pipeline_id=pipeline_id,
            )

        base_state = dict(runtime.state or {})

        # Launch the orchestrator coroutine as a background asyncio.Task
        orch_task: asyncio.Task[Any] = asyncio.create_task(
            _run_pipeline(
                pipeline_id=pipeline_id,
                batches=batches,
                subagent_graphs=subagent_graphs,
                base_state=base_state,
                continue_on_error=continue_on_error,
                max_failures=max_failures,
            ),
            name=f"rai-pipeline-{pipeline_id[:8]}",
        )
        _PIPELINE_GROUPS[pipeline_id] = orch_task

        msg = (
            f"Pipeline started.\n"
            f"pipeline_id: {pipeline_id}\n"
            f"tasks: {len(tasks)} across {len(batches)} batch(es)\n"
            f"plan: {batch_desc}\n"
            f"You will receive a notification after each batch completes."
        )
        return Command(
            update={
                "messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id)],
                "local_async_tasks": new_tasks,
            }
        )

    return StructuredTool.from_function(
        name="start_pipeline",
        func=start_pipeline,
        coroutine=astart_pipeline,
        description=(
            "Launch a pipeline of subagent tasks with declared dependencies. "
            "Tasks with no dependencies run in parallel (same batch); tasks with "
            "'depends_on' wait for their predecessors. Prior batch outputs are injected "
            "as context into dependent tasks automatically. "
            "Use instead of start_parallel_agents when tasks must be ordered "
            "(e.g. recon → exploit → report). "
            f"Available subagents: {agents_list}."
        ),
    )


def _build_check_tool() -> StructuredTool:
    def check_agent_task(
        task_id: Annotated[str, "The exact task_id returned by start_agent_task or start_parallel_agents."],
        runtime: ToolRuntime,
    ) -> str:
        return _NO_LOOP_MSG

    async def acheck_agent_task(
        task_id: Annotated[str, "The exact task_id returned by start_agent_task or start_parallel_agents."],
        runtime: ToolRuntime,
    ) -> str | Command:
        tracked = _resolve_tracked(task_id, runtime)
        if isinstance(tracked, str):
            return tracked

        status, output = _get_live_status(task_id, tracked)
        now = _now()

        # Build human-readable result
        lines = [f"task_id: {task_id}", f"agent:   {tracked['agent_name']}", f"status:  {status}"]
        if status == "running":
            lines.append(f"started: {tracked['created_at']}")
        elif status == "success":
            lines.append(f"\nOutput:\n{output}" if output else "\n(no output)")
        elif status == "error":
            lines.append(f"\nError:\n{output}")
        elif status in ("cancelled", "timeout"):
            lines.append(f"\n({status})")
        else:
            lines.append(f"\nOutput file: {tracked['output_file']}")

        updated = LocalAsyncTask(
            task_id=tracked["task_id"],
            agent_name=tracked["agent_name"],
            status=status,
            created_at=tracked["created_at"],
            last_checked_at=now,
            output_file=tracked["output_file"],
        )
        return Command(
            update={
                "messages": [ToolMessage("\n".join(lines), tool_call_id=runtime.tool_call_id)],
                "local_async_tasks": {task_id: updated},
            }
        )

    return StructuredTool.from_function(
        name="check_agent_task",
        func=check_agent_task,
        coroutine=acheck_agent_task,
        description=(
            "Check the status and output of a background agent task. "
            "Returns 'running' (with elapsed time) or the final output/error when complete. "
            "Only call when the user explicitly asks for a status update — do not poll in a loop."
        ),
    )


def _build_list_tool() -> StructuredTool:
    def list_agent_tasks(
        runtime: ToolRuntime,
        status_filter: Annotated[
            str | None,
            "Filter by status: 'running', 'success', 'error', 'cancelled', or 'all' (default).",
        ] = None,
    ) -> str:
        return _NO_LOOP_MSG

    async def alist_agent_tasks(
        runtime: ToolRuntime,
        status_filter: Annotated[
            str | None,
            "Filter by status: 'running', 'success', 'error', 'cancelled', or 'all' (default).",
        ] = None,
    ) -> str | Command:
        tasks: dict[str, LocalAsyncTask] = (runtime.state or {}).get("local_async_tasks") or {}
        if not tasks:
            return "No background agent tasks tracked."

        # Filter
        if status_filter and status_filter != "all":
            filtered = [t for t in tasks.values() if t["status"] == status_filter]
        else:
            filtered = list(tasks.values())

        if not filtered:
            return f"No tasks with status '{status_filter}'."

        now = _now()
        updated_tasks: dict[str, LocalAsyncTask] = {}
        entries: list[str] = []

        for tracked in filtered:
            tid = tracked["task_id"]
            status, _ = _get_live_status(tid, tracked)
            line = f"  task_id: {tid}  agent: {tracked['agent_name']}  status: {status}"
            if status in ("success", "error"):
                line += f"  output: {tracked['output_file']}"
            entries.append(line)
            updated_tasks[tid] = LocalAsyncTask(
                task_id=tracked["task_id"],
                agent_name=tracked["agent_name"],
                status=status,
                created_at=tracked["created_at"],
                last_checked_at=now,
                output_file=tracked["output_file"],
            )

        msg = f"{len(entries)} background task(s):\n" + "\n".join(entries)
        return Command(
            update={
                "messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id)],
                "local_async_tasks": updated_tasks,
            }
        )

    return StructuredTool.from_function(
        name="list_agent_tasks",
        func=list_agent_tasks,
        coroutine=alist_agent_tasks,
        description=(
            "List all tracked background agent tasks with their current status. "
            "Completed tasks (success/error) include the output file path. "
            "Use check_agent_task with the task_id to read the full output inline, "
            "or use the bash tool to cat the output file directly for large results. "
            "Use status_filter to narrow to 'running', 'success', 'error', or 'cancelled'."
        ),
    )


def _build_cancel_tool() -> StructuredTool:
    def cancel_agent_task(
        task_id: Annotated[str, "The exact task_id returned by start_agent_task or start_parallel_agents."],
        runtime: ToolRuntime,
    ) -> str:
        return _NO_LOOP_MSG

    async def acancel_agent_task(
        task_id: Annotated[str, "The exact task_id returned by start_agent_task or start_parallel_agents."],
        runtime: ToolRuntime,
    ) -> str | Command:
        tracked = _resolve_tracked(task_id, runtime)
        if isinstance(tracked, str):
            return tracked

        if tracked["status"] in _TERMINAL_STATUSES:
            return f"Task {task_id} is already in terminal state '{tracked['status']}' — nothing to cancel."

        asyncio_task = _TASK_REGISTRY.get(task_id)
        if asyncio_task and not asyncio_task.done():
            asyncio_task.cancel()

        # Immediately remove from live registries and push a cancelled notification
        # so the TUI panel transitions from yellow→red right away, without waiting
        # for _on_done to fire (which can be delayed if the task is stuck in a
        # blocking LLM call or subprocess and takes time to handle CancelledError).
        agent_name = tracked.get("agent_name", task_id[:8])
        with _NOTIF_LOCK:
            _TASK_REGISTRY.pop(task_id, None)
            _TASK_AGENT_NAMES.pop(task_id, None)
            _TASK_OUTPUT_QUEUES.pop(task_id, None)
            _MANUALLY_CANCELLED.add(task_id)
            _PENDING_NOTIFICATIONS[task_id] = {
                "agent_name": agent_name,
                "status": "cancelled",
                "output": "",
                "output_file": tracked.get("output_file", ""),
            }

        now = _now()
        updated = LocalAsyncTask(
            task_id=tracked["task_id"],
            agent_name=tracked["agent_name"],
            status="cancelled",
            created_at=tracked["created_at"],
            last_checked_at=now,
            output_file=tracked["output_file"],
        )
        msg = f"Cancelled background agent task: {task_id}"
        return Command(
            update={
                "messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id)],
                "local_async_tasks": {task_id: updated},
            }
        )

    return StructuredTool.from_function(
        name="cancel_agent_task",
        func=cancel_agent_task,
        coroutine=acancel_agent_task,
        description="Cancel a running background agent task.",
    )


def _build_update_tool() -> StructuredTool:
    def update_agent_task(
        task_id: Annotated[str, "The exact task_id returned by start_agent_task or start_parallel_agents."],
        message: Annotated[str, "Follow-up message or instruction to send to the subagent."],
        runtime: ToolRuntime,
    ) -> str:
        return _NO_LOOP_MSG

    async def aupdate_agent_task(
        task_id: Annotated[str, "The exact task_id returned by start_agent_task or start_parallel_agents."],
        message: Annotated[str, "Follow-up message or question to send to the subagent."],
        runtime: ToolRuntime,
    ) -> str | Command:
        tracked = _resolve_tracked(task_id, runtime)
        if isinstance(tracked, str):
            return tracked

        # If the subagent is still mid-ainvoke, tell the parent to wait for it.
        asyncio_task = _TASK_REGISTRY.get(task_id)
        if asyncio_task and not asyncio_task.done():
            return (
                f"Task {task_id} is still running. "
                "Call get_agent_response to wait for the current turn to complete, "
                "then call update_agent_task with your follow-up."
            )

        # Subagent has completed its turn — re-launch from LangGraph checkpoint.
        # Empty state {} forces LangGraph to load the checkpoint for thread_id=task_id
        # and append the new HumanMessage via the add_messages reducer.
        # Full conversation history (all prior turns) is preserved.
        runnable_info = _TASK_RUNNABLES.get(task_id)
        if not runnable_info:
            return (
                f"Cannot restart task {task_id}: runnable not cached "
                "(task may be from a previous process restart)."
            )
        runnable, timeout = runnable_info
        out_path = Path(tracked["output_file"])
        try:
            out_path.write_text(
                json.dumps({"status": "running", "output": "", "task_id": task_id}),
                encoding="utf-8",
            )
        except OSError as exc:
            return f"Could not reset output file {out_path}: {exc}"

        _launch_task(runnable, {}, task_id, out_path, initial_message=message, agent_name=tracked["agent_name"], timeout=timeout)

        now = _now()
        updated = LocalAsyncTask(
            task_id=tracked["task_id"],
            agent_name=tracked["agent_name"],
            status="running",
            created_at=tracked["created_at"],
            last_checked_at=now,
            output_file=tracked["output_file"],
        )
        msg = (
            f"Follow-up delivered — agent relaunched from checkpoint.\n"
            f"task_id: {task_id}\n"
            f"Call get_agent_response to wait for the reply."
        )
        return Command(
            update={
                "messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id)],
                "local_async_tasks": {task_id: updated},
            }
        )

    return StructuredTool.from_function(
        name="update_agent_task",
        func=update_agent_task,
        coroutine=aupdate_agent_task,
        description=(
            "Send a follow-up message or question to a subagent after its turn completes. "
            "The subagent resumes from its LangGraph checkpoint — full conversation history "
            "is preserved. Always call get_agent_response after this to wait for the reply. "
            "If the subagent is still running, call get_agent_response first to drain the "
            "current turn, then call update_agent_task."
        ),
    )


def _build_get_response_tool() -> StructuredTool:
    def get_agent_response(
        task_id: Annotated[str, "The exact task_id returned by start_agent_task or start_parallel_agents."],
        runtime: ToolRuntime,
        timeout_seconds: Annotated[int, "Seconds to wait for a response (default 120). Increase for long-running tasks."] = 120,
    ) -> str:
        return _NO_LOOP_MSG

    async def aget_agent_response(
        task_id: Annotated[str, "The exact task_id returned by start_agent_task or start_parallel_agents."],
        runtime: ToolRuntime,
        timeout_seconds: Annotated[int, "Seconds to wait for a response (default 120). Increase for long-running tasks."] = 120,
    ) -> str | Command:
        tracked = _resolve_tracked(task_id, runtime)
        if isinstance(tracked, str):
            return tracked

        now = _now()
        out_q = _TASK_OUTPUT_QUEUES.get(task_id)

        if out_q is None:
            # Queue already cleaned — task completed; fall back to output file.
            data = _read_output_file(Path(tracked["output_file"]))
            output = data.get("output", "")
            if output:
                updated = LocalAsyncTask(
                    task_id=tracked["task_id"],
                    agent_name=tracked["agent_name"],
                    status=data.get("status", tracked["status"]),
                    created_at=tracked["created_at"],
                    last_checked_at=now,
                    output_file=tracked["output_file"],
                )
                return Command(
                    update={
                        "messages": [ToolMessage(output, tool_call_id=runtime.tool_call_id)],
                        "local_async_tasks": {task_id: updated},
                    }
                )
            return f"No pending response for task {task_id}. Use check_agent_task to see its status."

        try:
            response = await asyncio.wait_for(out_q.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return (
                f"No response received within {timeout_seconds}s — "
                "agent is still working. Call get_agent_response again with a longer timeout."
            )

        updated = LocalAsyncTask(
            task_id=tracked["task_id"],
            agent_name=tracked["agent_name"],
            status=tracked["status"],
            created_at=tracked["created_at"],
            last_checked_at=now,
            output_file=tracked["output_file"],
        )
        return Command(
            update={
                "messages": [ToolMessage(response, tool_call_id=runtime.tool_call_id)],
                "local_async_tasks": {task_id: updated},
            }
        )

    return StructuredTool.from_function(
        name="get_agent_response",
        func=get_agent_response,
        coroutine=aget_agent_response,
        description=(
            "Wait for and return the subagent's reply after calling update_agent_task "
            "on a completed turn. Blocks until a response arrives or timeout_seconds "
            "elapses. Increase timeout_seconds for long-running tasks."
        ),
    )


def _build_progress_tool() -> StructuredTool:
    def get_task_progress(
        task_id: Annotated[str, "The exact task_id of the running background task."],
        runtime: ToolRuntime,
    ) -> str:
        return _NO_LOOP_MSG

    async def aget_task_progress(
        task_id: Annotated[str, "The exact task_id of the running background task."],
        runtime: ToolRuntime,
    ) -> str:
        tracked = _resolve_tracked(task_id, runtime)
        if isinstance(tracked, str):
            return tracked

        asyncio_task = _TASK_REGISTRY.get(task_id)
        if not asyncio_task or asyncio_task.done():
            return (
                f"Task {task_id} has already completed. "
                "Use check_agent_task to read its final output, or "
                "update_agent_task to send a follow-up."
            )

        runnable_info = _TASK_RUNNABLES.get(task_id)
        if not runnable_info:
            return f"No runnable cached for task {task_id}."
        runnable, _ = runnable_info

        if not hasattr(runnable, "aget_state"):
            return f"Task {task_id} runnable does not support live checkpoint reads."

        try:
            snapshot = await runnable.aget_state(
                config={"configurable": {"thread_id": task_id}}
            )
        except Exception as exc:
            return f"Could not read checkpoint for task {task_id}: {exc}"

        if not snapshot or not snapshot.values:
            return (
                f"Task {task_id} is running but no checkpoint written yet "
                "(subagent still initializing — try again in a moment)."
            )

        messages = snapshot.values.get("messages", [])
        if not messages:
            return f"Task {task_id} is running but no messages in checkpoint yet."

        def _extract_text(content: Any) -> str:
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                )
            return str(content)

        def _truncate(text: str, max_chars: int = 80) -> str:
            text = text.strip()
            lines = text.splitlines()
            first = lines[0][:max_chars] if lines else ""
            extra_lines = len(lines) - 1
            if extra_lines > 0:
                return f"{first}  (+{extra_lines} lines)"
            if len(text) > max_chars:
                return text[:max_chars] + "…"
            return text

        # Write a truncated progress file — all messages, but capped per entry.
        # Tool results: 200 chars + line count. AI text: 150 chars. Args: 120 chars.
        # Never writes raw full outputs — keeps the file readable at a glance.
        dump_path = Path(f"/tmp/rai_progress_{task_id[:12]}.txt")
        try:
            dump_lines: list[str] = [
                f"Progress dump — task {task_id} ({tracked['agent_name']})",
                f"Messages: {len(messages)}",
                "",
            ]
            for i, msg in enumerate(messages):
                msg_type = type(msg).__name__
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        name = tc.get("name", "?") if isinstance(tc, dict) else getattr(tc, "name", "?")
                        args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                        dump_lines.append(f"[{i}] → {name}({_truncate(str(args), 120)})")
                else:
                    raw = _extract_text(getattr(msg, "content", ""))
                    if not raw.strip():
                        continue
                    if msg_type == "ToolMessage":
                        dump_lines.append(f"[{i}] ← result: {_truncate(raw, 200)}")
                    elif msg_type == "HumanMessage":
                        dump_lines.append(f"[{i}] task: {_truncate(raw, 150)}")
                    else:
                        dump_lines.append(f"[{i}] ai: {_truncate(raw, 150)}")
            dump_path.write_text("\n".join(dump_lines), encoding="utf-8")
        except OSError:
            dump_path = None  # type: ignore[assignment]

        # Inline summary — last 6 messages, aggressively truncated (80 chars).
        # Dump file has all messages at slightly longer truncation (200 chars).
        # Neither contains full raw output — both are progress-readable only.
        recent = messages[-6:]
        lines = [
            f"Progress — task {task_id[:12]} (agent: {tracked['agent_name']}) "
            f"| {len(messages)} messages so far",
        ]
        for msg in recent:
            msg_type = type(msg).__name__
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "?") if isinstance(tc, dict) else getattr(tc, "name", "?")
                    args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                    args_str = _truncate(str(args), 80)
                    lines.append(f"  → calling {name}({args_str})")
                continue
            raw = _extract_text(getattr(msg, "content", ""))
            if not raw.strip():
                continue
            if msg_type == "ToolMessage":
                lines.append(f"  ← result: {_truncate(raw, 80)}")
            elif msg_type == "HumanMessage":
                lines.append(f"  task: {_truncate(raw, 80)}")
            else:
                # AIMessage reasoning / final text
                lines.append(f"  ai: {_truncate(raw, 100)}")

        if snapshot.next:
            lines.append(f"  now: {list(snapshot.next)}")
        if dump_path:
            lines.append(f"Full details: {dump_path}")

        return "\n".join(lines)

    return StructuredTool.from_function(
        name="get_task_progress",
        func=get_task_progress,
        coroutine=aget_task_progress,
        description=(
            "Read the live LangGraph checkpoint of a running background task to see its "
            "current progress — every LLM call and tool result the subagent has made so far. "
            "Safe to call at any time while the task is running; does not interrupt it. "
            "LangGraph writes to the checkpoint after every node, so this is always up-to-date."
        ),
    )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
## Background & Parallel Agent Tools

Use `start_agent_task`, `start_parallel_agents`, or `start_pipeline` to launch subagents \
without blocking. The tool returns a task_id (or pipeline_id) immediately.

Rules:
- Use `start_pipeline` when tasks have dependencies (e.g. recon → exploit → report). \
It runs independent tasks in parallel within each dependency level, and injects prior \
results into dependent tasks automatically.
- Use `start_parallel_agents` for independent tasks with no ordering requirements.
- Use `check_agent_task` to read a task's output by task_id.
- Use `list_agent_tasks` to see all tasks, or after context compaction to recall task_ids.
- Use `cancel_agent_task` to stop a task no longer needed.
- Use `get_task_progress` at any time while a task is running to read its live LangGraph \
checkpoint — every LLM call and tool result so far, without interrupting the task.
- Use `update_agent_task` to send a follow-up message to a subagent after its turn \
completes. The subagent resumes from its checkpoint (full history preserved) and replies.
- Use `get_agent_response` to wait (blocking) for the subagent's reply after \
`update_agent_task`, or to drain the output of a completed turn (default 120s timeout).
- If `update_agent_task` says the task is still running, call `get_agent_response` first \
to drain the current turn, then send your follow-up.

Communication rules (IMPORTANT):
- NEVER mention tool names (start_agent_task, start_parallel_agents, start_pipeline, etc.) in your responses to the user.
- Use natural language: "I launched a background researcher agent", "the pipeline completed", etc.

When you see a message starting with [Background agent turn completed], \
[Background agent completed], or [Pipeline ...], it contains results from subagents \
you spawned — read the output and incorporate it into your work.

Available subagents: {agents_desc}"""


class LocalAsyncAgentMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Middleware that adds non-blocking local background/parallel agent tools.

    Unlike SubAgentMiddleware (blocking task tool), these tools use
    asyncio.create_task() to launch subagents without blocking the core agent.
    Task metadata is persisted in LangGraph state under 'local_async_tasks'.
    Output is written to /tmp/rai_task_<id>.json when the task completes.

    Tools added:
      - start_agent_task      — single non-blocking subagent launch
      - start_parallel_agents — multiple non-blocking launches at once
      - start_pipeline        — DAG-ordered pipeline with depends_on batching
      - check_agent_task      — poll status + get output
      - list_agent_tasks      — list all tracked tasks
      - cancel_agent_task     — cancel a running task
      - get_task_progress     — read live checkpoint of a running task
      - update_agent_task     — send follow-up (checkpoint resume)
      - get_agent_response    — block until subagent replies
    """

    state_schema = LocalAsyncAgentState

    def __init__(
        self,
        *,
        subagents: Sequence[dict[str, Any]],
        default_tools: Sequence[BaseTool],
        default_model: BaseChatModel | str,
        backend: Any,
        checkpointer: BaseCheckpointSaver | None = None,
        parent_api_key: str = "",
        parent_base_url: str = "",
        default_task_timeout: int = _DEFAULT_TASK_TIMEOUT,
    ) -> None:
        super().__init__()

        # Compile subagent runnables with the same middleware stack that
        # create_deep_agent (graph.py) builds for blocking task-tool subagents:
        #   TodoListMiddleware
        #   FilesystemMiddleware(backend)          ← file read/write/edit
        #   SummarizationMiddleware                ← context compaction
        #   PatchToolCallsMiddleware               ← fix malformed tool calls
        #   spec["middleware"]                     ← EmptyContentSanitizerMiddleware
        #                                            (injected by _postprocess_subagents)
        #   RAIPromptCachingMiddleware
        subagent_graphs: dict[str, Runnable] = {}
        descriptions: list[str] = []

        for spec in subagents:
            name = spec.get("name", "")
            if not name:
                continue
            description = spec.get("description", "")
            system_prompt = spec.get("system_prompt", "")
            tools = spec.get("tools", list(default_tools))
            model = spec.get("model", default_model)
            from langchain_core.language_models import BaseChatModel as _BCM
            if not isinstance(model, _BCM):
                from rai.engine.model import _build_llm as _rai_build
                try:
                    model = _rai_build(model, api_key=parent_api_key, base_url=parent_base_url)
                except Exception:
                    model = default_model

            try:
                _summ_mw: Any = SummarizationMiddleware(
                    model=model,
                    backend=backend,
                    trigger=("fraction", 0.90),
                    keep=("fraction", 0.70),
                )
            except Exception:
                _summ_mw = None
            subagent_middleware: list[Any] = [
                TodoListMiddleware(),
                FilesystemMiddleware(backend=backend),
                *([_summ_mw] if _summ_mw is not None else []),
                PatchToolCallsMiddleware(),
            ]
            # Skills — mirrors create_deep_agent (graph.py) logic
            subagent_skills = spec.get("skills")
            if subagent_skills:
                subagent_middleware.append(
                    SkillsMiddleware(backend=backend, sources=subagent_skills)
                )
            # spec["middleware"] contains EmptyContentSanitizerMiddleware added
            # by _postprocess_subagents in agent.py — include it here so Bedrock
            # doesn't reject empty content blocks inside background agents.
            subagent_middleware.extend(spec.get("middleware", []))
            subagent_middleware.append(
                RAIPromptCachingMiddleware(unsupported_model_behavior="ignore")
            )

            try:
                runnable = create_agent(
                    model,
                    system_prompt=system_prompt,
                    tools=list(tools),
                    middleware=subagent_middleware,
                    name=name,
                    checkpointer=checkpointer,
                )
                subagent_graphs[name] = runnable
                descriptions.append(f"- {name}: {description}")
            except Exception as exc:
                logger.warning(
                    "LocalAsyncAgentMiddleware: could not compile subagent '%s': %s — skipping",
                    name,
                    exc,
                )

        if not subagent_graphs:
            logger.warning("LocalAsyncAgentMiddleware: no subagents compiled — tools will be no-ops")

        self.tools: list[StructuredTool] = [
            _build_start_tool(subagent_graphs),
            _build_parallel_tool(subagent_graphs),
            _build_pipeline_tool(subagent_graphs),
            _build_check_tool(),
            _build_list_tool(),
            _build_cancel_tool(),
            _build_progress_tool(),
            _build_update_tool(),
            _build_get_response_tool(),
        ]

        agents_desc = "\n".join(descriptions) if descriptions else "(none)"
        self._system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(agents_desc=agents_desc)

    @staticmethod
    def _pop_notification_text() -> str:
        """Consume all pending notifications and return them as a single text block.

        Returns empty string if nothing is pending.
        Delegates to the module-level pop_pending_notification_text().
        """
        with _NOTIF_LOCK:
            count = len(_PENDING_NOTIFICATIONS)
            if count > 10:
                logger.warning(
                    "LocalAsyncAgentMiddleware: %d pending notifications — "
                    "subagents are completing faster than the agent is processing them",
                    count,
                )
        return pop_pending_notification_text()

    def _apply_notifications(
        self,
        request: ModelRequest[ContextT],
        new_sys: str,
        notif_text: str,
    ) -> ModelRequest[ContextT]:
        """Inject notification text without creating consecutive HumanMessages.

        Anthropic's API rejects two consecutive HumanMessages. Strategy:
        - Last message is NOT HumanMessage (e.g. ToolMessage after a tool call):
            append a HumanMessage — alternation is valid, LLM sees it as a
            conversation event and responds naturally.
        - Last message IS HumanMessage (user just typed something):
            fold the notification into the system message for this one call only
            (already popped from _PENDING_NOTIFICATIONS so it never repeats).
        """
        messages = list(request.messages)
        if messages and isinstance(messages[-1], HumanMessage):
            # Fold into system message — one-time, already consumed, never repeats.
            new_sys = append_to_system_message(new_sys, notif_text)
            return request.override(system_message=new_sys)
        else:
            # Safe to append HumanMessage — maintains human/tool alternation
            messages.append(HumanMessage(content=notif_text))
            return request.override(system_message=new_sys, messages=messages)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        new_sys = append_to_system_message(request.system_message, self._system_prompt)
        notif_text = self._pop_notification_text()
        if notif_text:
            request = self._apply_notifications(request, new_sys, notif_text)
        else:
            request = request.override(system_message=new_sys)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        new_sys = append_to_system_message(request.system_message, self._system_prompt)
        notif_text = self._pop_notification_text()
        if notif_text:
            request = self._apply_notifications(request, new_sys, notif_text)
        else:
            request = request.override(system_message=new_sys)
        return await handler(request)
