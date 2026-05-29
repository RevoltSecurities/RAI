"""Core subagent executor: full-capability RAI subagents with streaming + HITL."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from rai.agents.background import (
    _NOTIF_LOCK,
    _PENDING_NOTIFICATIONS,
    _TASK_AGENT_NAMES,
    _TASK_REGISTRY,
)
from rai.harness.sse import RunEventBus, sse_frame
import rai.harness.subagents.registry as _subagent_registry
from rai.harness.subagents.registry import (
    _SUBAGENT_BUSES,
    _SUBAGENT_GRAPHS,
    _SUBAGENT_HITL,
    _SUBAGENT_LOCK,
    _SUBAGENT_OUTPUTS,
    _SUBAGENT_REGISTRY,
    _SUBAGENT_TASKS,
)

logger = logging.getLogger(__name__)

_DEFAULT_TASK_TIMEOUT = 3600.0


def _extract_text(content: Any) -> str:
    """Extract plain text from a LangChain message content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


async def _emit_both(
    task_id: str,
    parent_bus: RunEventBus,
    event_type: str,
    data: dict,
) -> None:
    """Publish event to the per-subagent bus and fan to the parent run bus."""
    subagent_bus = _SUBAGENT_BUSES.get(task_id)
    if subagent_bus:
        await subagent_bus.publish(event_type, data)
    await parent_bus.publish(event_type, data)


async def _stream_subagent(
    task_id: str,
    graph: Any,
    config: dict,
    msg: str | Command,
    parent_bus: RunEventBus,
) -> None:
    """Stream one turn of the subagent, fanning events to both buses."""
    if isinstance(msg, Command):
        event_stream = graph.astream_events(msg, config, version="v2")
    else:
        event_stream = graph.astream_events(
            {"messages": [HumanMessage(content=msg)]}, config, version="v2"
        )

    async for event in event_stream:
        kind = event["event"]

        if kind == "on_chat_model_stream":
            chunk = event["data"].get("chunk")
            if chunk is None:
                continue
            content = chunk.content
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    text = block.get("text", "")
                    if not text:
                        continue
                    evt = "subagent_thinking" if btype == "thinking" else "subagent_token"
                    await _emit_both(task_id, parent_bus, evt, {
                        "task_id": task_id, "content": text
                    })
            elif content:
                await _emit_both(task_id, parent_bus, "subagent_token", {
                    "task_id": task_id, "content": content
                })

        elif kind == "on_tool_start":
            await _emit_both(task_id, parent_bus, "subagent_tool_start", {
                "task_id": task_id,
                "tool_name": event["name"],
                "tool_input": event["data"].get("input"),
            })

        elif kind == "on_tool_end":
            raw_out = event["data"].get("output", "")
            await _emit_both(task_id, parent_bus, "subagent_tool_end", {
                "task_id": task_id,
                "tool_name": event["name"],
                "tool_output": str(raw_out)[:500],
            })


async def _run_subagent(
    task_id: str,
    agent_name: str,
    model: str,
    system_prompt: str | None,
    input_message: str,
    parent_run_id: str,
    parent_thread_id: str,
    parent_bus: RunEventBus,
    checkpointer: Any,
    timeout: float,
    label: str | None,
    pipeline_id: str | None,
    depends_on: list[str] | None,
    parent_api_key: str = "",
    parent_base_url: str = "",
) -> None:
    """Inner coroutine wrapped by asyncio.create_task() in launch_subagent()."""
    from rai import DEFAULT_MODEL
    from rai.engine.factory import create_rai_agent
    from rai.sessions.store import build_stream_config

    out_path = Path(f"/tmp/rai_subagent_{task_id[:12]}.json")
    status = "failed"
    output = ""
    _sub_mcp_tools: list = []
    _sub_mcp_session = None

    try:
        # 1. Compile with full capabilities (disable_subagents=True prevents nesting)
        resolved_model = model or DEFAULT_MODEL

        # Credential fallback: subagent's own config.toml wins; parent fills gaps.
        from rai.config.agent import load_agent_config as _load_sub_cfg
        try:
            _sub_cfg = _load_sub_cfg(agent_name)
            effective_api_key  = (_sub_cfg.api_key  if _sub_cfg and _sub_cfg.api_key  else "") or parent_api_key
            effective_base_url = (_sub_cfg.base_url if _sub_cfg and _sub_cfg.base_url else "") or parent_base_url
        except Exception:
            effective_api_key  = parent_api_key
            effective_base_url = parent_base_url

        from rai.mcp.loader import resolve_and_load_mcp_tools as _load_mcp
        try:
            _sub_mcp_tools, _sub_mcp_session, _ = await _load_mcp(agent_name=agent_name)
        except Exception as _mcp_exc:
            logger.debug("Subagent '%s' MCP load skipped: %s", agent_name, _mcp_exc)

        graph, _ = create_rai_agent(
            model=resolved_model,
            agent_name=agent_name,
            system_prompt=system_prompt or None,
            api_key=effective_api_key,
            base_url=effective_base_url,
            auto_approve=False,          # HITL enabled per tool call
            disable_subagents=True,      # prevent recursive HTTP spawning
            suppress_local_async=True,   # explicit — disable_subagents already ensures empty subagents list
            is_subagent=True,            # skips OPPLAN middleware/tools without enabling plan-mode interceptor
            checkpointer=checkpointer,
            interactive=False,
            extra_tools=_sub_mcp_tools or None,
        )

        config = build_stream_config(task_id, agent_name, cwd=str(Path.cwd()), recursion_limit=50)

        with _SUBAGENT_LOCK:
            _SUBAGENT_GRAPHS[task_id] = (graph, config)

        if _subagent_registry._TASK_STORE is not None:
            try:
                _created_at = (_SUBAGENT_REGISTRY.get(task_id) or {}).get("created_at", "")
                await _subagent_registry._TASK_STORE.upsert({
                    "task_id":          task_id,
                    "agent_name":       agent_name,
                    "parent_run_id":    parent_run_id,
                    "parent_thread_id": parent_thread_id,
                    "status":           "running",
                    "created_at":       _created_at,
                    "input":            input_message,
                    "output":           None,
                    "output_file":      str(out_path),
                    "label":            label,
                    "pipeline_id":      pipeline_id,
                    "depends_on":       json.dumps(depends_on) if depends_on else None,
                    "model":            resolved_model,
                    "system_prompt":    system_prompt,
                    "cwd":              str(Path.cwd()),
                })
            except Exception:
                pass

        # 2. Emit start event
        await _emit_both(task_id, parent_bus, "subagent_started", {
            "task_id": task_id,
            "agent_name": agent_name,
            "input": input_message,
            "parent_run_id": parent_run_id,
            "model": resolved_model,
        })

        # 3. Stream initial turn
        await asyncio.wait_for(
            _stream_subagent(task_id, graph, config, input_message, parent_bus),
            timeout=timeout,
        )

        # 4. HITL loop — mirrors parent runner.py execute_run() HITL loop
        from rai.harness.runner import _SESSION_APPROVED as _SA
        while True:
            snapshot = await graph.aget_state(config)
            if not (snapshot.tasks and snapshot.tasks[0].interrupts):
                break

            intr = snapshot.tasks[0].interrupts[0]
            _intr_payload = json.dumps(intr.value, sort_keys=True, default=str) if isinstance(intr.value, dict) else str(intr.value)
            interrupt_id = hashlib.sha256(_intr_payload.encode()).hexdigest()[:16]
            action_requests = intr.value.get("action_requests", []) if isinstance(intr.value, dict) else []

            # Auto-bypass interrupt if all requested tools are already session-approved
            _req_names = [_ar.get("name", "") for _ar in action_requests if _ar.get("name")]
            if _req_names and all(t in _SA.get(parent_thread_id, set()) for t in _req_names):
                _num_auto = len(action_requests) if action_requests else 1
                await asyncio.wait_for(
                    _stream_subagent(
                        task_id, graph, config,
                        Command(resume={"decisions": [{"type": "approve"}] * _num_auto}),
                        parent_bus,
                    ),
                    timeout=timeout,
                )
                continue

            fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
            with _SUBAGENT_LOCK:
                _SUBAGENT_HITL[task_id] = fut
                _SUBAGENT_REGISTRY[task_id]["status"] = "interrupted"
                _SUBAGENT_REGISTRY[task_id]["interrupt_id"] = interrupt_id
                _SUBAGENT_REGISTRY[task_id]["action_requests"] = action_requests

            if _subagent_registry._TASK_STORE is not None:
                try:
                    await _subagent_registry._TASK_STORE.update_status(task_id, "interrupted")
                except Exception:
                    pass

            await _emit_both(task_id, parent_bus, "subagent_interrupt", {
                "task_id": task_id,
                "agent_name": agent_name,
                "interrupt_id": interrupt_id,
                "action_requests": action_requests,
            })

            try:
                decision = await asyncio.wait_for(fut, timeout=3600.0)
            except asyncio.TimeoutError:
                logger.warning("Subagent HITL timed out for task %s", task_id)
                raise asyncio.TimeoutError(f"HITL approval timeout for task {task_id}")
            finally:
                with _SUBAGENT_LOCK:
                    _SUBAGENT_HITL.pop(task_id, None)

            with _SUBAGENT_LOCK:
                _SUBAGENT_REGISTRY[task_id]["status"] = "running"

            if _subagent_registry._TASK_STORE is not None:
                try:
                    await _subagent_registry._TASK_STORE.update_status(task_id, "running")
                except Exception:
                    pass

            await _emit_both(task_id, parent_bus, "subagent_interrupt_resolved", {
                "task_id": task_id,
                "interrupt_id": interrupt_id,
                "decision": decision,
            })

            # Build N resume decisions — SDK requires len(decisions) == len(action_requests)
            _num_actions = len(action_requests) if action_requests else 1
            _dtype = decision.get("type", "approve")
            if _dtype == "edit":
                _resume_decisions = [{"type": "edit", "edited_action": decision["edited_action"]}]
                _resume_decisions += [{"type": "approve"}] * (_num_actions - 1)
            elif _dtype == "reject":
                _rej: dict = {"type": "reject"}
                if decision.get("message"):
                    _rej["message"] = decision["message"]
                _resume_decisions = [_rej] * _num_actions
            elif _dtype == "respond":
                _resume_decisions = [{"type": "respond", "message": decision["message"]}] * _num_actions
            elif _dtype == "approve_for_session":
                for _ar in action_requests:
                    _SA.setdefault(parent_thread_id, set()).add(_ar.get("name", ""))
                _resume_decisions = [{"type": "approve"}] * _num_actions
            else:
                _resume_decisions = [{"type": "approve"}] * _num_actions

            await asyncio.wait_for(
                _stream_subagent(
                    task_id, graph, config,
                    Command(resume={"decisions": _resume_decisions}),
                    parent_bus,
                ),
                timeout=timeout,
            )

        # 5. Extract output from final checkpoint
        final = await graph.aget_state(config)
        msgs = (final.values or {}).get("messages", [])
        for msg in reversed(msgs):
            if isinstance(msg, AIMessage):
                output = _extract_text(msg.content)
                break

        status = "completed"

    except asyncio.CancelledError:
        status = "cancelled"
        logger.info("Subagent %s (%s) cancelled", task_id[:12], agent_name)
    except asyncio.TimeoutError:
        status = "timeout"
        logger.warning("Subagent %s (%s) timed out", task_id[:12], agent_name)
    except Exception as exc:
        status = "failed"
        logger.exception("Subagent %s (%s) failed: %s", task_id[:12], agent_name, exc)
        await _emit_both(task_id, parent_bus, "subagent_error", {
            "task_id": task_id,
            "agent_name": agent_name,
            "message": str(exc),
        })
    finally:
        if _sub_mcp_session is not None:
            try:
                await _sub_mcp_session.cleanup()
            except Exception:
                pass

        # 6. Write output file
        try:
            out_path.write_text(json.dumps({
                "status": status,
                "output": output,
                "task_id": task_id,
            }))
        except Exception:
            pass

        # 7. Notify parent via _PENDING_NOTIFICATIONS — triggers existing watcher loop
        #    Uses same schema as deepagents _on_done() so LocalAsyncAgentMiddleware picks it up
        with _NOTIF_LOCK:
            _PENDING_NOTIFICATIONS[task_id] = {
                "agent_name": agent_name,
                "status": status,
                "output": output[:400],
                "output_file": str(out_path),
            }
            _TASK_REGISTRY.pop(task_id, None)
            _TASK_AGENT_NAMES.pop(task_id, None)

        # 8. Update own registry
        with _SUBAGENT_LOCK:
            if task_id in _SUBAGENT_REGISTRY:
                _SUBAGENT_REGISTRY[task_id]["status"] = status
                _SUBAGENT_REGISTRY[task_id]["output"] = output
                _SUBAGENT_REGISTRY[task_id]["output_file"] = str(out_path)
            _SUBAGENT_TASKS.pop(task_id, None)

        if _subagent_registry._TASK_STORE is not None:
            try:
                await _subagent_registry._TASK_STORE.update_output(task_id, status, output, str(out_path))
            except Exception:
                pass

        # 9. Put full output in sync queue (for http_run_agent_sync)
        out_q = _SUBAGENT_OUTPUTS.get(task_id)
        if out_q is not None:
            out_q.put_nowait(output)

        # 10. Emit completion event to both buses + close per-subagent bus
        await _emit_both(task_id, parent_bus, "subagent_completed", {
            "task_id": task_id,
            "agent_name": agent_name,
            "status": status,
            "output_preview": output[:400],
            "output_file": str(out_path),
        })
        subagent_bus = _SUBAGENT_BUSES.get(task_id)
        if subagent_bus:
            subagent_bus.close(task_id)


async def _resume_subagent(
    task_id: str,
    message: str | Command,
    graph: Any,
    config: dict,
    agent_name: str,
    parent_bus: RunEventBus,
    timeout: float = _DEFAULT_TASK_TIMEOUT,
) -> None:
    """Re-invoke a completed subagent from its LangGraph checkpoint with a new message."""
    status = "failed"
    output = ""

    try:
        with _SUBAGENT_LOCK:
            if task_id in _SUBAGENT_REGISTRY:
                _SUBAGENT_REGISTRY[task_id]["status"] = "running"

        if _subagent_registry._TASK_STORE is not None:
            try:
                await _subagent_registry._TASK_STORE.update_status(task_id, "running")
            except Exception:
                pass

        await _emit_both(task_id, parent_bus, "subagent_resumed", {
            "task_id": task_id,
            "agent_name": agent_name,
            "message": message if isinstance(message, str) else "(resumed from checkpoint)",
        })

        await asyncio.wait_for(
            _stream_subagent(task_id, graph, config, message, parent_bus),
            timeout=timeout,
        )

        # HITL loop — identical to _run_subagent phases 4
        from rai.harness.runner import _SESSION_APPROVED as _SA
        with _SUBAGENT_LOCK:
            _parent_tid = (_SUBAGENT_REGISTRY.get(task_id) or {}).get("parent_thread_id", "")
        while True:
            snapshot = await graph.aget_state(config)
            if not (snapshot.tasks and snapshot.tasks[0].interrupts):
                break

            intr = snapshot.tasks[0].interrupts[0]
            _intr_payload = json.dumps(intr.value, sort_keys=True, default=str) if isinstance(intr.value, dict) else str(intr.value)
            interrupt_id = hashlib.sha256(_intr_payload.encode()).hexdigest()[:16]
            action_requests = intr.value.get("action_requests", []) if isinstance(intr.value, dict) else []

            # Auto-bypass interrupt if all requested tools are already session-approved
            _req_names = [_ar.get("name", "") for _ar in action_requests if _ar.get("name")]
            if _req_names and all(t in _SA.get(_parent_tid, set()) for t in _req_names):
                _num_auto = len(action_requests) if action_requests else 1
                await asyncio.wait_for(
                    _stream_subagent(
                        task_id, graph, config,
                        Command(resume={"decisions": [{"type": "approve"}] * _num_auto}),
                        parent_bus,
                    ),
                    timeout=timeout,
                )
                continue

            fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
            with _SUBAGENT_LOCK:
                _SUBAGENT_HITL[task_id] = fut
                _SUBAGENT_REGISTRY[task_id]["status"] = "interrupted"
                _SUBAGENT_REGISTRY[task_id]["interrupt_id"] = interrupt_id
                _SUBAGENT_REGISTRY[task_id]["action_requests"] = action_requests

            if _subagent_registry._TASK_STORE is not None:
                try:
                    await _subagent_registry._TASK_STORE.update_status(task_id, "interrupted")
                except Exception:
                    pass

            await _emit_both(task_id, parent_bus, "subagent_interrupt", {
                "task_id": task_id,
                "agent_name": agent_name,
                "interrupt_id": interrupt_id,
                "action_requests": action_requests,
            })

            try:
                decision = await asyncio.wait_for(fut, timeout=3600.0)
            except asyncio.TimeoutError:
                logger.warning("Subagent HITL timed out for task %s", task_id)
                raise asyncio.TimeoutError(f"HITL approval timeout for task {task_id}")
            finally:
                with _SUBAGENT_LOCK:
                    _SUBAGENT_HITL.pop(task_id, None)

            with _SUBAGENT_LOCK:
                _SUBAGENT_REGISTRY[task_id]["status"] = "running"

            if _subagent_registry._TASK_STORE is not None:
                try:
                    await _subagent_registry._TASK_STORE.update_status(task_id, "running")
                except Exception:
                    pass

            await _emit_both(task_id, parent_bus, "subagent_interrupt_resolved", {
                "task_id": task_id,
                "interrupt_id": interrupt_id,
                "decision": decision,
            })

            # Build N resume decisions — SDK requires len(decisions) == len(action_requests)
            _num_actions = len(action_requests) if action_requests else 1
            _dtype = decision.get("type", "approve")
            if _dtype == "edit":
                _resume_decisions = [{"type": "edit", "edited_action": decision["edited_action"]}]
                _resume_decisions += [{"type": "approve"}] * (_num_actions - 1)
            elif _dtype == "reject":
                _rej: dict = {"type": "reject"}
                if decision.get("message"):
                    _rej["message"] = decision["message"]
                _resume_decisions = [_rej] * _num_actions
            elif _dtype == "respond":
                _resume_decisions = [{"type": "respond", "message": decision["message"]}] * _num_actions
            elif _dtype == "approve_for_session":
                for _ar in action_requests:
                    _SA.setdefault(_parent_tid, set()).add(_ar.get("name", ""))
                _resume_decisions = [{"type": "approve"}] * _num_actions
            else:
                _resume_decisions = [{"type": "approve"}] * _num_actions

            await asyncio.wait_for(
                _stream_subagent(
                    task_id, graph, config,
                    Command(resume={"decisions": _resume_decisions}),
                    parent_bus,
                ),
                timeout=timeout,
            )

        # Extract output
        final = await graph.aget_state(config)
        msgs = (final.values or {}).get("messages", [])
        for msg in reversed(msgs):
            if isinstance(msg, AIMessage):
                output = _extract_text(msg.content)
                break

        status = "turn_complete"

    except asyncio.CancelledError:
        status = "cancelled"
        logger.info("Subagent resume %s (%s) cancelled", task_id[:12], agent_name)
    except asyncio.TimeoutError:
        status = "timeout"
        logger.warning("Subagent resume %s (%s) timed out", task_id[:12], agent_name)
    except Exception as exc:
        status = "failed"
        logger.exception("Subagent resume %s (%s) failed: %s", task_id[:12], agent_name, exc)
        await _emit_both(task_id, parent_bus, "subagent_error", {
            "task_id": task_id,
            "agent_name": agent_name,
            "message": str(exc),
        })
    finally:
        with _SUBAGENT_LOCK:
            if task_id in _SUBAGENT_REGISTRY:
                _SUBAGENT_REGISTRY[task_id]["status"] = "completed" if status == "turn_complete" else status
                _SUBAGENT_REGISTRY[task_id]["output"] = output
            _SUBAGENT_TASKS.pop(task_id, None)

        if _subagent_registry._TASK_STORE is not None:
            try:
                _db_status = "completed" if status == "turn_complete" else status
                await _subagent_registry._TASK_STORE.update_output(task_id, _db_status, output, "")
            except Exception:
                pass

        with _NOTIF_LOCK:
            _PENDING_NOTIFICATIONS[task_id] = {
                "agent_name": agent_name,
                "status": status,
                "output": output[:400],
                "output_file": "",
            }
            _TASK_REGISTRY.pop(task_id, None)
            _TASK_AGENT_NAMES.pop(task_id, None)

        await _emit_both(task_id, parent_bus, "subagent_turn_complete", {
            "task_id": task_id,
            "agent_name": agent_name,
            "status": status,
            "output_preview": output[:400],
        })


def launch_subagent_resume(
    task_id: str,
    message: str,
    parent_bus: RunEventBus,
) -> asyncio.Task:
    """Re-invoke a completed HTTP subagent from its LangGraph checkpoint."""
    with _SUBAGENT_LOCK:
        graph_info = _SUBAGENT_GRAPHS.get(task_id)
        if graph_info is None:
            raise KeyError(f"No graph stored for task {task_id}")
        graph, config = graph_info
        agent_name = _SUBAGENT_REGISTRY.get(task_id, {}).get("agent_name", "")

    coro = _resume_subagent(task_id, message, graph, config, agent_name, parent_bus)
    task = asyncio.create_task(coro, name=f"subagent-resume-{task_id[:8]}")

    with _NOTIF_LOCK:
        _TASK_REGISTRY[task_id] = task
        _TASK_AGENT_NAMES[task_id] = agent_name
    with _SUBAGENT_LOCK:
        _SUBAGENT_TASKS[task_id] = task

    return task


def launch_subagent(
    task_id: str,
    agent_name: str,
    model: str,
    input_message: str,
    parent_run_id: str,
    parent_thread_id: str,
    parent_bus: RunEventBus,
    checkpointer: Any,
    system_prompt: str | None = None,
    timeout: float = _DEFAULT_TASK_TIMEOUT,
    label: str | None = None,
    pipeline_id: str | None = None,
    depends_on: list[str] | None = None,
    parent_api_key: str = "",
    parent_base_url: str = "",
) -> asyncio.Task:
    """Spawn a subagent as a background asyncio.Task.

    Registers in both own registries and deepagents' _TASK_REGISTRY so the
    parent execute_run() watcher loop waits for quiescence correctly.

    Returns the asyncio.Task (callers can await or cancel it).
    """
    from rai.harness.sse import RunEventBus as _Bus

    # Create per-subagent SSE bus
    subagent_bus = _Bus.create(task_id)

    created_at = datetime.now(UTC).isoformat()
    meta: SubagentMeta = {  # type: ignore[assignment]
        "task_id": task_id,
        "agent_name": agent_name,
        "parent_run_id": parent_run_id,
        "parent_thread_id": parent_thread_id,
        "status": "running",
        "created_at": created_at,
        "input": input_message,
        "output": None,
        "output_file": str(Path(f"/tmp/rai_subagent_{task_id[:12]}.json")),
        "label": label,
        "pipeline_id": pipeline_id,
        "depends_on": depends_on,
    }

    # Import here to satisfy typing
    from rai.harness.subagents.registry import SubagentMeta  # noqa: F401

    # Register in own state
    with _SUBAGENT_LOCK:
        _SUBAGENT_REGISTRY[task_id] = meta
        _SUBAGENT_BUSES[task_id] = subagent_bus
        out_q: asyncio.Queue[str] = asyncio.Queue()
        _SUBAGENT_OUTPUTS[task_id] = out_q

    # Register in deepagents' _TASK_REGISTRY so watcher quiescence check waits
    coro = _run_subagent(
        task_id=task_id,
        agent_name=agent_name,
        model=model,
        system_prompt=system_prompt,
        input_message=input_message,
        parent_run_id=parent_run_id,
        parent_thread_id=parent_thread_id,
        parent_bus=parent_bus,
        checkpointer=checkpointer,
        timeout=timeout,
        label=label,
        pipeline_id=pipeline_id,
        depends_on=depends_on,
        parent_api_key=parent_api_key,
        parent_base_url=parent_base_url,
    )
    task = asyncio.create_task(coro, name=f"subagent-{task_id[:8]}")

    with _NOTIF_LOCK:
        _TASK_REGISTRY[task_id] = task
        _TASK_AGENT_NAMES[task_id] = agent_name

    with _SUBAGENT_LOCK:
        _SUBAGENT_TASKS[task_id] = task

    return task


async def resume_interrupted_subagent_with_decision(task_id: str, decision: dict) -> None:
    """Resume a recovered-interrupt subagent using a stored LangGraph checkpoint.

    Called by routes.py when POST /subagents/{id}/interrupt arrives but no live
    HITL future exists (task was restored from tasks.db after server restart).
    """
    with _SUBAGENT_LOCK:
        graph_info = _SUBAGENT_GRAPHS.get(task_id)
        agent_name = (_SUBAGENT_REGISTRY.get(task_id) or {}).get("agent_name", "")
    if graph_info is None:
        logger.warning("Cannot resume recovered task %s: graph not found", task_id[:12])
        return
    graph, config = graph_info
    with _SUBAGENT_LOCK:
        existing_bus = _SUBAGENT_BUSES.get(task_id)
    from rai.harness.sse import RunEventBus as _Bus
    bus = existing_bus or _Bus.create(task_id)

    coro = _resume_subagent(
        task_id=task_id,
        message=Command(resume={"decisions": [decision]}),
        graph=graph,
        config=config,
        agent_name=agent_name,
        parent_bus=bus,
    )
    task = asyncio.create_task(coro, name=f"subagent-hitl-{task_id[:8]}")
    with _SUBAGENT_LOCK:
        _SUBAGENT_TASKS[task_id] = task
    with _NOTIF_LOCK:
        _TASK_REGISTRY[task_id] = task
        _TASK_AGENT_NAMES[task_id] = agent_name


async def recover_incomplete_subagents(checkpointer: Any) -> None:
    """Re-hydrate in-memory registries from tasks.db on server startup.

    For each row with status 'running' or 'interrupted':
    - Recompile the agent graph from sessions.db checkpoint
    - Restore _SUBAGENT_REGISTRY, _SUBAGENT_GRAPHS, _SUBAGENT_BUSES, _SUBAGENT_OUTPUTS
    - Detect actual checkpoint state and mark completed/failed if appropriate
    """
    _ts = _subagent_registry._TASK_STORE
    if _ts is None:
        return

    from rai.engine.factory import create_rai_agent
    from rai.sessions.store import build_stream_config
    from rai.harness.sse import RunEventBus as _Bus

    rows = await _ts.list_incomplete()
    if not rows:
        return

    logger.info("Recovery: found %d incomplete subagent task(s) in tasks.db", len(rows))

    for row in rows:
        task_id = row["task_id"]
        agent_name = row.get("agent_name", "")
        model = row.get("model", "")
        system_prompt = row.get("system_prompt")
        cwd = row.get("cwd", "")

        try:
            from rai.config.agent import load_agent_config as _load_cfg
            try:
                _cfg = _load_cfg(agent_name)
                api_key  = (_cfg.api_key  if _cfg and _cfg.api_key  else "")
                base_url = (_cfg.base_url if _cfg and _cfg.base_url else "")
            except Exception:
                api_key = base_url = ""

            from rai import DEFAULT_MODEL
            graph, _ = create_rai_agent(
                model=model or DEFAULT_MODEL,
                agent_name=agent_name,
                system_prompt=system_prompt or None,
                api_key=api_key,
                base_url=base_url,
                auto_approve=False,
                disable_subagents=True,
                suppress_local_async=True,
                is_subagent=True,       # skips OPPLAN middleware/tools without enabling plan-mode interceptor
                checkpointer=checkpointer,
                interactive=False,
            )

            config = build_stream_config(task_id, agent_name, cwd=cwd or str(Path.cwd()), recursion_limit=50)

            snapshot = await graph.aget_state(config)
            if snapshot is None or not snapshot.values:
                logger.warning("Recovery: no checkpoint for task %s — marking failed", task_id[:12])
                await _ts.update_status(task_id, "failed")
                continue

            has_interrupts = bool(snapshot.tasks and snapshot.tasks[0].interrupts)
            has_next = bool(snapshot.next)

            # Defaults from DB row — overridden below for the completed case
            _recovered_output: str | None = row.get("output")
            _recovered_output_file: str = row.get("output_file", "")

            if has_interrupts:
                recovery_status = "interrupted"
            elif not has_next:
                msgs = (snapshot.values or {}).get("messages", [])
                _ckpt_output = ""
                for msg in reversed(msgs):
                    if isinstance(msg, AIMessage):
                        _ckpt_output = _extract_text(msg.content)
                        break
                _ckpt_out_path = Path(f"/tmp/rai_subagent_{task_id[:12]}.json")
                try:
                    _ckpt_out_path.write_text(json.dumps({
                        "status": "completed",
                        "output": _ckpt_output,
                        "task_id": task_id,
                    }))
                except Exception:
                    pass
                await _ts.update_output(task_id, "completed", _ckpt_output, str(_ckpt_out_path))
                _recovered_output = _ckpt_output
                _recovered_output_file = str(_ckpt_out_path)
                recovery_status = "completed"
            else:
                await _ts.update_status(task_id, "failed")
                recovery_status = "failed"

            depends_on_raw = row.get("depends_on")
            depends_on = json.loads(depends_on_raw) if depends_on_raw else None

            meta: dict = {
                "task_id":          task_id,
                "agent_name":       agent_name,
                "parent_run_id":    row.get("parent_run_id", ""),
                "parent_thread_id": row.get("parent_thread_id", ""),
                "status":           recovery_status,
                "created_at":       row.get("created_at", ""),
                "input":            row.get("input", ""),
                "output":           _recovered_output,
                "output_file":      _recovered_output_file,
                "label":            row.get("label"),
                "pipeline_id":      row.get("pipeline_id"),
                "depends_on":       depends_on,
            }

            bus = _Bus.create(task_id)
            out_q: asyncio.Queue[str] = asyncio.Queue()

            with _SUBAGENT_LOCK:
                _SUBAGENT_REGISTRY[task_id] = meta  # type: ignore[assignment]
                _SUBAGENT_GRAPHS[task_id] = (graph, config)
                _SUBAGENT_BUSES[task_id] = bus
                _SUBAGENT_OUTPUTS[task_id] = out_q

            logger.info(
                "Recovery: restored task %s (%s) as '%s'",
                task_id[:12], agent_name, recovery_status,
            )

        except Exception as exc:
            logger.warning("Recovery: failed to restore task %s: %s", task_id[:12], exc)
