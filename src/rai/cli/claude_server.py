"""FastAPI SSE backend for the RAI Claude React UI.

Launch:
    rai claude-serve [--agent rai] [--host 127.0.0.1] [--port 8001]

─────────────────────────────────────────────────────────────────────────────
REST endpoints
─────────────────────────────────────────────────────────────────────────────

  GET  /ok

  POST /chat
    body:  { prompt, session_id?, resume?, agent?, model?, effort?, hitl?, plan_mode? }
    stream: see SSE events below

  GET    /agents
  DELETE /agents/{session_id}
  POST   /agents/{session_id}/interrupt
  POST   /agents/{session_id}/cancel-task      body: { task_id }
  POST   /agents/{session_id}/permission       body: { tool_use_id, decision }
  POST   /agents/{session_id}/model            body: { model }
  POST   /agents/{session_id}/permission-mode  body: { mode }
  GET    /agents/{session_id}/mcp
  GET    /agents/{session_id}/context

  GET    /history                              ?limit=30&offset=0
  GET    /history/{session_id}
  GET    /history/{session_id}/messages        ?limit=50&offset=0
  GET    /history/{session_id}/subagents
  GET    /history/{session_id}/subagents/{agent_id}/messages
  DELETE /history/{session_id}

─────────────────────────────────────────────────────────────────────────────
SSE events  (POST /chat  →  text/event-stream)
─────────────────────────────────────────────────────────────────────────────

  {"type": "session_id",        "session_id": "..."}
  {"type": "token",             "text": "..."}
  {"type": "thinking",          "text": "..."}
  {"type": "tool_start",        "id": "...", "name": "...", "input": {...}}
  {"type": "tool_result",       "id": "...", "output": "...", "is_error": false}
  {"type": "subagent_start",    "task_id": "...", "description": "...", "uuid": "..."}
  {"type": "subagent_progress", "task_id": "...", "last_tool": "...", "tokens": 0,
                                "duration_ms": 0, "tool_uses": 0}
  {"type": "subagent_done",     "task_id": "...", "status": "completed|failed|stopped",
                                "summary": "...", "tokens": 0, "duration_ms": 0}
  // HITL — agent frozen until POST /agents/{session_id}/permission
  {"type": "hitl_request",      "tool_use_id": "...", "tool_name": "...", "input": {...},
                                "title": "...", "display_name": "...", "description": "...",
                                "reason": "...", "agent_id": null, "session_id": "..."}
  {"type": "hitl_response",     "tool_use_id": "...", "decision": "allow|deny"}
  // Plan mode — tools were blocked; call POST /permission-mode {"mode":"default"} to continue
  {"type": "plan_blocked",      "denied": [{...},...], "deferred": {"id","name","input"}|null}
  {"type": "rate_limit",        "status": "allowed|allowed_warning|rejected",
                                "limit_type": "...", "resets_at": 0, "utilization": 0.0}
  {"type": "run_end",           "session_id": "...", "cost": 0.0, "turns": 0,
                                "is_error": false, "stop_reason": "end_turn",
                                "duration_ms": 0, "usage": {...}}
  {"type": "error",             "message": "..."}
  data: [DONE]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from contextlib import asynccontextmanager

_log = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from rai.engine.claude_sdk.agent import RunnableClaudeAgent
from rai.engine.claude_sdk.session import ClaudeSessionManager
from rai.sdk import (
    AssistantMessage,
    ClaudeAgent,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    iter_thinking,
    iter_tokens,
)

# Native Claude Code tools whose names the CLI session rule store recognises.
# updated_permissions(destination="session") only works for these — MCP tools
# (mcp__*) and custom tools are handled purely by session_allowed_tools instead.
_NATIVE_TOOLS = frozenset({
    "Bash", "Write", "Edit", "NotebookEdit", "MultiEdit",
    "Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch",
    "TodoWrite", "TodoRead", "Task",
})

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

def _max_sessions() -> int:
    return int(os.environ.get("RAI_CLAUDE_MAX_SESSIONS", "20"))


@asynccontextmanager
async def _lifespan(_: FastAPI):
    if not _api_key():
        _log.warning(
            "RAI_CLAUDE_API_KEY is not set — claude-serve is open to all requests. "
            "Pass --api-key <secret> to require Bearer token authentication."
        )
    yield
    # Shutdown: disconnect every live subprocess cleanly
    async with _sessions_lock:
        ids = list(_sessions.keys())
    for sid in ids:
        await _disconnect(sid)


app = FastAPI(title="RAI Claude Serve", version="2.0.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_SENTINEL = object()  # end-of-stream signal for output_queue


# ─────────────────────────────────────────────────────────────────────────────
# Per-session state
# ─────────────────────────────────────────────────────────────────────────────

class _SessionState:
    """Holds one live agent subprocess and its HITL state.

    output_queue and hitl_queue are created fresh per POST /chat call (not
    here) to prevent stale sentinels and concurrent-stream corruption.
    """

    def __init__(self) -> None:
        self.agent: RunnableClaudeAgent | None = None
        # Tracks the current permission mode so set_permission_mode is only
        # called when the mode actually changes, not on every message.
        self.current_mode: str = "default"
        # HITL: tool_use_id → Future["allow"|"deny"|"allow_session"]
        self.hitl_futures: dict[str, asyncio.Future[str]] = {}
        self.hitl_lock = asyncio.Lock()
        # hitl_queue is replaced each call — set by _make_call_queues()
        self.hitl_queue: asyncio.Queue[Any] = asyncio.Queue()
        # active_stream: True while an SSE stream is open on this session.
        # Prevents two concurrent /chat calls from corrupting each other.
        self.active_stream: bool = False
        # Tools the user has session-allowed — _gate returns "allow" for these
        # so our PreToolUse hook doesn't re-escalate after the CLI already has
        # a session allow-rule stored (CLI "ask rule on hook-rewritten input").
        self.session_allowed_tools: set[str] = set()
        # Answers for in-flight AskUserQuestion calls — keyed by tool_use_id.
        self.hitl_answers: dict[str, dict[str, str]] = {}
        # Feedback text for plan_feedback decisions — keyed by tool_use_id.
        self.plan_feedback: dict[str, str] = {}


_sessions: dict[str, _SessionState] = {}
_sessions_lock = asyncio.Lock()


def _get_state(session_id: str) -> _SessionState | None:
    return _sessions.get(session_id)


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────

def _api_key() -> str | None:
    return os.environ.get("RAI_CLAUDE_API_KEY") or None


async def _require_auth(request: Request) -> None:
    required = _api_key()
    if not required:
        return
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if token != required:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


def _default_agent() -> str:
    return os.environ.get("RAI_CLAUDE_AGENT", "rai")


def _hitl_enabled_env() -> bool:
    return os.environ.get("RAI_CLAUDE_HITL", "1") != "0"


# ─────────────────────────────────────────────────────────────────────────────
# Request models
# ─────────────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    prompt: str
    session_id: str = "default"
    resume: str | None = None
    agent: str | None = None
    model: str | None = None
    effort: str | None = None
    # Permission mode — priority: plan_mode > hitl > autonomous
    hitl: bool = True       # default: HITL on
    plan_mode: bool = False  # force plan mode for this turn


class PermissionDecision(BaseModel):
    tool_use_id: str
    decision: str  # "allow" | "deny" | "allow_session" | "ask_user_answer" | "plan_feedback"
    answers: dict[str, str] | None = None   # for ask_user_answer
    feedback: str | None = None             # for plan_feedback (reject-with-edit)


class CancelTaskRequest(BaseModel):
    task_id: str


class SetModelRequest(BaseModel):
    model: str | None = None


class SetPermissionModeRequest(BaseModel):
    mode: str  # "default"|"bypassPermissions"|"acceptEdits"|"plan"|"dontAsk"


# ─────────────────────────────────────────────────────────────────────────────
# Permission mode resolution
# ─────────────────────────────────────────────────────────────────────────────

def _effective_mode(req: ChatRequest) -> str:
    if req.plan_mode:
        return "plan"
    if req.hitl and _hitl_enabled_env():
        return "default"
    return "bypassPermissions"


# ─────────────────────────────────────────────────────────────────────────────
# HITL callback factory
# ─────────────────────────────────────────────────────────────────────────────

def _make_hitl_callback(state: _SessionState, session_id: str):
    """
    can_use_tool callback — invoked by the SDK when any tool needs permission
    (escalated by the PreToolUse _gate hook returning permissionDecision="ask").

    Flow:
      1. Pushes hitl_request into state.hitl_queue
         (_pump_hitl_events forwards it to output_queue → SSE emits it)
      2. Creates a Future and blocks (agent subprocess is frozen here)
      3. POST /agents/{id}/permission resolves the Future
      4. Pushes hitl_response, returns Allow or Deny
    """
    async def _callback(tool_name: str, tool_input: dict, context: Any) -> Any:
        tool_use_id  = getattr(context, "tool_use_id", None) or f"{tool_name}_{id(context)}"
        title        = getattr(context, "title", None)          or f"Allow {tool_name}?"
        display_name = getattr(context, "display_name", None)   or tool_name
        description  = getattr(context, "description", None)    or ""
        reason       = getattr(context, "decision_reason", None) or ""
        agent_id     = getattr(context, "agent_id", None)

        await state.hitl_queue.put({
            "type":         "hitl_request",
            "tool_use_id":  tool_use_id,
            "tool_name":    tool_name,
            "input":        tool_input,
            "title":        title,
            "display_name": display_name,
            "description":  description,
            "reason":       reason,
            "agent_id":     agent_id,
            "session_id":   session_id,
        })

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        async with state.hitl_lock:
            state.hitl_futures[tool_use_id] = fut

        try:
            decision = await asyncio.wait_for(fut, timeout=120)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            decision = "deny"
        finally:
            async with state.hitl_lock:
                state.hitl_futures.pop(tool_use_id, None)

        await state.hitl_queue.put({
            "type":        "hitl_response",
            "tool_use_id": tool_use_id,
            "decision":    decision,
        })

        if decision == "ask_user_answer":
            answers = state.hitl_answers.pop(tool_use_id, {})
            return PermissionResultAllow(
                updated_input={**tool_input, "answers": answers}
            )

        if decision == "plan_feedback":
            # ExitPlanMode rejected with edit feedback — Claude sees the message,
            # stays in plan mode, and revises before calling ExitPlanMode again.
            feedback = state.plan_feedback.pop(tool_use_id, "Plan needs revision.")
            return PermissionResultDeny(message=feedback)

        if decision == "allow_session":
            # Server-side tracking: _gate returns "allow" for session-allowed tools
            # so the CLI never re-escalates via can_use_tool again.
            # This is the only reliable path for MCP/custom tools — the CLI's
            # session rule store (updated_permissions destination="session") only
            # works for native Claude Code tools, not mcp__* or custom tool names.
            state.session_allowed_tools.add(tool_name)

            # Also send updated_permissions for native tools as belt-and-suspenders
            # so the CLI binary's own session store is updated where it works.
            if tool_name in _NATIVE_TOOLS:
                from claude_agent_sdk.types import PermissionRuleValue, PermissionUpdate
                return PermissionResultAllow(
                    updated_permissions=[
                        PermissionUpdate(
                            type="addRules",
                            rules=[PermissionRuleValue(tool_name=tool_name, rule_content=None)],
                            behavior="allow",
                            destination="session",
                        )
                    ]
                )
            return PermissionResultAllow()

        if decision == "allow":
            return PermissionResultAllow()
        return PermissionResultDeny(message="Denied by user")

    return _callback


# ─────────────────────────────────────────────────────────────────────────────
# Agent creation
# ─────────────────────────────────────────────────────────────────────────────

async def _create_session(req: ChatRequest) -> _SessionState:
    """Spawn a new agent subprocess and register it. Called outside _sessions_lock."""
    state = _SessionState()
    mode  = _effective_mode(req)
    state.current_mode = mode

    from claude_agent_sdk.types import HookMatcher

    builder = (
        ClaudeAgent.builder()
        .agent_name(req.agent or _default_agent())
        .with_partial_messages()
    )

    if os.environ.get("RAI_CLAUDE_NO_SUBAGENTS") == "1":
        builder = builder.without_subagents()

    if mode == "plan":
        builder = builder.plan_mode()

    elif mode == "default":
        # HITL: PreToolUse hook escalates Bash to can_use_tool.
        # Without the hook, Claude Code's own allow-rules bypass can_use_tool
        # for pre-approved tools even in permission_mode="default".
        callback = _make_hitl_callback(state, req.session_id)

        # Tools that are always safe to run without HITL — read-only / informational.
        # EnterPlanMode is auto-allowed: it only switches the permission mode,
        # the user-facing approval gate is ExitPlanMode (which IS escalated).
        _ALLOW_TOOLS = frozenset({
            "Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch",
            "NotebookRead", "TodoRead", "ListMcpResourcesTool",
            "ReadMcpResourceTool", "EnterPlanMode",
        })

        async def _gate(input_data: Any, _tuid: str | None, _ctx: Any) -> dict:
            tool_name  = input_data.get("tool_name", "") if isinstance(input_data, dict) else ""
            tool_input = input_data.get("tool_input", {}) if isinstance(input_data, dict) else {}

            if tool_name in _ALLOW_TOOLS or tool_name in state.session_allowed_tools:
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}

            if tool_name == "Bash":
                cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
                reason = f"$ {cmd[:120]}"
            elif tool_name == "Write":
                path = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
                reason = f"Write {path}"
            elif tool_name == "Edit":
                path = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
                reason = f"Edit {path}"
            elif tool_name == "NotebookEdit":
                path = tool_input.get("notebook_path", "") if isinstance(tool_input, dict) else ""
                reason = f"NotebookEdit {path}"
            elif tool_name == "MultiEdit":
                path = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
                reason = f"MultiEdit {path}"
            else:
                reason = tool_name

            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }}

        builder = (
            builder
            .with_hitl()
            .can_use_tool(callback)
            .add_hook("PreToolUse", HookMatcher(hooks=[_gate]))
        )

    else:
        builder = builder.without_hitl()

    if req.model:  builder = builder.model(req.model)
    if req.effort: builder = builder.effort(req.effort)
    if req.resume: builder = builder.resume(req.resume)

    ctx         = builder.build()
    state.agent = await ctx.__aenter__()  # spawns subprocess, may take a few seconds
    return state


async def _get_or_create(req: ChatRequest) -> tuple[RunnableClaudeAgent, _SessionState]:
    # Fast path: session exists — no lock held during slow operations
    async with _sessions_lock:
        existing = _sessions.get(req.session_id)

    if existing is not None:
        assert existing.agent is not None
        # Only call set_permission_mode when the mode actually changed.
        # Avoids a control_request round-trip on every single message.
        new_mode = _effective_mode(req)
        if new_mode != existing.current_mode:
            await existing.agent.set_permission_mode(new_mode)
            existing.current_mode = new_mode
        return existing.agent, existing

    # Slow path: create subprocess outside the lock so other requests aren't blocked
    state = await _create_session(req)

    # Re-acquire lock to register — check again in case another request raced us
    async with _sessions_lock:
        if req.session_id in _sessions:
            # Another request won the race — discard ours, disconnect cleanly
            try:
                await state.agent.__aexit__(None, None, None)
            except Exception:
                pass
            existing = _sessions[req.session_id]
            return existing.agent, existing  # type: ignore[return-value]
        _sessions[req.session_id] = state

    return state.agent, state  # type: ignore[return-value]


async def _disconnect(session_id: str) -> None:
    async with _sessions_lock:
        state = _sessions.pop(session_id, None)
    if state and state.agent:
        try:
            await state.agent.__aexit__(None, None, None)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# SSE pumps
# ─────────────────────────────────────────────────────────────────────────────

def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def _pump_sdk_messages(
    agent: RunnableClaudeAgent,
    state: _SessionState,
    prompt: str,
    output_queue: asyncio.Queue[Any],
) -> None:
    """Drive agent.stream() and forward SDK messages to output_queue.
    May block mid-turn when HITL fires. _pump_hitl_events runs concurrently
    so hitl events still reach the SSE generator while this is blocked.

    agent.stream() keeps yielding after ResultMessage when subagents are
    still running — we forward everything until the iterator exhausts.
    """
    try:
        async for msg in agent.stream(prompt):
            await output_queue.put(msg)
    except Exception as exc:
        await output_queue.put({"__error__": str(exc)})
    finally:
        await output_queue.put(_SENTINEL)


async def _pump_hitl_events(
    state: _SessionState,
    output_queue: asyncio.Queue[Any],
) -> None:
    """Sole reader of state.hitl_queue → forward to output_queue.
    Runs until cancelled. Keeping this as the only hitl_queue reader
    prevents any race with _pump_sdk_messages.
    """
    try:
        while True:
            ev = await state.hitl_queue.get()
            await output_queue.put(ev)
    except asyncio.CancelledError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# SSE stream generator
# ─────────────────────────────────────────────────────────────────────────────

async def _stream_events(
    agent: RunnableClaudeAgent,
    state: _SessionState,
    prompt: str,
) -> AsyncGenerator[str, None]:
    # output_queue is created fresh per call to avoid stale _SENTINEL from
    # a previous turn and concurrent-stream corruption if two /chat calls
    # arrive on the same session_id.
    output_queue: asyncio.Queue[Any] = asyncio.Queue()

    sdk_task  = asyncio.create_task(_pump_sdk_messages(agent, state, prompt, output_queue))
    hitl_task = asyncio.create_task(_pump_hitl_events(state, output_queue))
    session_id_emitted = False

    try:
        while True:
            item = await output_queue.get()

            if item is _SENTINEL:
                break

            if isinstance(item, dict) and "__error__" in item:
                yield _sse({"type": "error", "message": item["__error__"]})
                break

            if isinstance(item, dict):
                # HITL event (hitl_request / hitl_response)
                yield _sse(item)
                continue

            msg = item

            for token in iter_tokens(msg):
                yield _sse({"type": "token", "text": token})

            for chunk in iter_thinking(msg):
                yield _sse({"type": "thinking", "text": chunk})

            if isinstance(msg, AssistantMessage):
                if msg.session_id and not session_id_emitted:
                    session_id_emitted = True
                    yield _sse({"type": "session_id", "session_id": msg.session_id})
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        yield _sse({
                            "type":  "tool_start",
                            "id":    block.id,
                            "name":  block.name,
                            "input": block.input,
                        })

            # Tool results arrive in UserMessage.content, not AssistantMessage
            if isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        output = block.content
                        if isinstance(output, list):
                            output = " ".join(
                                b.get("text", "") for b in output
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        yield _sse({
                            "type":     "tool_result",
                            "id":       block.tool_use_id,
                            "output":   str(output or "")[:2000],
                            "is_error": bool(block.is_error),
                        })

            if isinstance(msg, TaskStartedMessage):
                yield _sse({
                    "type":        "subagent_start",
                    "task_id":     msg.task_id,
                    "description": msg.description,
                    "uuid":        msg.uuid,
                    "session_id":  msg.session_id,
                    "task_type":   msg.task_type or "",
                })

            if isinstance(msg, TaskProgressMessage):
                usage = msg.usage or {}
                yield _sse({
                    "type":        "subagent_progress",
                    "task_id":     msg.task_id,
                    "description": msg.description or "",   # stdout line for Monitor watches
                    "last_tool":   msg.last_tool_name or "",
                    "tokens":      usage.get("total_tokens", 0),
                    "tool_uses":   usage.get("tool_uses", 0),
                    "duration_ms": usage.get("duration_ms", 0),
                })

            if isinstance(msg, TaskNotificationMessage):
                usage = msg.usage or {}
                yield _sse({
                    "type":        "subagent_done",
                    "task_id":     msg.task_id,
                    "status":      msg.status,
                    "summary":     msg.summary,
                    "output_file": msg.output_file or "",
                    "tokens":      usage.get("total_tokens", 0),
                    "duration_ms": usage.get("duration_ms", 0),
                })

            if isinstance(msg, RateLimitEvent):
                info = msg.rate_limit_info
                yield _sse({
                    "type":        "rate_limit",
                    "status":      info.status,
                    "limit_type":  info.rate_limit_type or "",
                    "resets_at":   info.resets_at,
                    "utilization": info.utilization,
                })

            if isinstance(msg, ResultMessage):
                # Emit plan_blocked before run_end so the client knows which
                # tools were denied before the turn summary arrives.
                if msg.permission_denials:
                    yield _sse({
                        "type":     "plan_blocked",
                        "denied":   msg.permission_denials,
                        "deferred": {
                            "id":    msg.deferred_tool_use.id,
                            "name":  msg.deferred_tool_use.name,
                            "input": msg.deferred_tool_use.input,
                        } if msg.deferred_tool_use else None,
                    })

                yield _sse({
                    "type":        "run_end",
                    "session_id":  msg.session_id,
                    "cost":        msg.total_cost_usd,
                    "turns":       msg.num_turns,
                    "is_error":    msg.is_error,
                    "stop_reason": msg.stop_reason,
                    "duration_ms": msg.duration_ms,
                    "usage":       msg.usage,
                    "errors":      msg.errors,
                })
                # Don't break — agent.stream() keeps yielding subagent
                # notifications after ResultMessage when subagents are
                # still running. SENTINEL arrives when stream exhausts.

    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})
    finally:
        state.active_stream = False
        sdk_task.cancel()
        hitl_task.cancel()
        for t in (sdk_task, hitl_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    yield "data: [DONE]\n\n"


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/ok")
async def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(body: ChatRequest, _: None = Depends(_require_auth)):
    # Enforce pool size before creating a new session
    if body.session_id not in _sessions and len(_sessions) >= _max_sessions():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Session pool full ({_max_sessions()} max). Delete an existing session first.",
        )

    agent, state = await _get_or_create(body)

    # Set active_stream=True synchronously HERE, before returning StreamingResponse.
    # If we set it inside the generator, a concurrent request could pass the check
    # before the generator starts (TOCTOU). asyncio is single-threaded so no lock needed.
    if state.active_stream:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session already has an active stream. Interrupt it or wait for it to finish.",
        )
    state.active_stream = True

    # Replace hitl_queue with a fresh one each call so stale items from a
    # previous turn (e.g. unresolved hitl_response after a deny) don't leak.
    state.hitl_queue = asyncio.Queue()

    return StreamingResponse(
        _stream_events(agent, state, body.prompt),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Agent pool ────────────────────────────────────────────────────────────────

@app.get("/agents")
async def list_agents(_: None = Depends(_require_auth)):
    return {
        "sessions": [
            {"session_id": sid, "active_stream": s.active_stream, "mode": s.current_mode}
            for sid, s in _sessions.items()
        ]
    }


@app.delete("/agents/{session_id}")
async def disconnect_agent(session_id: str, _: None = Depends(_require_auth)):
    await _disconnect(session_id)
    return {"disconnected": session_id}


@app.post("/agents/{session_id}/interrupt")
async def interrupt_agent(session_id: str, _: None = Depends(_require_auth)):
    """Interrupt the current run. CLI emits ResultMessage(is_error=True); SSE ends naturally.
    The subprocess stays alive — ready for the next prompt."""
    state = _get_state(session_id)
    if not state or not state.agent:
        raise HTTPException(status_code=404, detail="Session not in pool")
    await state.agent.interrupt()
    return {"interrupted": session_id}


@app.post("/agents/{session_id}/cancel-task")
async def cancel_task(session_id: str, body: CancelTaskRequest, _: None = Depends(_require_auth)):
    """Cancel a specific subagent. A subagent_done with status='stopped' follows in the stream."""
    state = _get_state(session_id)
    if not state or not state.agent:
        raise HTTPException(status_code=404, detail="Session not in pool")
    client = state.agent._client
    if client is None:
        raise HTTPException(status_code=400, detail="Agent not connected")
    await client.stop_task(body.task_id)
    return {"stopped_task": body.task_id}


@app.post("/agents/{session_id}/permission")
async def resolve_permission(
    session_id: str,
    body: PermissionDecision,
    _: None = Depends(_require_auth),
):
    """Resolve a pending HITL permission request.
    tool_use_id must match the value from the hitl_request SSE event.
    decision: "allow" | "deny"
    Resolving this unblocks the frozen agent subprocess.
    """
    if body.decision not in ("allow", "allow_session", "deny", "ask_user_answer", "plan_feedback"):
        raise HTTPException(
            status_code=400,
            detail="decision must be 'allow', 'allow_session', 'deny', 'ask_user_answer', or 'plan_feedback'",
        )
    state = _get_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not in pool")
    if body.decision == "ask_user_answer" and body.answers:
        state.hitl_answers[body.tool_use_id] = body.answers
    if body.decision == "plan_feedback" and body.feedback:
        state.plan_feedback[body.tool_use_id] = body.feedback
    async with state.hitl_lock:
        fut = state.hitl_futures.get(body.tool_use_id)
    if fut is None or fut.done():
        raise HTTPException(status_code=404, detail="No pending permission for that tool_use_id")
    fut.set_result(body.decision)
    return {"resolved": body.tool_use_id, "decision": body.decision}


@app.post("/agents/{session_id}/model")
async def set_model(session_id: str, body: SetModelRequest, _: None = Depends(_require_auth)):
    """Switch model mid-session. Takes effect on the next prompt."""
    state = _get_state(session_id)
    if not state or not state.agent:
        raise HTTPException(status_code=404, detail="Session not in pool")
    client = state.agent._client
    if client is None:
        raise HTTPException(status_code=400, detail="Agent not connected")
    await client.set_model(body.model)
    return {"model": body.model}


@app.post("/agents/{session_id}/permission-mode")
async def set_permission_mode(
    session_id: str,
    body: SetPermissionModeRequest,
    _: None = Depends(_require_auth),
):
    """Switch permission mode mid-session.
    Modes: default | bypassPermissions | acceptEdits | plan | dontAsk | auto
    After switching from plan → default, re-send the original prompt to execute.
    """
    state = _get_state(session_id)
    if not state or not state.agent:
        raise HTTPException(status_code=404, detail="Session not in pool")
    await state.agent.set_permission_mode(body.mode)
    state.current_mode = body.mode
    return {"mode": body.mode}


@app.get("/agents/{session_id}/mcp")
async def get_mcp_status(session_id: str, _: None = Depends(_require_auth)):
    """Live MCP server connection status for this session."""
    state = _get_state(session_id)
    if not state or not state.agent:
        raise HTTPException(status_code=404, detail="Session not in pool")
    client = state.agent._client
    if client is None:
        raise HTTPException(status_code=400, detail="Agent not connected")
    return await client.get_mcp_status()


@app.get("/agents/{session_id}/context")
async def get_context_usage(session_id: str, _: None = Depends(_require_auth)):
    """Context window token breakdown for this session."""
    state = _get_state(session_id)
    if not state or not state.agent:
        raise HTTPException(status_code=404, detail="Session not in pool")
    client = state.agent._client
    if client is None:
        raise HTTPException(status_code=400, detail="Agent not connected")
    return await client.get_context_usage()


@app.get("/agents/{session_id}/server-info")
async def get_server_info(session_id: str, _: None = Depends(_require_auth)):
    """Server initialization info: available commands, skills, output styles."""
    state = _get_state(session_id)
    if not state or not state.agent:
        raise HTTPException(status_code=404, detail="Session not in pool")
    client = state.agent._client
    if client is None:
        raise HTTPException(status_code=400, detail="Agent not connected")
    return await client.get_server_info() or {}


# ── Session history (ClaudeSessionManager — reads ~/.claude/) ─────────────────

def _session_to_dict(s: Any) -> dict:
    return {
        "session_id":    s.session_id,
        "summary":       s.summary,
        "custom_title":  s.custom_title,
        "first_prompt":  s.first_prompt,
        "git_branch":    s.git_branch,
        "cwd":           s.cwd,
        "tag":           s.tag,
        "last_modified": s.last_modified,
        "created_at":    s.created_at,
        "file_size":     s.file_size,
    }


def _msg_to_dict(m: Any) -> dict:
    raw     = m.message if hasattr(m, "message") else {}
    role    = raw.get("role", m.type) if isinstance(raw, dict) else m.type
    content = raw.get("content", "") if isinstance(raw, dict) else ""

    text         = ""
    thinking     = ""
    tool_uses:   list[dict] = []
    tool_results: list[dict] = []

    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            btype = b.get("type", "")
            if btype == "text":
                text += b.get("text", "")
            elif btype in ("thinking", "redacted_thinking"):
                thinking += b.get("thinking", b.get("text", ""))
            elif btype == "tool_use":
                tool_uses.append({
                    "id":    b.get("id", ""),
                    "name":  b.get("name", ""),
                    "input": b.get("input", {}),
                })
            elif btype == "tool_result":
                inner = b.get("content", "")
                if isinstance(inner, list):
                    inner = " ".join(
                        x.get("text", "") for x in inner
                        if isinstance(x, dict) and x.get("type") == "text"
                    )
                tool_results.append({
                    "tool_use_id": b.get("tool_use_id", ""),
                    "output":      str(inner)[:2000],
                    "is_error":    b.get("is_error", False),
                })
    else:
        text = str(content).strip()

    out: dict = {
        "type": m.type,
        "role": role,
        "uuid": m.uuid,
        "text": text.strip()[:4000],
    }
    if thinking:
        out["thinking"] = thinking[:1000]
    if tool_uses:
        out["tool_uses"] = tool_uses
    if tool_results:
        out["tool_results"] = tool_results
    return out


@app.get("/history")
async def list_history(limit: int = 30, offset: int = 0, _: None = Depends(_require_auth)):
    try:
        sessions = await asyncio.to_thread(
            lambda: ClaudeSessionManager().list(limit=limit, offset=offset)
        )
        return {"sessions": [_session_to_dict(s) for s in sessions]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/history/{session_id}")
async def get_history_session(session_id: str, _: None = Depends(_require_auth)):
    try:
        info = await asyncio.to_thread(lambda: ClaudeSessionManager().info(session_id))
        if info is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return _session_to_dict(info)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/history/{session_id}/messages")
async def get_history_messages(
    session_id: str, limit: int = 50, offset: int = 0, _: None = Depends(_require_auth)
):
    try:
        # Fetch all messages then tail-slice so limit=N always returns the
        # most recent N messages in chronological order, not the oldest N.
        all_msgs = await asyncio.to_thread(
            lambda: ClaudeSessionManager().messages(session_id, limit=None, offset=0)
        )
        total = len(all_msgs)
        # tail: last `limit` messages (chronological order preserved)
        tail_offset = max(0, total - limit - offset)
        tail_end   = total - offset if offset > 0 else total
        msgs = all_msgs[tail_offset:tail_end]
        return {"messages": [_msg_to_dict(m) for m in msgs]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/history/{session_id}/subagents")
async def get_history_subagents(session_id: str, _: None = Depends(_require_auth)):
    try:
        agents = await asyncio.to_thread(lambda: ClaudeSessionManager().subagents(session_id))
        return {"subagents": agents}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/history/{session_id}/subagents/{agent_id}/messages")
async def get_subagent_messages(
    session_id: str, agent_id: str, limit: int = 50, _: None = Depends(_require_auth)
):
    try:
        msgs = await asyncio.to_thread(
            lambda: ClaudeSessionManager().subagent_messages(session_id, agent_id, limit=limit)
        )
        return {"messages": [_msg_to_dict(m) for m in msgs]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/history/{session_id}")
async def delete_history_session(session_id: str, _: None = Depends(_require_auth)):
    try:
        await asyncio.to_thread(lambda: ClaudeSessionManager().delete(session_id))
        return {"deleted": session_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
