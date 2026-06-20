"""Core subagent executor — compile, stream, HITL loop, notify."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from rai.harness.subagents_v2.models import (
    TERMINAL_STATUSES,
    CompletionNotification,
    _now,
)
from rai.harness.subagents_v2.service import SubagentService

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 3600.0


def _extract_text(content: Any) -> str:
    """Extract plain text from a LangChain message content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        thinking_parts = []
        for item in content:
            if isinstance(item, dict):
                btype = item.get("type", "")
                if btype == "text" and item.get("text"):
                    text_parts.append(item["text"])
                elif btype == "thinking" and item.get("thinking"):
                    thinking_parts.append(item["thinking"])
                elif item.get("text"):
                    text_parts.append(item["text"])
                elif item.get("thinking"):
                    thinking_parts.append(item["thinking"])
            elif item:
                text_parts.append(str(item))
        return "".join(text_parts) or "".join(thinking_parts)
    return str(content) if content else ""


async def _emit_both(
    task_id: str,
    parent_bus: Any,
    event_type: str,
    data: dict,
) -> None:
    """Publish event to per-subagent bus AND parent run bus."""
    service = SubagentService.instance()
    sub_bus = service.get_bus(task_id)
    if sub_bus:
        await sub_bus.publish(event_type, data)
    await parent_bus.publish(event_type, data)


async def _stream_subagent(
    task_id: str,
    graph: Any,
    config: dict,
    msg: str | Command,
    parent_bus: Any,
    *,
    text_accumulator: list[str] | None = None,
) -> None:
    """Stream one turn of the subagent, fanning events to both buses.

    If text_accumulator is provided, appends all non-thinking text tokens to it
    so the caller has a fallback output even if checkpoint extraction fails.
    """
    if isinstance(msg, Command):
        event_stream = graph.astream_events(msg, config, version="v2")
    else:
        event_stream = graph.astream_events(
            {"messages": [HumanMessage(content=msg)]}, config, version="v2"
        )

    # Track whether we're in a tool call — only accumulate text from the FINAL
    # model response (after all tools complete), not intermediate reasoning.
    _in_tool_sequence = False

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
                    if text_accumulator is not None and btype != "thinking":
                        text_accumulator.append(text)
            elif content:
                await _emit_both(task_id, parent_bus, "subagent_token", {
                    "task_id": task_id, "content": content
                })
                if text_accumulator is not None:
                    text_accumulator.append(content)

        elif kind == "on_chat_model_end":
            # Model finished a response — check if it has tool_calls
            # If yes, more tool execution follows; clear accumulator for next round
            resp = event["data"].get("output")
            if resp and hasattr(resp, "tool_calls") and resp.tool_calls:
                _in_tool_sequence = True
                if text_accumulator is not None:
                    text_accumulator.clear()
            else:
                _in_tool_sequence = False

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


async def run_subagent(
    task_id: str,
    agent_name: str,
    input_message: str,
    parent_run_id: str,
    parent_thread_id: str,
    parent_bus: Any,
    *,
    model: str = "",
    system_prompt: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    label: str | None = None,
    pipeline_id: str | None = None,
    depends_on: list[str] | None = None,
    parent_api_key: str = "",
    parent_base_url: str = "",
    parent_model: str = "",
) -> None:
    """Inner coroutine wrapped by asyncio.create_task() in tools.

    Compiles the subagent, streams its execution, handles HITL,
    and pushes CompletionNotification on finish.
    """
    from rai import DEFAULT_MODEL
    from rai.engine.factory import create_rai_agent
    from rai.harness.sse import RunEventBus
    from rai.sessions.store import build_stream_config

    service = SubagentService.instance()
    out_path = Path(f"/tmp/rai_subagent_{task_id[:12]}.json")
    status = "failed"
    output = ""

    try:
        # 1. Credential resolution
        from rai.config.agent import load_agent_config as _load_cfg
        try:
            _cfg = _load_cfg(agent_name)
            effective_api_key = (_cfg.api_key if _cfg and _cfg.api_key else "") or parent_api_key
            effective_base_url = (_cfg.base_url if _cfg and _cfg.base_url else "") or parent_base_url
        except Exception:
            effective_api_key = parent_api_key
            effective_base_url = parent_base_url

        resolved_model = model or parent_model or DEFAULT_MODEL

        # 2. MCP tools
        _sub_mcp_tools: list = []
        try:
            from rai.mcp.loader import resolve_and_load_mcp_tools as _load_mcp
            _sub_mcp_tools, _, _ = await _load_mcp(agent_name=agent_name)
        except Exception as _mcp_exc:
            logger.debug("Subagent '%s' MCP load skipped: %s", agent_name, _mcp_exc)

        # 3. Compile subagent graph
        _cp = service._checkpointer
        if _cp is None:
            from langgraph.checkpoint.memory import MemorySaver
            _cp = MemorySaver()
        logger.info(
            "Subagent %s (%s) compiling graph — checkpointer=%s, model=%s",
            task_id[:12], agent_name,
            type(_cp).__name__,
            resolved_model if isinstance(resolved_model, str) else type(resolved_model).__name__,
        )
        graph, _ = create_rai_agent(
            model=resolved_model,
            agent_name=agent_name,
            system_prompt=system_prompt or None,
            api_key=effective_api_key,
            base_url=effective_base_url,
            disable_subagents=True,
            suppress_local_async=True,
            is_subagent=True,
            checkpointer=_cp,
            interactive=False,
            extra_tools=_sub_mcp_tools or None,
        )

        config = build_stream_config(task_id, agent_name, cwd=str(Path.cwd()), recursion_limit=9999)
        service.register_graph(task_id, graph, config)

        # 3b. Store output_file path in task_meta (registered by tools.py without it)
        meta = service.get_task_meta(task_id)
        if meta:
            meta["output_file"] = str(out_path)

        # 4. Create per-task SSE bus
        sub_bus = RunEventBus.create(task_id)
        service.register_bus(task_id, sub_bus)

        # 5. Persist to task store
        if service._task_store is not None:
            try:
                await service._task_store.upsert({
                    "task_id": task_id,
                    "agent_name": agent_name,
                    "parent_run_id": parent_run_id,
                    "parent_thread_id": parent_thread_id,
                    "status": "running",
                    "created_at": _now(),
                    "input": input_message,
                    "output": None,
                    "output_file": str(out_path),
                    "label": label,
                    "pipeline_id": pipeline_id,
                    "depends_on": json.dumps(depends_on) if depends_on else None,
                    "model": resolved_model,
                    "system_prompt": system_prompt,
                    "cwd": str(Path.cwd()),
                })
            except Exception:
                pass

        # 6. Emit start event
        await _emit_both(task_id, parent_bus, "subagent_started", {
            "task_id": task_id,
            "agent_name": agent_name,
            "input": input_message,
            "parent_run_id": parent_run_id,
            "model": resolved_model,
        })

        # 7. Stream initial turn
        _streamed_text: list[str] = []  # accumulates final response text as fallback
        logger.info("Subagent %s (%s) starting initial stream...", task_id[:12], agent_name)
        try:
            await asyncio.wait_for(
                _stream_subagent(task_id, graph, config, input_message, parent_bus, text_accumulator=_streamed_text),
                timeout=timeout,
            )
            logger.info("Subagent %s (%s) initial stream completed normally", task_id[:12], agent_name)
        except GraphInterrupt:
            logger.info("Subagent %s (%s) initial stream hit GraphInterrupt", task_id[:12], agent_name)
        except Exception as stream_exc:
            logger.error("Subagent %s (%s) initial stream error: %s", task_id[:12], agent_name, stream_exc)
            raise

        # 8. HITL loop
        from rai.harness.runner import _SESSION_APPROVED as _SA
        while True:
            snapshot = await graph.aget_state(config)
            has_interrupts = bool(snapshot.tasks and snapshot.tasks[0].interrupts)
            logger.info(
                "Subagent %s HITL check: has_tasks=%s, has_interrupts=%s, next=%s",
                task_id[:12], bool(snapshot.tasks), has_interrupts,
                list(snapshot.next) if snapshot.next else "[]",
            )
            if not has_interrupts:
                break

            intr = snapshot.tasks[0].interrupts[0]
            _intr_payload = (
                json.dumps(intr.value, sort_keys=True, default=str)
                if isinstance(intr.value, dict) else str(intr.value)
            )
            interrupt_id = hashlib.sha256(_intr_payload.encode()).hexdigest()[:16]
            action_requests = (
                intr.value.get("action_requests", [])
                if isinstance(intr.value, dict) else []
            )

            # Auto-bypass if all tools are session-approved
            _req_names = [ar.get("name", "") for ar in action_requests if ar.get("name")]
            if _req_names and all(t in _SA.get(parent_thread_id, set()) for t in _req_names):
                _num_auto = len(action_requests) if action_requests else 1
                try:
                    await asyncio.wait_for(
                        _stream_subagent(
                            task_id, graph, config,
                            Command(resume={"decisions": [{"type": "approve"}] * _num_auto}),
                            parent_bus,
                            text_accumulator=_streamed_text,
                        ),
                        timeout=timeout,
                    )
                except GraphInterrupt:
                    pass
                continue

            # Create HITL future and wait
            fut = service.create_hitl_future(task_id)
            service.update_task_status(task_id, "interrupted")

            await _emit_both(task_id, parent_bus, "subagent_interrupt", {
                "task_id": task_id,
                "agent_name": agent_name,
                "interrupt_id": interrupt_id,
                "action_requests": action_requests,
            })

            if service._task_store is not None:
                try:
                    await service._task_store.update_status(task_id, "interrupted")
                except Exception:
                    pass

            try:
                decision = await asyncio.wait_for(fut, timeout=3600.0)
            except asyncio.TimeoutError:
                logger.warning("Subagent HITL timed out for task %s", task_id)
                raise
            except asyncio.CancelledError:
                status = "cancelled"
                raise

            # Resume with decision
            service.update_task_status(task_id, "running")
            dtype = decision.get("type", "approve")
            num_actions = len(action_requests) if action_requests else 1

            if dtype == "approve":
                resume_val = {"decisions": [{"type": "approve"}] * num_actions}
            elif dtype == "approve_session":
                for ar in action_requests:
                    name = ar.get("name", "")
                    if name:
                        _SA.setdefault(parent_thread_id, set()).add(name)
                resume_val = {"decisions": [{"type": "approve"}] * num_actions}
            elif dtype == "reject":
                msg = decision.get("message", "Rejected by user")
                resume_val = {"decisions": [{"type": "deny", "message": msg}] * num_actions}
            elif dtype == "edit":
                edits = decision.get("edits", {})
                resume_val = {"decisions": [{"type": "edit", "args": edits}]}
            elif dtype == "respond":
                respond_msg = decision.get("message", "")
                resume_val = {"decisions": [{"type": "respond", "args": respond_msg}]}
            else:
                resume_val = {"decisions": [{"type": "approve"}] * num_actions}

            try:
                await asyncio.wait_for(
                    _stream_subagent(
                        task_id, graph, config,
                        Command(resume=resume_val),
                        parent_bus,
                        text_accumulator=_streamed_text,
                    ),
                    timeout=timeout,
                )
            except GraphInterrupt:
                pass

        # 8b. Output discipline enforcement (same thread — full context preserved)
        _md_out_path = Path(f"/tmp/rai_{task_id}_output.md")
        if not _md_out_path.exists():
            logger.info("Subagent %s: enforcing output discipline", task_id[:12])
            _enforcement_msg = (
                f'<output-discipline-enforcement>\n'
                f'"""\n'
                f'You have NOT saved your output summary.\n'
                f'Write it NOW to /tmp/rai_{task_id}_output.md using bash.\n'
                f'\n'
                f'Include: Objective, Actions taken, Findings, Recommendations.\n'
                f'You have full context of your work above — summarize it into the file.\n'
                f'"""\n'
                f'</output-discipline-enforcement>'
            )
            try:
                await asyncio.wait_for(
                    _stream_subagent(
                        task_id, graph, config,
                        _enforcement_msg,
                        parent_bus,
                        text_accumulator=_streamed_text,
                    ),
                    timeout=min(120.0, timeout),
                )
            except (GraphInterrupt, asyncio.TimeoutError):
                pass
            except Exception as _enf_exc:
                logger.debug("Subagent %s enforcement failed: %s", task_id[:12], _enf_exc)

        # 9. Extract output — .md discipline file takes priority
        _md_output_used = False
        if _md_out_path.exists():
            try:
                _md_content = _md_out_path.read_text(encoding="utf-8")
                if _md_content.strip():
                    output = _md_content
                    _md_output_used = True
                    logger.info(
                        "Subagent %s using .md output (%d chars)",
                        task_id[:12], len(output),
                    )
            except Exception:
                pass

        if not _md_output_used:
            # 9b. Checkpoint extraction fallback
            snapshot = await graph.aget_state(config)
            messages = snapshot.values.get("messages", [])
            logger.info(
                "Subagent %s (%s) finished — %d messages in checkpoint",
                task_id[:12], agent_name, len(messages),
            )
            if messages:
                last = messages[-1]
                output = _extract_text(getattr(last, "content", ""))
                if not output and len(messages) > 1:
                    for msg in reversed(messages[:-1]):
                        text = _extract_text(getattr(msg, "content", ""))
                        if text and type(msg).__name__ == "AIMessage":
                            output = text
                            break
                if not output and len(messages) > 1:
                    for msg in reversed(messages):
                        if type(msg).__name__ == "ToolMessage":
                            text = _extract_text(getattr(msg, "content", ""))
                            if text and len(text) > 20:
                                output = f"[Tool output]: {text}"
                                break
                if not output:
                    tool_names = []
                    for msg in messages:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                                if name and name not in tool_names:
                                    tool_names.append(name)
                    if tool_names:
                        output = f"Subagent executed {len(messages)} steps using tools: {', '.join(tool_names[:15])}"

            # Streaming text fallback
            if not output and _streamed_text:
                output = "".join(_streamed_text)
                logger.info(
                    "Subagent %s using streaming fallback (%d chars)",
                    task_id[:12], len(output),
                )

        status = "completed"

    except asyncio.CancelledError:
        status = "cancelled"
    except asyncio.TimeoutError:
        status = "timeout"
    except Exception as exc:
        status = "failed"
        output = str(exc)
        logger.exception("Subagent %s (%s) failed", task_id, agent_name)
    finally:
        # Write output file
        try:
            out_path.write_text(
                json.dumps({"status": status, "output": output, "task_id": task_id}),
                encoding="utf-8",
            )
        except OSError:
            pass

        # Push completion notification BEFORE marking status terminal.
        # The watcher loop checks has_active_tasks() which reads status —
        # if status goes terminal before notification is queued, the watcher
        # may break its loop without ever draining the queue.
        notification = CompletionNotification(
            task_id=task_id,
            agent_name=agent_name,
            status=status,
            output_preview=output[:400],
            output_file=str(out_path),
            label=label,
        )
        service.push_completion(parent_run_id, notification)

        # Emit SSE completion event (before status update so TUI sees it)
        await _emit_both(task_id, parent_bus, "subagent_completed", {
            "task_id": task_id,
            "agent_name": agent_name,
            "status": status,
            "output_preview": output[:400],
        })

        # NOW update service state to terminal
        service.update_task_status(task_id, status, output[:400])
        service.cleanup_task(task_id)

        # Persist to task store
        if service._task_store is not None:
            try:
                await service._task_store.update_output(
                    task_id, status, output[:2000], str(out_path)
                )
            except Exception:
                pass

        # Close per-task bus (sync method, requires run_id)
        sub_bus = service.get_bus(task_id)
        if sub_bus:
            sub_bus.close(task_id)
