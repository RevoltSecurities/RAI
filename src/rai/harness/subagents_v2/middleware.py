"""AsyncRaiSubagentsMiddleware — notification drain only, no system prompt per-turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ContextT, ModelRequest, ModelResponse, ResponseT
from langchain_core.messages import HumanMessage

from rai.harness.subagents_v2.models import AsyncSubAgentState, CompletionNotification
from rai.harness.subagents_v2.service import SubagentService
from rai.harness.subagents_v2.tools import build_subagent_tools


def _format_notification(notif: CompletionNotification) -> str:
    """Format a completion notification as a <task-notification> block."""
    agent = notif.get("agent_name", "")
    task_id = notif.get("task_id", "")
    status = notif.get("status", "")
    preview = notif.get("output_preview", "")[:400]
    label = notif.get("label", "")

    label_str = f" | Label: {label}" if label else ""
    return (
        f"<task-notification>\n"
        f"[Background agent completed]\n"
        f"Agent: {agent} | Task: {task_id}{label_str} | Status: {status}\n"
        f"Output preview: {preview}\n"
        f"Full output: /tmp/rai_{task_id}_output.md\n"
        f"</task-notification>"
    )


class AsyncRaiSubagentsMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Middleware for RAI async subagents.

    - state_schema: extends graph state with `async_tasks` reducer
    - tools: 10 subagent management tools (Subagent, MultiSubagents, etc.)
    - awrap_model_call: ONLY drains completion notifications — does NOT
      modify system prompt (preserves Anthropic prompt cache)

    System prompt with subagent descriptions is injected ONCE at build time
    in create_rai_agent() via _build_subagent_system_section(), not per-turn.
    """

    state_schema = AsyncSubAgentState

    def __init__(
        self,
        *,
        subagents: list[dict[str, Any]],
        default_model: Any = None,
        checkpointer: Any = None,
        parent_api_key: str = "",
        parent_base_url: str = "",
    ) -> None:
        super().__init__()
        self.tools = build_subagent_tools(subagents)
        self._default_model = default_model
        self._parent_api_key = parent_api_key
        self._parent_base_url = parent_base_url

        # Initialize service if not already done
        SubagentService.initialize(checkpointer=checkpointer)

    def _drain_notifications(self) -> list[CompletionNotification]:
        """Non-blocking drain of all pending notifications for the current run.

        Returns empty list if no notifications pending — zero overhead.
        """
        service = SubagentService.instance()
        notifications: list[CompletionNotification] = []

        # Drain all queues (we don't have run_id here so drain all)
        for run_id, q in list(service._completion_queues.items()):
            while not q.empty():
                try:
                    notifications.append(q.get_nowait())
                except Exception:
                    break

        return notifications

    def _apply_notifications(
        self,
        request: ModelRequest[ContextT],
        notifications: list[CompletionNotification],
    ) -> ModelRequest[ContextT]:
        """Inject notifications without creating consecutive HumanMessages."""
        notif_text = "\n\n".join(_format_notification(n) for n in notifications)
        notif_text += (
            "\n\nSummarise what the background agent(s) completed and their "
            "key results — only if relevant to the current task."
        )

        messages = list(request.messages)
        if messages and isinstance(messages[-1], HumanMessage):
            # Fold into existing system message to avoid consecutive HumanMessages
            new_sys = (request.system_message or "") + "\n\n" + notif_text
            return request.override(system_message=new_sys)
        else:
            messages.append(HumanMessage(content=notif_text))
            return request.override(messages=messages)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        notifications = self._drain_notifications()
        if notifications:
            request = self._apply_notifications(request, notifications)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]],
    ) -> ModelResponse[ResponseT]:
        notifications = self._drain_notifications()
        if notifications:
            request = self._apply_notifications(request, notifications)
        return await handler(request)


def build_subagent_system_section(subagents: list[dict[str, Any]]) -> str:
    """Build the static system prompt section for subagent awareness.

    Injected ONCE at create_rai_agent() build time → benefits from
    Anthropic prompt caching across all turns.
    """
    agents_desc = "\n".join(
        f"- {s['name']}: {s.get('description', '')[:80]}" for s in subagents
    )
    return (
        "## Background Subagents\n\n"
        "Use `Subagent` tool to launch background tasks. Available agents:\n"
        f"{agents_desc}\n\n"
        "Rules:\n"
        "- Never poll in a loop. You'll be notified via <task-notification> on completion.\n"
        "- Use `ListSubagents` for full descriptions if needed.\n"
        "- Use `ListTasks` to see all running/completed tasks.\n"
        "- Use `SubagentStatus` to read a task's full output after completion.\n"
        "- Use `SubagentProgress` to peek at a running task's live state.\n"
        "- NEVER mention tool names to the user — use natural language."
    )
