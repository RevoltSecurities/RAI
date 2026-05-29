"""HTTP run executor — drives agent turns via SDK RunableAgent with SSE publishing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from rai.agents.background import (
    _NOTIF_LOCK,
    _PENDING_NOTIFICATIONS,
    _TASK_AGENT_NAMES,
    _TASK_REGISTRY,
    _TERMINAL_STATUSES,
)
from rai.harness.sse import RunEventBus, ThreadNotifBus
from rai.harness.subagents.registry import (
    _RUN_CONTEXT, RunContext,
    _SUBAGENT_REGISTRY, _SUBAGENT_BUSES, _SUBAGENT_LOCK, SubagentMeta,
)

logger = logging.getLogger(__name__)

_KEEPALIVE_INTERVAL = 15.0  # seconds between run_keepalive SSE events

# Process-local run registry: run_id → metadata dict
_RUN_REGISTRY: dict[str, dict[str, Any]] = {}

# Per-run asyncio.Task references for cancellation support
_RUN_TASKS: dict[str, asyncio.Task] = {}

# HITL futures: run_id → Future[dict] set by POST /threads/{id}/interrupt
_HITL_FUTURES: dict[str, asyncio.Future[dict[str, Any]]] = {}

# Session-approved tools per thread: thread_id → set[tool_name]
# Tools added via approve_for_session decision auto-bypass future interrupts for the thread lifetime.
_SESSION_APPROVED: dict[str, set[str]] = {}

# Plan futures: run_id → Future[dict] set by POST /agents/{name}/runs/{id}/plan/approve|reject
_PLAN_FUTURES: dict[str, asyncio.Future[dict[str, Any]]] = {}

# Ask-user futures: run_id → Future[dict] set by POST /threads/{id}/ask_user
_ASK_USER_FUTURES: dict[str, asyncio.Future[dict[str, Any]]] = {}

_TASK_SPAWNING_TOOLS = frozenset({
    "start_agent_task",
    "start_parallel_agents",
    "start_pipeline",
})

_DEFAULT_TIMEOUT = 3600.0
# Fallback poll interval — the notification monitor fires much faster (50ms)
_WATCHER_POLL = 2.0


def generate_run_id() -> str:
    return uuid.uuid4().hex


def get_run(run_id: str) -> dict[str, Any] | None:
    return _RUN_REGISTRY.get(run_id)


def list_runs_for_thread(thread_id: str) -> list[dict[str, Any]]:
    return [r for r in _RUN_REGISTRY.values() if r.get("thread_id") == thread_id]


async def _notification_monitor(notif_event: asyncio.Event, stop_flag: asyncio.Event) -> None:
    """Concurrent coroutine: sets notif_event within 50ms of any new notification.

    Tracks _PENDING_NOTIFICATIONS and _TASK_REGISTRY separately so that the
    atomic transition of a task_id from TASK_REGISTRY → PENDING_NOTIFICATIONS
    (which leaves the union set unchanged) is still detected and fires the event.
    """
    seen_pending: frozenset[str] = frozenset()
    seen_active: frozenset[str] = frozenset()
    while not stop_flag.is_set():
        with _NOTIF_LOCK:
            cur_pending = frozenset(_PENDING_NOTIFICATIONS.keys())
            cur_active = frozenset(_TASK_REGISTRY.keys())
        if cur_pending != seen_pending or cur_active != seen_active:
            seen_pending = cur_pending
            seen_active = cur_active
            notif_event.set()
        await asyncio.sleep(0.05)


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Return True when exc represents an HTTP 429 / rate-limit response."""
    # Status-code attribute (httpx, requests, openai, anthropic SDKs all set this)
    if getattr(exc, "status_code", None) == 429:
        return True
    # Nested .response.status_code (httpx HTTPStatusError)
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 429:
        return True
    # Class name heuristic — litellm.RateLimitError, openai.RateLimitError, etc.
    cls_name = type(exc).__name__.lower()
    if "ratelimit" in cls_name:
        return True
    # Fallback: check string representation
    msg = str(exc).lower()
    return "rate_limit" in msg or "rate limit" in msg or " 429" in msg


async def execute_run(
    run_id: str,
    thread_id: str,
    agent_name: str,
    runnable: Any,          # RunableAgent — per-run SDK wrapper sharing compiled graph
    input_message: str,
    bus: RunEventBus,
    notif_bus: ThreadNotifBus,
    timeout: float = _DEFAULT_TIMEOUT,
    metadata: "dict[str, Any] | None" = None,
    model_hint: str = "",   # pre-seeded from builder config; updated from first LLM event
    allowed_tools: "list[str] | None" = None,  # B3 — soft tool whitelist
    max_turns: "int | None" = None,            # B6 — turn cap
    checkpointer: "Any | None" = None,         # passed to subagent tools via context var
    recursion_limit: int = 100,                # LangGraph recursion limit for this run
    plan_mode: bool = False,                   # When True, inject preamble + block at write_plan()
    self_learn: bool = False,                  # When True, post-run rule-based lessons extracted
) -> None:
    """Execute one agent run, publishing all lifecycle events to the SSE bus.

    Uses the SDK's RunableAgent.stream() for live token/tool streaming and drives
    the inline SubagentWatcher loop so every re-invocation is also fully streamed.
    """
    run_start_time = datetime.now(UTC)
    _ctx_token = None  # contextvars token for _RUN_CONTEXT — reset in finally
    # Mutable counters — mutated inside _stream_once via nonlocal
    _num_turns: list[int] = [0]  # list to allow nonlocal mutation in nested function
    _first_token_ms: list[float | None] = [None]
    _usage_accum: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
    }
    _last_stop_reason: list[str] = ["end_turn"]
    _model_usage: dict[str, dict[str, int]] = {}  # model_id → per-model token counts (A8)
    _request_count: list[int] = [0]               # LLM API calls (may exceed num_turns)
    _detected_model: list[str] = [model_hint]     # first-seen model name from LLM events
    _hit_max_turns: list[bool] = [False]           # B6 — set when max_turns is reached

    _RUN_REGISTRY[run_id] = {
        "run_id": run_id,
        "thread_id": thread_id,
        "agent_name": agent_name,
        "status": "running",
        "input": input_message,
        "output": None,
        "created_at": run_start_time.isoformat(),
        "metadata": metadata or {},
        "model": model_hint,
    }
    if plan_mode:
        _RUN_REGISTRY[run_id]["plan_mode"] = True
        _RUN_REGISTRY[run_id]["plan_approved"] = False
    if self_learn:
        _RUN_REGISTRY[run_id]["self_learn"] = True

    notif_event = asyncio.Event()
    stop_flag = asyncio.Event()
    monitor_task = asyncio.create_task(
        _notification_monitor(notif_event, stop_flag),
        name=f"notif-monitor-{run_id[:8]}",
    )

    # C5 — run_keepalive heartbeat every 15s
    async def _keepalive_loop() -> None:
        while not stop_flag.is_set():
            try:
                await asyncio.wait_for(stop_flag.wait(), timeout=_KEEPALIVE_INTERVAL)
            except asyncio.TimeoutError:
                pass
            if stop_flag.is_set():
                break
            elapsed = int((datetime.now(UTC) - run_start_time).total_seconds() * 1000)
            await bus.publish("run_keepalive", {
                "run_id": run_id,
                "elapsed_ms": elapsed,
                "status": "running",
            })

    keepalive_task = asyncio.create_task(
        _keepalive_loop(),
        name=f"keepalive-{run_id[:8]}",
    )

    try:
        await bus.publish("run_start", {
            "run_id": run_id,
            "thread_id": thread_id,
            "agent_name": agent_name,
            "input": input_message,
            "model": model_hint,
        })

        async def _stream_once(msg: "str | Command") -> None:
            """Stream one agent turn via SDK and publish SSE events for each chunk.

            Accepts a plain string prompt, a LangGraph Command (for HITL resume),
            or an empty string (checkpoint-resume after watcher re-invoke).
            """
            _num_turns[0] += 1
            loop_start_ms = asyncio.get_event_loop().time() * 1000

            with _NOTIF_LOCK:
                pre_task_ids = set(_TASK_REGISTRY.keys())

            try:
                if isinstance(msg, Command):
                    # HITL resume — stream the continuation from the Command
                    event_stream = runnable.graph.astream_events(
                        msg, runnable._config, version="v2"
                    )
                elif msg:
                    event_stream = runnable.stream(msg)
                else:
                    event_stream = runnable.graph.astream_events(
                        {}, runnable._config, version="v2"
                    )

                async for event in event_stream:
                    kind: str = event["event"]

                    if kind == "on_chat_model_stream":
                        chunk = event["data"]["chunk"]
                        content = getattr(chunk, "content", "")
                        # Track time-to-first-token (A6)
                        if content and _first_token_ms[0] is None:
                            _first_token_ms[0] = asyncio.get_event_loop().time() * 1000 - loop_start_ms
                        await _publish_content_blocks(bus, thread_id, content)

                    elif kind == "on_chat_model_end":
                        # One completed LLM API call — increment request counter
                        _request_count[0] += 1

                        output_msg = event["data"].get("output")
                        if output_msg is not None:
                            # ── Token accumulation (A7) ──────────────────────
                            usage = getattr(output_msg, "usage_metadata", None) or {}
                            in_toks = usage.get("input_tokens", 0)
                            out_toks = usage.get("output_tokens", 0)
                            total_toks = usage.get("total_tokens", 0)
                            if in_toks or out_toks:
                                _usage_accum["input_tokens"] += in_toks
                                _usage_accum["output_tokens"] += out_toks
                            elif total_toks:
                                _usage_accum["input_tokens"] += total_toks
                            details = usage.get("input_token_details", {}) or {}
                            _usage_accum["cache_creation_tokens"] += details.get("cache_creation", 0)
                            _usage_accum["cache_read_tokens"] += details.get("cache_read", 0)

                            # ── Model detection (priority order) ─────────────
                            # 1. ls_model_name — LangChain standard set by all ChatModel
                            #    implementations, regardless of provider
                            # 2. response_metadata.model_id — Anthropic SDK
                            # 3. response_metadata.model — OpenAI SDK
                            # 4. response_metadata.model_name — LiteLLM fallback
                            meta = getattr(output_msg, "response_metadata", {}) or {}
                            resolved_model = (
                                (event.get("metadata") or {}).get("ls_model_name")
                                or meta.get("model_id")
                                or meta.get("model")
                                or meta.get("model_name")
                                or ""
                            )
                            if resolved_model and not _detected_model[0]:
                                _detected_model[0] = resolved_model
                                _RUN_REGISTRY[run_id]["model"] = resolved_model

                            # ── Per-model breakdown (A8) ─────────────────────
                            key = resolved_model or _detected_model[0]
                            if key:
                                entry = _model_usage.setdefault(key, {
                                    "input_tokens": 0, "output_tokens": 0,
                                    "cache_creation_tokens": 0, "cache_read_tokens": 0,
                                    "request_count": 0,
                                })
                                entry["input_tokens"] += in_toks or total_toks
                                entry["output_tokens"] += out_toks
                                entry["cache_creation_tokens"] += details.get("cache_creation", 0)
                                entry["cache_read_tokens"] += details.get("cache_read", 0)
                                entry["request_count"] += 1

                            # ── Stop reason (A1) ─────────────────────────────
                            sr = meta.get("stop_reason") or meta.get("finish_reason")
                            if sr:
                                _last_stop_reason[0] = sr

                    elif kind == "on_tool_start":
                        tool_name = event["name"]
                        await bus.publish("tool_start", {
                            "thread_id": thread_id,
                            "tool_name": tool_name,
                            "tool_input": event["data"].get("input"),
                        })
                        # B3 — emit permission_denied when tool is not in whitelist
                        if allowed_tools is not None and tool_name not in allowed_tools:
                            await bus.publish("permission_denied", {
                                "thread_id": thread_id,
                                "tool_name": tool_name,
                                "reason": "tool_not_in_allowed_list",
                                "allowed_tools": allowed_tools,
                            })

                    elif kind == "on_tool_end":
                        tool_output = event["data"].get("output", "")
                        await bus.publish("tool_end", {
                            "thread_id": thread_id,
                            "tool_name": event["name"],
                            "tool_output": str(tool_output)[:500],
                        })

                        if event["name"] in _TASK_SPAWNING_TOOLS:
                            with _NOTIF_LOCK:
                                current_ids = set(_TASK_REGISTRY.keys())
                                agent_names_snap = dict(_TASK_AGENT_NAMES)
                            new_task_ids = current_ids - pre_task_ids

                            if event["name"] == "start_pipeline":
                                try:
                                    snapshot = await runnable.get_state()
                                    tasks_state: dict = snapshot.values.get("local_async_tasks", {})
                                    pipeline_tasks = []
                                    pipeline_id: str | None = None
                                    for tid in new_task_ids:
                                        t = tasks_state.get(tid, {})
                                        pipeline_id = pipeline_id or t.get("pipeline_id")
                                        pipeline_tasks.append({
                                            "task_id": tid,
                                            "label": t.get("label"),
                                            "depends_on": t.get("depends_on", []),
                                        })
                                    if pipeline_id:
                                        await bus.publish("pipeline_created", {
                                            "thread_id": thread_id,
                                            "pipeline_id": pipeline_id,
                                            "tasks": pipeline_tasks,
                                        })
                                except Exception:
                                    pass
                            else:
                                for new_tid in new_task_ids:
                                    await bus.publish("task_created", {
                                        "thread_id": thread_id,
                                        "task_id": new_tid,
                                        "agent_name": agent_names_snap.get(new_tid, ""),
                                    })

                            pre_task_ids = current_ids

            except Exception as exc:
                logger.warning("Stream error in run %s: %s", run_id, exc)
                raise  # outer execute_run handler publishes the error event

        # Set context var so HTTP subagent tools (http_spawn_agent etc.) know which
        # run's bus and checkpointer to use — each asyncio.Task gets its own copy
        try:
            from rai.config.agent import load_agent_config as _load_cfg
            _pcfg = _load_cfg(agent_name)
            _parent_api_key  = (_pcfg.api_key  if _pcfg else "") or ""
            _parent_base_url = (_pcfg.base_url if _pcfg else "") or ""
        except Exception:
            _parent_api_key = _parent_base_url = ""

        _ctx_token = _RUN_CONTEXT.set(RunContext(
            run_id=run_id,
            thread_id=thread_id,
            parent_bus=bus,
            checkpointer=checkpointer,
            parent_api_key=_parent_api_key,
            parent_base_url=_parent_base_url,
            agent_name=agent_name,
        ))

        # Propagate recursion_limit into the per-run runnable's config so all
        # three streaming paths in _stream_once() see it (Command, stream, checkpoint).
        if isinstance(getattr(runnable, "_config", None), dict):
            runnable._config["recursion_limit"] = recursion_limit

        # ── Plan mode preamble injection ──────────────────────────────────────
        if plan_mode:
            _RUN_REGISTRY[run_id]["status"] = "planning"
            input_message = (
                "[PLAN MODE ACTIVE]\n"
                "Research and plan ONLY. Use read-only tools (Read, Glob, Grep, "
                "WebSearch, WebFetch, GET requests). Do NOT write files, execute "
                "code, or make mutating API calls.\n"
                "When you have a complete plan, call write_plan(content) to submit "
                "it for approval. After approval, execute the plan step by step "
                "exactly as written.\n\n"
                f"User request:\n{input_message}"
            )

        # ── Initial turn ──────────────────────────────────────────────────────
        await _stream_once(input_message)

        # B6 — stop immediately if max_turns reached on the very first turn
        if max_turns is not None and _num_turns[0] >= max_turns:
            _hit_max_turns[0] = True

        # ── Inline SubagentWatcher loop ───────────────────────────────────────
        deadline = asyncio.get_event_loop().time() + timeout
        _deepagents_bridged: set[str] = set()  # task_ids mirrored → _SUBAGENT_REGISTRY

        while True:
            # B6 — hard turn cap: stop watching after max_turns is reached
            if _hit_max_turns[0]:
                break

            with _NOTIF_LOCK:
                notifs = dict(_PENDING_NOTIFICATIONS)
                active = dict(_TASK_REGISTRY)

            if not notifs and not active:
                break

            if asyncio.get_event_loop().time() > deadline:
                await bus.publish("error", {
                    "thread_id": thread_id,
                    "message": "Timed out waiting for background agents",
                    "traceback": "",
                })
                break

            for tid in list(active):
                await bus.publish("task_status", {
                    "thread_id": thread_id,
                    "task_id": tid,
                    "status": "running",
                    "agent_name": _TASK_AGENT_NAMES.get(tid, ""),
                })
                # Bridge: mirror deepagents tasks into _SUBAGENT_REGISTRY
                if tid not in _deepagents_bridged:
                    _deepagents_bridged.add(tid)
                    with _SUBAGENT_LOCK:
                        if tid not in _SUBAGENT_REGISTRY:
                            _SUBAGENT_REGISTRY[tid] = SubagentMeta(
                                task_id=tid,
                                agent_name=_TASK_AGENT_NAMES.get(tid, ""),
                                parent_run_id=run_id,
                                parent_thread_id=thread_id,
                                status="running",
                                created_at=datetime.now(UTC).isoformat(),
                                input="",
                                output=None,
                                output_file="",
                                label=None,
                                pipeline_id=None,
                                depends_on=None,
                            )
                            _SUBAGENT_BUSES[tid] = RunEventBus.create(tid)

            if notifs:
                for tid, notif in notifs.items():
                    payload = {
                        "thread_id": thread_id,
                        "task_id": tid,
                        "agent_name": notif.get("agent_name", ""),
                        "status": notif.get("status", ""),
                        "output_preview": notif.get("output", "")[:400],
                        "output_file": notif.get("output_file", ""),
                    }
                    await bus.publish("notification", payload)
                    await notif_bus.publish(thread_id, "notification", payload)

                    if notif.get("status") in _TERMINAL_STATUSES:
                        await bus.publish("task_completed", {
                            "thread_id": thread_id,
                            "task_id": tid,
                            "status": notif.get("status"),
                            "agent_name": notif.get("agent_name", ""),
                            "output": notif.get("output", ""),
                            "output_file": notif.get("output_file", ""),
                        })
                        await notif_bus.publish(thread_id, "task_completed", {
                            "task_id": tid,
                            "status": notif.get("status"),
                            "agent_name": notif.get("agent_name", ""),
                        })
                    elif ":" in tid:
                        parts = tid.split(":")
                        pid, key = parts[0], parts[1] if len(parts) > 1 else ""
                        if key.startswith("batch"):
                            await bus.publish("pipeline_batch", {
                                "thread_id": thread_id,
                                "pipeline_id": pid,
                                "batch_key": key,
                                "output_preview": notif.get("output", "")[:400],
                            })
                        elif key == "done":
                            await bus.publish("pipeline_end", {
                                "thread_id": thread_id,
                                "pipeline_id": pid,
                                "output": notif.get("output", ""),
                            })

                    # Bridge: upsert _SUBAGENT_REGISTRY entry for deepagents tasks.
                    # Fast tasks complete and are popped from _TASK_REGISTRY before
                    # the watcher's 'active' loop ever sees them — create the entry
                    # here from the notification so they always appear in /subagents.
                    _da_mapped = (
                        "completed" if notif.get("status") in ("success", "turn_complete") else
                        "failed" if notif.get("status") == "error" else
                        notif.get("status", "completed")
                    )
                    _sub_bus = None
                    with _SUBAGENT_LOCK:
                        _existing = _SUBAGENT_REGISTRY.get(tid)
                        if _existing is None:
                            # Task completed before bridge saw it in _TASK_REGISTRY.
                            _SUBAGENT_REGISTRY[tid] = SubagentMeta(
                                task_id=tid,
                                agent_name=notif.get("agent_name", ""),
                                parent_run_id=run_id,
                                parent_thread_id=thread_id,
                                status=_da_mapped,
                                created_at=datetime.now(UTC).isoformat(),
                                input="",
                                output=notif.get("output", ""),
                                output_file=notif.get("output_file", ""),
                                label=None,
                                pipeline_id=None,
                                depends_on=None,
                            )
                            _SUBAGENT_BUSES[tid] = RunEventBus.create(tid)
                            _deepagents_bridged.add(tid)
                        elif _existing.get("status") == "running":
                            _SUBAGENT_REGISTRY[tid] = {
                                **_existing,
                                "status": _da_mapped,
                                "output": notif.get("output", ""),
                                "output_file": notif.get("output_file", ""),
                            }
                        _sub_bus = _SUBAGENT_BUSES.get(tid)
                    if _sub_bus is not None:
                        await _sub_bus.publish("subagent_completed", {
                            "task_id": tid,
                            "agent_name": notif.get("agent_name", ""),
                            "status": _da_mapped,
                            "output_preview": notif.get("output", "")[:400],
                        })

                # Safety: don't inject consecutive HumanMessage
                try:
                    snapshot = await runnable.get_state()
                    # Enrich bridged entries with label/pipeline_id from local_async_tasks
                    local_tasks = snapshot.values.get("local_async_tasks") or {}
                    with _SUBAGENT_LOCK:
                        for lt_tid, lt in local_tasks.items():
                            _lt_existing = _SUBAGENT_REGISTRY.get(lt_tid)
                            if _lt_existing is not None and _lt_existing.get("label") is None and lt.get("label"):
                                _SUBAGENT_REGISTRY[lt_tid] = {
                                    **_lt_existing,
                                    "label": lt.get("label"),
                                    "pipeline_id": lt.get("pipeline_id"),
                                    "depends_on": lt.get("depends_on"),
                                }
                    last = (snapshot.values.get("messages") or [None])[-1]
                    _graph_interrupted = bool(snapshot.tasks and snapshot.tasks[0].interrupts)
                    if not isinstance(last, HumanMessage) and not _graph_interrupted:
                        # Build notification text (same format as pop_pending_notification_text)
                        notif_lines: list[str] = []
                        for tid, notif in notifs.items():
                            preview = (notif.get("output") or "(no output)")[:400]
                            status = notif.get("status", "")
                            agent_nm = notif.get("agent_name", "")
                            output_file = notif.get("output_file", "")
                            if status == "turn_complete":
                                notif_lines.append(
                                    f"[Background agent turn completed]\n"
                                    f"Agent: {agent_nm} | Task ID: {tid}\n"
                                    f"Output preview: {preview}\n"
                                    f"Use get_agent_response(task_id) to read the full reply, "
                                    f"or update_agent_task(task_id, message) to continue the conversation."
                                )
                            else:
                                notif_lines.append(
                                    f"[Background agent completed]\n"
                                    f"Agent: {agent_nm} | Task ID: {tid} | Status: {status}\n"
                                    f"Output preview: {preview}\n"
                                    f"Full output: {output_file}"
                                )
                        notif_text = "\n\n".join(notif_lines)
                        notif_text += "\n\nSummarise what the background agent(s) completed and their key results to the user — only if the outcome is relevant to the current task."
                        # Clear processed notifications before re-invocation
                        with _NOTIF_LOCK:
                            for tid in notifs:
                                _PENDING_NOTIFICATIONS.pop(tid, None)
                        await bus.publish("watcher_invoke", {
                            "thread_id": thread_id,
                            "reason": "notification_received",
                            "pending_count": len(notifs),
                        })
                        await _stream_once(notif_text)
                        # B6 — stop watcher loop if max_turns now reached
                        if max_turns is not None and _num_turns[0] >= max_turns:
                            _hit_max_turns[0] = True
                except Exception as exc:
                    logger.debug("Watcher re-invoke failed: %s", exc)
            else:
                # Fast wait: notif_event fires within 50ms of new activity
                notif_event.clear()
                try:
                    await asyncio.wait_for(notif_event.wait(), timeout=_WATCHER_POLL)
                except asyncio.TimeoutError:
                    pass

        # ── Outer recheck loop — HITL + step enforcement ─────────────────────
        # Wraps both the HITL loop and the step enforcement loop so that
        # HITL interrupts triggered during enforcement turns are fully resolved
        # before enforcement resumes. _step_enforcement_count is shared across
        # all outer iterations so the safety cap is total, not per-recheck.
        _MAX_STEP_ENFORCEMENT = 20
        _step_enforcement_count = 0
        _needs_hitl_recheck = True

        while _needs_hitl_recheck:
            _needs_hitl_recheck = False

            # ── HITL loop — handle consecutive interrupts until graph is quiescent ──
            # Each tool call that requires approval is a separate interrupt; loop until none remain.
            while True:
                try:
                    snapshot = await runnable.get_state()
                except Exception as exc:
                    logger.debug("HITL state check failed: %s", exc)
                    break

                if not (snapshot.tasks and snapshot.tasks[0].interrupts):
                    break

                intr = snapshot.tasks[0].interrupts[0]

                # ── ask_user interrupt — payload has type=="ask_user" ──────────────
                if isinstance(intr.value, dict) and intr.value.get("type") == "ask_user":
                    questions = intr.value.get("questions", [])
                    tool_call_id = intr.value.get("tool_call_id", "")

                    ask_fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
                    _ASK_USER_FUTURES[run_id] = ask_fut
                    _RUN_REGISTRY[run_id]["status"] = "interrupted"

                    await bus.publish("ask_user_request", {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "questions": questions,
                        "tool_call_id": tool_call_id,
                    })

                    try:
                        ask_response = await asyncio.wait_for(ask_fut, timeout=3600.0)
                    except asyncio.TimeoutError:
                        _ASK_USER_FUTURES.pop(run_id, None)
                        await bus.publish("error", {
                            "run_id": run_id,
                            "thread_id": thread_id,
                            "message": "ask_user timed out after 1 hour",
                            "traceback": "",
                        })
                        _RUN_REGISTRY[run_id]["status"] = "failed"
                        return
                    finally:
                        _ASK_USER_FUTURES.pop(run_id, None)

                    _RUN_REGISTRY[run_id]["status"] = "running"
                    await bus.publish("ask_user_resolved", {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "status": ask_response.get("status", "answered"),
                    })
                    await _stream_once(Command(resume=ask_response))
                    continue
                # ── end ask_user branch ────────────────────────────────────────────

                _intr_payload = json.dumps(intr.value, sort_keys=True, default=str) if isinstance(intr.value, dict) else str(intr.value)
                interrupt_id = hashlib.sha256(_intr_payload.encode()).hexdigest()[:16]
                action_requests = intr.value.get("action_requests", []) if isinstance(intr.value, dict) else []
                _num_actions = len(action_requests) if action_requests else 1

                # Session auto-approval: skip future if all interrupted tools were previously approved
                _tool_names = [ar.get("name", "") for ar in action_requests if ar.get("name")]
                if _tool_names and all(t in _SESSION_APPROVED.get(thread_id, set()) for t in _tool_names):
                    await bus.publish("interrupt_auto_approved", {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "tool_names": _tool_names,
                    })
                    await _stream_once(Command(resume={"decisions": [{"type": "approve"}] * _num_actions}))
                    continue

                # Store current action_requests so submit endpoint can read them for approve_for_session
                _RUN_REGISTRY[run_id]["current_interrupt_action_requests"] = action_requests

                fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
                _HITL_FUTURES[run_id] = fut
                _RUN_REGISTRY[run_id]["status"] = "interrupted"

                await bus.publish("interrupt", {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "interrupt_id": interrupt_id,
                    "action_requests": action_requests,
                })

                try:
                    decision = await asyncio.wait_for(fut, timeout=3600.0)
                except asyncio.TimeoutError:
                    _HITL_FUTURES.pop(run_id, None)
                    await bus.publish("error", {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "message": "HITL decision timed out after 1 hour",
                        "traceback": "",
                    })
                    _RUN_REGISTRY[run_id]["status"] = "failed"
                    return
                finally:
                    _HITL_FUTURES.pop(run_id, None)

                # Reset to "running" immediately so GET /runs/{id} shows active execution
                _RUN_REGISTRY[run_id]["status"] = "running"
                await bus.publish("interrupt_resolved", {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "interrupt_id": interrupt_id,
                    "decision": decision,
                })

                # Build N resume decisions — SDK requires len(decisions) == len(action_requests)
                _dtype = decision.get("type", "approve")
                if _dtype == "approve_for_session":
                    for _ar in action_requests:
                        _SESSION_APPROVED.setdefault(thread_id, set()).add(_ar.get("name", ""))
                    if allowed_tools is not None:
                        for _ar in action_requests:
                            _tn = _ar.get("name", "")
                            if _tn and _tn not in allowed_tools:
                                allowed_tools.append(_tn)
                    _resume_decisions = [{"type": "approve"}] * _num_actions
                    await bus.publish("session_approved", {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "approved_tools": [_ar.get("name", "") for _ar in action_requests],
                        "session_approved_tools": list(_SESSION_APPROVED.get(thread_id, set())),
                    })
                elif _dtype == "approve":
                    _resume_decisions = [{"type": "approve"}] * _num_actions
                elif _dtype == "reject":
                    _rej: dict[str, Any] = {"type": "reject"}
                    if decision.get("message"):
                        _rej["message"] = decision["message"]
                    _resume_decisions = [_rej] * _num_actions
                elif _dtype == "edit":
                    _resume_decisions = [{"type": "edit", "edited_action": decision["edited_action"]}]
                    _resume_decisions += [{"type": "approve"}] * (_num_actions - 1)
                elif _dtype == "respond":
                    _resume_decisions = [{"type": "respond", "message": decision["message"]}] * _num_actions
                else:
                    _resume_decisions = [{"type": "approve"}] * _num_actions

                await _stream_once(Command(resume={"decisions": _resume_decisions}))

            # ── Step enforcement loop ─────────────────────────────────────────────
            # Re-invoke the agent until all approved plan steps are complete.
            # Fires only when plan_approved=True and at least one step is still pending.
            while _RUN_REGISTRY[run_id].get("plan_approved"):
                _steps = _RUN_REGISTRY[run_id].get("plan_steps", [])
                _incomplete = [s for s in _steps if s.get("status") not in ("done", "blocked")]
                if not _incomplete:
                    break
                if _step_enforcement_count >= _MAX_STEP_ENFORCEMENT:
                    await bus.publish("error", {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "message": (
                            f"Step enforcement cap reached ({_MAX_STEP_ENFORCEMENT} re-injections). "
                            f"{len(_incomplete)} steps still incomplete."
                        ),
                        "traceback": "",
                    })
                    break
                _step_enforcement_count += 1
                _step_lines = "\n".join(
                    f"  {s['number']}. {s.get('title') or s.get('description', '—')}" for s in _incomplete
                )
                _enforcement_msg = (
                    f"<system-reminder>\n"
                    f"You attempted to end the run with {len(_incomplete)} incomplete plan step(s):\n"
                    f"{_step_lines}\n\n"
                    f"You MUST complete all steps before ending the run. "
                    f"For each step: enter_step(N) → do the work → mark_step_done(N). "
                    f"Or mark_step_blocked(N, reason) if a step cannot proceed. "
                    f"Call exit_plan_mode() only after all steps are done or blocked.\n"
                    f"</system-reminder>"
                )
                await _stream_once(_enforcement_msg)
                if max_turns is not None and _num_turns[0] >= max_turns:
                    _hit_max_turns[0] = True
                    break
                # If the enforcement turn triggered a HITL interrupt, break back to
                # the HITL loop (outer loop will recheck and resolve the interrupt
                # before enforcement resumes).
                try:
                    _enf_snapshot = await runnable.get_state()
                    if _enf_snapshot.tasks and _enf_snapshot.tasks[0].interrupts:
                        _needs_hitl_recheck = True
                        break
                except Exception:
                    break

        # ── Final output ──────────────────────────────────────────────────────
        try:
            final = await runnable.get_state()
            msgs = final.values.get("messages", [])
            output = _extract_text(getattr(msgs[-1], "content", "")) if msgs else ""
        except Exception:
            output = ""

        duration_ms = int((datetime.now(UTC) - run_start_time).total_seconds() * 1000)
        # Preserve "failed" status set by tools (e.g. plan approval timeout)
        _tool_failed = _RUN_REGISTRY[run_id].get("status") == "failed"
        _final_status = "failed" if _tool_failed else "completed"
        _result_subtype = (
            "error_max_turns" if _hit_max_turns[0]
            else "error_during_execution" if _tool_failed
            else "success"
        )
        _RUN_REGISTRY[run_id].update({
            "status": _final_status,
            "output": output,
            "model": _detected_model[0],
            "stop_reason": _last_stop_reason[0],
            "result_subtype": _result_subtype,
            "num_turns": _num_turns[0],
            "request_count": _request_count[0],
            "duration_ms": duration_ms,
            "ttft_ms": _first_token_ms[0],
            "usage": dict(_usage_accum),
            "model_usage": dict(_model_usage),
            "total_cost_usd": None,
        })
        await bus.publish("run_end", {
            "run_id": run_id,
            "thread_id": thread_id,
            "status": _final_status,
            "output": output,
            "model": _detected_model[0],
            "stop_reason": _last_stop_reason[0],
            "result_subtype": _result_subtype,
            "num_turns": _num_turns[0],
            "request_count": _request_count[0],
            "duration_ms": duration_ms,
            "ttft_ms": _first_token_ms[0],
            "usage": dict(_usage_accum),
            "model_usage": dict(_model_usage),
            "total_cost_usd": None,
        })

        if self_learn:
            try:
                from rai.harness.selflearn import run_self_learn
                await run_self_learn(run_id, agent_name, _RUN_REGISTRY.get(run_id, {}), bus)
            except Exception:
                pass

    except asyncio.CancelledError:
        _HITL_FUTURES.pop(run_id, None)
        _PLAN_FUTURES.pop(run_id, None)
        duration_ms = int((datetime.now(UTC) - run_start_time).total_seconds() * 1000)
        _RUN_REGISTRY[run_id].update({
            "status": "cancelled",
            "model": _detected_model[0],
            "stop_reason": "cancelled",
            "result_subtype": "error_during_execution",
            "num_turns": _num_turns[0],
            "request_count": _request_count[0],
            "duration_ms": duration_ms,
            "usage": dict(_usage_accum),
            "model_usage": dict(_model_usage),
        })
        await bus.publish("error", {
            "run_id": run_id,
            "thread_id": thread_id,
            "message": "Run cancelled",
            "traceback": "",
        })
        await bus.publish("run_end", {
            "run_id": run_id,
            "thread_id": thread_id,
            "status": "cancelled",
            "output": "",
            "model": _detected_model[0],
            "stop_reason": "cancelled",
            "result_subtype": "error_during_execution",
            "num_turns": _num_turns[0],
            "request_count": _request_count[0],
            "duration_ms": duration_ms,
            "usage": dict(_usage_accum),
            "model_usage": dict(_model_usage),
        })
    except Exception as exc:
        duration_ms = int((datetime.now(UTC) - run_start_time).total_seconds() * 1000)
        _RUN_REGISTRY[run_id].update({
            "status": "failed",
            "model": _detected_model[0],
            "stop_reason": "error",
            "result_subtype": "error_during_execution",
            "num_turns": _num_turns[0],
            "request_count": _request_count[0],
            "duration_ms": duration_ms,
            "usage": dict(_usage_accum),
            "model_usage": dict(_model_usage),
        })
        # C1 — emit rate_limit event before error when the cause is a 429
        if _is_rate_limit_error(exc):
            await bus.publish("rate_limit", {
                "run_id": run_id,
                "thread_id": thread_id,
                "status": "rate_limited",
                "resets_at": getattr(getattr(exc, "response", None), "headers", {}).get(
                    "x-ratelimit-reset-requests"
                ),
                "rate_limit_type": "requests",
                "utilization": None,
                "overage_status": None,
            })
        await bus.publish("error", {
            "run_id": run_id,
            "thread_id": thread_id,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        })
        await bus.publish("run_end", {
            "run_id": run_id,
            "thread_id": thread_id,
            "status": "failed",
            "output": "",
            "model": _detected_model[0],
            "stop_reason": "error",
            "result_subtype": "error_during_execution",
            "num_turns": _num_turns[0],
            "request_count": _request_count[0],
            "duration_ms": duration_ms,
            "usage": dict(_usage_accum),
            "model_usage": dict(_model_usage),
        })
    finally:
        _RUN_TASKS.pop(run_id, None)
        if _ctx_token is not None:
            _RUN_CONTEXT.reset(_ctx_token)
        notif_bus.detach()
        stop_flag.set()
        monitor_task.cancel()
        keepalive_task.cancel()
        bus.close(run_id)


# ── Content block helpers ─────────────────────────────────────────────────────

def _extract_text(content: Any) -> str:
    """Extract plain text from a str or list of content blocks."""
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
            if not isinstance(b, dict) or b.get("type") in (None, "text")
        ).strip()
    return str(content) if content else ""



async def _publish_content_blocks(bus: RunEventBus, thread_id: str, content: Any) -> None:
    """Publish token / thinking events from a streaming chunk.

    Handles plain string content (most models) and list content blocks
    (Claude extended thinking / multi-part streaming chunks).

    LangChain-Anthropic block type mapping (after normalization):
      streaming delta  → type="thinking", key="thinking_delta"  (incremental text)
      signature delta  → type="thinking", key="signature"       (skip — crypto verifier)
      complete block   → type="thinking", key="thinking"        (full text in final AIMessage)
      redacted         → type="redacted_thinking", key="data"   (encrypted block — emit flag)
    """
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                if block:
                    await bus.publish("token", {"thread_id": thread_id, "content": str(block)})
                continue
            btype = block.get("type", "text")

            if btype == "text":
                text = block.get("text", "")
                if text:
                    await bus.publish("token", {"thread_id": thread_id, "content": text})

            elif btype == "thinking":
                # During streaming:  key is "thinking_delta" (incremental chunk)
                # In final message:  key is "thinking" (complete block)
                # Signature blocks:  key is "signature" — skip, not user-visible content
                text = block.get("thinking_delta") or block.get("thinking") or ""
                if text:
                    await bus.publish("thinking", {"thread_id": thread_id, "content": text})

            elif btype == "redacted_thinking":
                # Anthropic sends redacted_thinking when the thinking block is
                # encrypted for safety. Emit a placeholder so the client knows
                # reasoning happened but was withheld.
                await bus.publish("thinking", {
                    "thread_id": thread_id,
                    "content": "",
                    "redacted": True,
                })

            # tool_use / input_json_delta: covered by on_tool_start / on_tool_end
    elif content:
        await bus.publish("token", {"thread_id": thread_id, "content": content})
