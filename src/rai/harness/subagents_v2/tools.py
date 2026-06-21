"""LLM-facing tools for async subagent management.

All tools return Command(update={"async_tasks": {...}}) to persist state in
the parent agent's LangGraph graph. Tool names are set via @tool("Name") so
the LLM sees clean, disciplined names.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.types import Command

from rai.harness.subagents_v2.models import (
    TERMINAL_STATUSES,
    AsyncTask,
    CompletionNotification,
    _now,
    make_task,
)
from rai.harness.subagents_v2.service import SubagentService


def _get_run_context() -> dict[str, Any]:
    """Get the current run context from ContextVar."""
    from rai.harness.subagents.registry import _RUN_CONTEXT
    ctx = _RUN_CONTEXT.get()
    if ctx is None:
        return {}
    return dict(ctx)


def _output_discipline_suffix(task_id: str) -> str:
    """Instruction appended to subagent task prompts for structured output."""
    return (
        f'\n\n<output-discipline>\n'
        f'"""\n'
        f'MANDATORY: Before ending your work, save a structured summary to:\n'
        f'  /tmp/rai_{task_id}_output.md\n'
        f'\n'
        f'Use bash to write it (e.g. cat << \'EOF\' > /tmp/rai_{task_id}_output.md).\n'
        f'\n'
        f'Required sections:\n'
        f'- Objective: what you were asked to do\n'
        f'- Actions: what you did (tools used, files read/written)\n'
        f'- Findings: key results, data, discoveries\n'
        f'- Recommendations: next steps or concerns\n'
        f'\n'
        f'This file is how the parent agent reads your results. Do NOT skip this step.\n'
        f'"""\n'
        f'</output-discipline>'
    )


def _resolve_task(task_id_or_label: str, state: dict) -> AsyncTask | None:
    """Resolve a task by ID or label from graph state."""
    tasks: dict[str, AsyncTask] = state.get("async_tasks") or {}
    if task_id_or_label in tasks:
        return tasks[task_id_or_label]
    for t in tasks.values():
        if t.get("label") == task_id_or_label:
            return t
    return None


def build_subagent_tools(
    subagents: list[dict[str, Any]],
) -> list[BaseTool]:
    """Build the 10 subagent tools with subagent descriptions baked in.

    Args:
        subagents: List of subagent spec dicts with at least {name, description}.
    """
    agent_names = [s["name"] for s in subagents]
    agents_desc = "\n".join(
        f"- {s['name']}: {s.get('description', '')[:100]}" for s in subagents
    )

    # ------------------------------------------------------------------

    @tool("Subagent")
    async def subagent(
        prompt: str,
        agent: str,
        label: str = "",
        model: str = "",
        system_prompt: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command:
        """Launch a subagent in the background. Returns immediately with a task_id.
        You'll be notified via <task-notification> when it completes."""
        if agent not in agent_names:
            return Command(update={
                "messages": [ToolMessage(
                    f"Unknown agent '{agent}'. Available: {', '.join(agent_names)}",
                    tool_call_id=tool_call_id,
                )],
            })

        ctx = _get_run_context()
        if not ctx:
            return Command(update={
                "messages": [ToolMessage(
                    "No run context — cannot spawn subagent outside HTTP mode.",
                    tool_call_id=tool_call_id,
                )],
            })

        service = SubagentService.instance()
        task_id = uuid.uuid4().hex[:16]
        prompt = prompt + _output_discipline_suffix(task_id)

        from rai.harness.subagents_v2.executor import run_subagent

        asyncio_task = asyncio.create_task(
            run_subagent(
                task_id=task_id,
                agent_name=agent,
                input_message=prompt,
                parent_run_id=ctx.get("run_id", ""),
                parent_thread_id=ctx.get("thread_id", ""),
                parent_bus=ctx.get("parent_bus"),
                model=model,
                system_prompt=system_prompt or None,
                label=label or None,
                parent_api_key=ctx.get("parent_api_key", ""),
                parent_base_url=ctx.get("parent_base_url", ""),
                parent_model=ctx.get("parent_model", ""),
            )
        )

        service.register_task(
            task_id,
            asyncio_task,
            agent_name=agent,
            parent_run_id=ctx.get("run_id", ""),
            parent_thread_id=ctx.get("thread_id", ""),
            label=label or None,
            input_message=prompt,
        )

        task_meta = make_task(task_id, agent, label=label or None)
        msg = f"Launched background agent '{agent}'. task_id: {task_id}"
        if label:
            msg += f" (label: {label})"

        return Command(
            update={
                "messages": [ToolMessage(msg, tool_call_id=tool_call_id)],
                "async_tasks": {task_id: task_meta},
            }
        )

    # Update description to include available agents
    subagent.description = (
        f"Launch a subagent in the background. Returns immediately with a task_id. "
        f"You'll be notified via <task-notification> when it completes.\n\n"
        f"Available agents:\n{agents_desc}"
    )

    # ------------------------------------------------------------------

    @tool("MultiSubagents")
    async def multi_subagents(
        tasks: list[dict[str, str]],
        model: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command:
        """Launch multiple subagents in parallel. Each task dict needs:
        {agent, prompt, label (optional)}. All run concurrently."""
        ctx = _get_run_context()
        if not ctx:
            return Command(update={
                "messages": [ToolMessage("No run context.", tool_call_id=tool_call_id)],
            })

        service = SubagentService.instance()
        results = []
        task_updates: dict[str, AsyncTask] = {}

        from rai.harness.subagents_v2.executor import run_subagent

        for spec in tasks:
            agent = spec.get("agent", "rai")
            prompt = spec.get("prompt", "")
            label = spec.get("label", "")
            if agent not in agent_names:
                results.append(f"Skipped unknown agent '{agent}'")
                continue
            if not prompt:
                results.append("Skipped task with empty prompt")
                continue

            task_id = uuid.uuid4().hex[:16]
            prompt = prompt + _output_discipline_suffix(task_id)
            asyncio_task = asyncio.create_task(
                run_subagent(
                    task_id=task_id,
                    agent_name=agent,
                    input_message=prompt,
                    parent_run_id=ctx.get("run_id", ""),
                    parent_thread_id=ctx.get("thread_id", ""),
                    parent_bus=ctx.get("parent_bus"),
                    model=model or spec.get("model", ""),
                    label=label or None,
                    parent_api_key=ctx.get("parent_api_key", ""),
                    parent_base_url=ctx.get("parent_base_url", ""),
                    parent_model=ctx.get("parent_model", ""),
                )
            )
            service.register_task(
                task_id,
                asyncio_task,
                agent_name=agent,
                parent_run_id=ctx.get("run_id", ""),
                parent_thread_id=ctx.get("thread_id", ""),
                label=label or None,
                input_message=prompt,
            )
            task_meta = make_task(task_id, agent, label=label or None)
            task_updates[task_id] = task_meta
            results.append(f"Launched '{agent}' → {task_id}" + (f" ({label})" if label else ""))

        msg = f"Launched {len(task_updates)} subagents:\n" + "\n".join(results)
        return Command(update={
            "messages": [ToolMessage(msg, tool_call_id=tool_call_id)],
            "async_tasks": task_updates,
        })

    # ------------------------------------------------------------------

    @tool("Pipeline")
    async def pipeline(
        tasks: list[dict[str, Any]],
        pipeline_id: str = "",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command:
        """Launch a DAG pipeline of subagent tasks with dependency ordering."""
        ctx = _get_run_context()
        if not ctx:
            return Command(update={
                "messages": [ToolMessage("No run context.", tool_call_id=tool_call_id)],
            })

        if not pipeline_id:
            pipeline_id = uuid.uuid4().hex[:16]

        service = SubagentService.instance()
        task_updates: dict[str, AsyncTask] = {}

        from rai.harness.subagents_v2.executor import run_subagent

        # Pass 1: pre-generate all task_ids so deps can reference them by label.
        # Only map labels for valid agents — unknown agents are skipped in Pass 2
        # and must not create phantom task_ids that waiters would poll for forever.
        label_to_task_id: dict[str, str] = {}
        spec_task_ids: list[str] = []
        for spec in tasks:
            tid = uuid.uuid4().hex[:16]
            spec_task_ids.append(tid)
            if spec.get("agent", "rai") in agent_names:
                lbl = spec.get("label", "")
                if lbl:
                    label_to_task_id[lbl] = tid

        # Pass 2: launch tasks, gating dependents behind their deps
        results = []
        for spec, task_id in zip(tasks, spec_task_ids):
            agent = spec.get("agent", "rai")
            prompt = spec.get("prompt", "")
            label = spec.get("label", "")
            deps = spec.get("depends_on", [])

            if agent not in agent_names:
                results.append(f"Skipped unknown agent '{agent}'")
                continue

            prompt = prompt + _output_discipline_suffix(task_id)
            run_kwargs: dict[str, Any] = dict(
                task_id=task_id,
                agent_name=agent,
                input_message=prompt,
                parent_run_id=ctx.get("run_id", ""),
                parent_thread_id=ctx.get("thread_id", ""),
                parent_bus=ctx.get("parent_bus"),
                label=label or None,
                pipeline_id=pipeline_id,
                depends_on=deps or None,
                parent_api_key=ctx.get("parent_api_key", ""),
                parent_base_url=ctx.get("parent_base_url", ""),
                parent_model=ctx.get("parent_model", ""),
            )

            dep_task_ids = [label_to_task_id[lbl] for lbl in deps if lbl in label_to_task_id]

            if dep_task_ids:
                async def _run_after_deps(
                    _dep_ids: list[str] = dep_task_ids,
                    _kwargs: dict[str, Any] = run_kwargs,
                ) -> None:
                    while True:
                        if all(
                            (m := service.get_task_meta(d)) and m.get("status") in TERMINAL_STATUSES
                            for d in _dep_ids
                        ):
                            break
                        await asyncio.sleep(1.0)
                    await run_subagent(**_kwargs)

                asyncio_task = asyncio.create_task(_run_after_deps())
            else:
                asyncio_task = asyncio.create_task(run_subagent(**run_kwargs))

            service.register_task(
                task_id,
                asyncio_task,
                agent_name=agent,
                parent_run_id=ctx.get("run_id", ""),
                parent_thread_id=ctx.get("thread_id", ""),
                label=label or None,
                pipeline_id=pipeline_id,
                depends_on=deps or None,
                input_message=prompt,
            )
            task_meta = make_task(task_id, agent, label=label or None, pipeline_id=pipeline_id, depends_on=deps or None)
            task_updates[task_id] = task_meta
            dep_str = f" (waits for: {deps})" if dep_task_ids else ""
            results.append(f"  {agent} → {task_id}{dep_str}")

        msg = f"Pipeline '{pipeline_id}' launched ({len(task_updates)} tasks):\n" + "\n".join(results)
        return Command(update={
            "messages": [ToolMessage(msg, tool_call_id=tool_call_id)],
            "async_tasks": task_updates,
        })

    # ------------------------------------------------------------------

    @tool("SubagentStatus")
    async def subagent_status(
        task_id: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command:
        """Get the current status and output of a background subagent task."""
        service = SubagentService.instance()
        meta = service.get_task_meta(task_id)

        if meta is None:
            return Command(update={
                "messages": [ToolMessage(
                    f"No task found with ID '{task_id}'.",
                    tool_call_id=tool_call_id,
                )],
            })

        status = meta.get("status", "unknown")
        now = _now()

        output = ""
        if status in TERMINAL_STATUSES:
            # Priority: .md discipline output
            md_path = Path(f"/tmp/rai_{task_id}_output.md")
            if md_path.exists():
                try:
                    md_content = md_path.read_text(encoding="utf-8")
                    if md_content.strip():
                        output = md_content
                except OSError:
                    pass
            # Fallback: .json output file
            if not output:
                out_file = meta.get("output_file", "")
                if out_file:
                    try:
                        data = json.loads(Path(out_file).read_text(encoding="utf-8"))
                        output = data.get("output", "")
                    except (OSError, json.JSONDecodeError):
                        pass
            if not output:
                output = meta.get("output_preview", "")

        result = {
            "task_id": task_id,
            "agent_name": meta.get("agent_name", ""),
            "status": status,
            "label": meta.get("label"),
        }
        if output:
            result["output"] = output[:4000]

        updated = AsyncTask(
            task_id=task_id,
            agent_name=meta.get("agent_name", ""),
            thread_id=task_id,
            run_id=task_id,
            status=status,
            created_at=meta.get("created_at", now),
            last_checked_at=now,
            last_updated_at=now if status != meta.get("status") else meta.get("created_at", now),
            label=meta.get("label"),
            pipeline_id=meta.get("pipeline_id"),
            depends_on=meta.get("depends_on"),
            output_preview=output[:400] if output else meta.get("output_preview", ""),
        )

        msg = json.dumps(result, default=str)
        return Command(update={
            "messages": [ToolMessage(msg, tool_call_id=tool_call_id)],
            "async_tasks": {task_id: updated},
        })

    # ------------------------------------------------------------------

    @tool("SubagentCancel")
    async def subagent_cancel(
        task_id: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command:
        """Cancel a running background subagent task."""
        service = SubagentService.instance()
        meta = service.get_task_meta(task_id)
        if meta is None:
            return Command(update={
                "messages": [ToolMessage(f"No task found: '{task_id}'", tool_call_id=tool_call_id)],
            })
        if meta.get("status") in TERMINAL_STATUSES:
            return Command(update={
                "messages": [ToolMessage(
                    f"Task {task_id} already in terminal state '{meta['status']}'.",
                    tool_call_id=tool_call_id,
                )],
            })

        await service.cancel_task(task_id)
        updated = AsyncTask(
            task_id=task_id,
            agent_name=meta.get("agent_name", ""),
            thread_id=task_id,
            run_id=task_id,
            status="cancelled",
            created_at=meta.get("created_at", ""),
            last_checked_at=_now(),
            last_updated_at=_now(),
            label=meta.get("label"),
            pipeline_id=meta.get("pipeline_id"),
            depends_on=meta.get("depends_on"),
            output_preview="",
        )
        return Command(update={
            "messages": [ToolMessage(f"Cancelled task {task_id}.", tool_call_id=tool_call_id)],
            "async_tasks": {task_id: updated},
        })

    # ------------------------------------------------------------------

    @tool("SubagentUpdate")
    async def subagent_update(
        task_id: str,
        message: str,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command:
        """Send a follow-up message to a subagent after its turn completes.
        Resumes from checkpoint — full history preserved."""
        service = SubagentService.instance()
        meta = service.get_task_meta(task_id)
        if meta is None:
            return Command(update={
                "messages": [ToolMessage(f"No task found: '{task_id}'", tool_call_id=tool_call_id)],
            })

        ctx = _get_run_context()

        from rai.harness.subagents_v2.executor import run_subagent

        asyncio_task = asyncio.create_task(
            run_subagent(
                task_id=task_id,
                agent_name=meta.get("agent_name", ""),
                input_message=message,
                parent_run_id=ctx.get("run_id", ""),
                parent_thread_id=ctx.get("thread_id", ""),
                parent_bus=ctx.get("parent_bus"),
                label=meta.get("label"),
                parent_api_key=ctx.get("parent_api_key", ""),
                parent_base_url=ctx.get("parent_base_url", ""),
                parent_model=ctx.get("parent_model", ""),
            )
        )
        service.set_active_task(task_id, asyncio_task)
        service.update_task_status(task_id, "running")

        updated = AsyncTask(
            task_id=task_id,
            agent_name=meta.get("agent_name", ""),
            thread_id=task_id,
            run_id=task_id,
            status="running",
            created_at=meta.get("created_at", ""),
            last_checked_at=_now(),
            last_updated_at=_now(),
            label=meta.get("label"),
            pipeline_id=meta.get("pipeline_id"),
            depends_on=meta.get("depends_on"),
            output_preview="",
        )
        return Command(update={
            "messages": [ToolMessage(f"Resumed task {task_id} with follow-up.", tool_call_id=tool_call_id)],
            "async_tasks": {task_id: updated},
        })

    # ------------------------------------------------------------------

    @tool("SubagentResponse")
    async def subagent_response(
        task_id: str,
        timeout_seconds: int = 120,
    ) -> str:
        """Wait for a subagent's response (blocks until reply or timeout)."""
        service = SubagentService.instance()
        meta = service.get_task_meta(task_id)
        if meta is None:
            return f"No task found: '{task_id}'"

        if meta.get("status") in TERMINAL_STATUSES:
            # Priority: .md discipline output
            md_path = Path(f"/tmp/rai_{task_id}_output.md")
            if md_path.exists():
                try:
                    md_content = md_path.read_text(encoding="utf-8")
                    if md_content.strip():
                        return md_content
                except OSError:
                    pass
            # Fallback: .json output file
            out_file = meta.get("output_file", "")
            if out_file:
                try:
                    data = json.loads(Path(out_file).read_text(encoding="utf-8"))
                    out_text = data.get("output", "")
                    if out_text:
                        return out_text
                except (OSError, json.JSONDecodeError):
                    pass
            # Fallback: output_preview in task_meta
            preview = meta.get("output_preview", "")
            if preview:
                return preview
            return f"(task {meta.get('status', 'completed')} — no output captured)"

        ctx = _get_run_context()
        run_id = ctx.get("run_id", "")
        notif = await service.wait_completion(run_id, timeout=float(timeout_seconds))

        if notif and notif["task_id"] == task_id:
            return notif.get("output_preview", "(completed)")
        if notif is None:
            return f"No response within {timeout_seconds}s. Use SubagentStatus to check current state."
        return f"A different task completed ({notif['task_id']}). Use SubagentStatus to check task {task_id}."

    # ------------------------------------------------------------------

    @tool("SubagentProgress")
    async def subagent_progress(task_id: str) -> str:
        """Read the live checkpoint of a running task to see its progress."""
        service = SubagentService.instance()
        graph_info = service.get_graph(task_id)
        if not graph_info:
            return f"No graph cached for task {task_id}."

        graph, config = graph_info
        try:
            snapshot = await graph.aget_state(config)
        except Exception as exc:
            return f"Cannot read checkpoint: {exc}"

        if not snapshot or not snapshot.values:
            return f"Task {task_id} running but no checkpoint yet."

        messages = snapshot.values.get("messages", [])
        if not messages:
            return f"Task {task_id} running, no messages in checkpoint."

        from rai.harness.subagents_v2.executor import _extract_text

        recent = messages[-6:]
        lines = [f"Progress — task {task_id[:12]} | {len(messages)} messages"]
        for msg in recent:
            msg_type = type(msg).__name__
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "?") if isinstance(tc, dict) else getattr(tc, "name", "?")
                    lines.append(f"  → {name}(...)")
            else:
                raw = _extract_text(getattr(msg, "content", ""))
                if not raw.strip():
                    continue
                trunc = raw[:100] + "…" if len(raw) > 100 else raw
                if msg_type == "ToolMessage":
                    lines.append(f"  ← {trunc}")
                else:
                    lines.append(f"  {trunc}")

        if snapshot.next:
            lines.append(f"  now: {list(snapshot.next)}")

        return "\n".join(lines)

    # ------------------------------------------------------------------

    @tool("ListSubagents")
    async def list_subagents() -> str:
        """List available subagent types with their descriptions."""
        lines = [f"Available subagents ({len(subagents)}):"]
        for s in subagents:
            desc = s.get("description", "")[:100]
            lines.append(f"  - {s['name']}: {desc}")
        return "\n".join(lines)

    # ------------------------------------------------------------------

    @tool("ListTasks")
    async def list_tasks(status_filter: str = "all") -> str:
        """List all background tasks for this run with their statuses.
        Filter by status: 'running', 'completed', 'failed', 'cancelled', 'all'."""
        service = SubagentService.instance()
        ctx = _get_run_context()
        run_id = ctx.get("run_id", "")

        all_tasks = service.list_tasks_for_run(run_id)
        if not all_tasks:
            return "No background tasks tracked for this run."

        if status_filter and status_filter != "all":
            all_tasks = [t for t in all_tasks if t.get("status") == status_filter]

        if not all_tasks:
            return f"No tasks with status '{status_filter}'."

        lines = [f"{len(all_tasks)} task(s):"]
        for t in all_tasks:
            label_str = f" ({t['label']})" if t.get("label") else ""
            lines.append(
                f"  - {t['task_id'][:12]}{label_str} | "
                f"{t.get('agent_name', '?')} | {t.get('status', '?')}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------

    return [
        subagent,
        multi_subagents,
        pipeline,
        subagent_status,
        subagent_cancel,
        subagent_update,
        subagent_response,
        subagent_progress,
        list_subagents,
        list_tasks,
    ]
