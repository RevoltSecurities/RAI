"""SSEEventPump — translates RAI SSE events into Textual Messages."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.message import Message


# ---------------------------------------------------------------------------
# Textual Message subclasses (one per event category)
# ---------------------------------------------------------------------------

class AppendToken(Message):
    def __init__(self, run_id: str, content: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.content = content


class AppendThinking(Message):
    def __init__(self, run_id: str, content: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.content = content


class ToolStarted(Message):
    def __init__(self, run_id: str, tool_name: str, tool_input: dict) -> None:
        super().__init__()
        self.run_id = run_id
        self.tool_name = tool_name
        self.tool_input = tool_input or {}


class ToolFinished(Message):
    def __init__(self, run_id: str, tool_name: str, tool_output: Any, tool_use_id: str = "") -> None:
        super().__init__()
        self.run_id = run_id
        self.tool_name = tool_name
        self.tool_output = tool_output
        self.tool_use_id = tool_use_id


class PermissionDenied(Message):
    def __init__(self, run_id: str, tool_name: str, reason: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.tool_name = tool_name
        self.reason = reason


class HITLRequired(Message):
    def __init__(
        self,
        run_id: str,
        thread_id: str,
        interrupt_id: str,
        action_requests: list,
    ) -> None:
        super().__init__()
        self.run_id = run_id
        self.thread_id = thread_id
        self.interrupt_id = interrupt_id
        self.action_requests = action_requests or []


class HITLAutoApproved(Message):
    def __init__(self, run_id: str, tool_names: list) -> None:
        super().__init__()
        self.run_id = run_id
        self.tool_names = tool_names or []


class HITLResolved(Message):
    def __init__(self, run_id: str, decision: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.decision = decision


class AskUserRequired(Message):
    def __init__(self, run_id: str, thread_id: str, questions: list) -> None:
        super().__init__()
        self.run_id = run_id
        self.thread_id = thread_id
        self.questions = questions or []


class PlanModeEntered(Message):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id


class PlanReady(Message):
    def __init__(self, run_id: str, plan: str, approve_url: str, reject_url: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.plan = plan
        self.approve_url = approve_url
        self.reject_url = reject_url


class PlanDone(Message):
    def __init__(self, run_id: str, approved: bool, feedback: str = "") -> None:
        super().__init__()
        self.run_id = run_id
        self.approved = approved
        self.feedback = feedback


class SubagentAdded(Message):
    def __init__(self, run_id: str, task_id: str, agent_name: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.task_id = task_id
        self.agent_name = agent_name


class SubagentToken(Message):
    def __init__(self, task_id: str, content: str) -> None:
        super().__init__()
        self.task_id = task_id
        self.content = content


class SubagentThinking(Message):
    def __init__(self, task_id: str, content: str) -> None:
        super().__init__()
        self.task_id = task_id
        self.content = content


class SubagentToolStarted(Message):
    def __init__(self, task_id: str, tool_name: str, tool_input: dict) -> None:
        super().__init__()
        self.task_id = task_id
        self.tool_name = tool_name
        self.tool_input = tool_input or {}


class SubagentToolFinished(Message):
    def __init__(self, task_id: str, tool_name: str, tool_output: Any, tool_use_id: str = "") -> None:
        super().__init__()
        self.task_id = task_id
        self.tool_name = tool_name
        self.tool_output = tool_output
        self.tool_use_id = tool_use_id


class SubagentDone(Message):
    def __init__(self, task_id: str, status: str, output_preview: str = "") -> None:
        super().__init__()
        self.task_id = task_id
        self.status = status
        self.output_preview = output_preview


class SubagentHITL(Message):
    def __init__(self, task_id: str, interrupt_id: str, action_requests: list) -> None:
        super().__init__()
        self.task_id = task_id
        self.interrupt_id = interrupt_id
        self.action_requests = action_requests or []


class SubagentHITLAutoApproved(Message):
    def __init__(self, task_id: str, tool_names: list) -> None:
        super().__init__()
        self.task_id = task_id
        self.tool_names = tool_names or []


class RunCompleted(Message):
    def __init__(
        self,
        run_id: str,
        status: str,
        output: str = "",
        duration_ms: int | None = None,
    ) -> None:
        super().__init__()
        self.run_id = run_id
        self.status = status
        self.output = output
        self.duration_ms = duration_ms


class RunError(Message):
    def __init__(self, run_id: str, message: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.message = message


class RateLimited(Message):
    def __init__(self, run_id: str, resets_at: str = "") -> None:
        super().__init__()
        self.run_id = run_id
        self.resets_at = resets_at


class NotificationArrived(Message):
    def __init__(
        self,
        task_id: str,
        agent_name: str,
        status: str,
        output_preview: str = "",
    ) -> None:
        super().__init__()
        self.task_id = task_id
        self.agent_name = agent_name
        self.status = status
        self.output_preview = output_preview


class PipelineUpdate(Message):
    def __init__(self, pipeline_id: str, event_type: str, data: dict) -> None:
        super().__init__()
        self.pipeline_id = pipeline_id
        self.event_type = event_type
        self.data = data or {}


class StepComplete(Message):
    def __init__(self, run_id: str, step_number: int, description: str = "") -> None:
        super().__init__()
        self.run_id = run_id
        self.step_number = step_number
        self.description = description


class StepStarted(Message):
    def __init__(self, run_id: str, step_number: int, description: str = "") -> None:
        super().__init__()
        self.run_id = run_id
        self.step_number = step_number
        self.description = description


class StepBlocked(Message):
    def __init__(self, run_id: str, step_number: int, description: str = "", reason: str = "") -> None:
        super().__init__()
        self.run_id = run_id
        self.step_number = step_number
        self.description = description
        self.reason = reason


class PlanCompleted(Message):
    def __init__(self, run_id: str, total_steps: int = 0) -> None:
        super().__init__()
        self.run_id = run_id
        self.total_steps = total_steps


class SessionApproved(Message):
    def __init__(self, run_id: str, tool_name: str) -> None:
        super().__init__()
        self.run_id = run_id
        self.tool_name = tool_name


# ---------------------------------------------------------------------------
# SSEEventPump
# ---------------------------------------------------------------------------

class SSEEventPump:
    """Streams a single run's SSE events and posts Textual Messages to the app."""

    async def run(
        self,
        app: Any,
        client: Any,
        agent: str,
        run_id: str,
    ) -> None:
        from rai.client import (
            AskUserRequestEvent,
            TokenEvent,
            ThinkingEvent,
            ToolStartEvent,
            ToolEndEvent,
            PermissionDeniedEvent,
            InterruptEvent,
            InterruptAutoApprovedEvent,
            InterruptResolvedEvent,
            SessionApprovedEvent,
            PlanCompletedEvent,
            PlanModeEnteredEvent,
            PlanReadyEvent,
            PlanApprovedEvent,
            PlanRejectedEvent,
            StepCompleteEvent,
            StepStartEvent,
            StepBlockedEvent,
            SubagentStartedEvent,
            SubagentTokenEvent,
            SubagentThinkingEvent,
            SubagentToolStartEvent,
            SubagentToolEndEvent,
            SubagentInterruptEvent,
            SubagentInterruptResolvedEvent,
            SubagentCompletedEvent,
            SubagentResumedEvent,
            SubagentTurnCompleteEvent,
            SubagentErrorEvent,
            RunEndEvent,
            ErrorEvent,
            RateLimitEvent,
            NotificationEvent,
            PipelineCreatedEvent,
            PipelineBatchStartedEvent,
            PipelineBatchEvent,
            PipelineEndEvent,
            PipelineErrorEvent,
            WatcherInvokeEvent,
            TaskCreatedEvent,
            TaskStatusEvent,
            TaskCompletedEvent,
        )

        try:
            async for ev in client.runs.stream(agent, run_id):
                match ev:
                    case TokenEvent():
                        app.post_message(AppendToken(run_id, ev.content or ""))

                    case ThinkingEvent():
                        app.post_message(AppendThinking(run_id, ev.content or ""))

                    case ToolStartEvent():
                        app.post_message(ToolStarted(run_id, ev.tool_name or "", ev.tool_input or {}))

                    case ToolEndEvent():
                        app.post_message(
                            ToolFinished(run_id, ev.tool_name or "", ev.tool_output, getattr(ev, "tool_use_id", ""))
                        )

                    case PermissionDeniedEvent():
                        app.post_message(PermissionDenied(run_id, ev.tool_name or "", getattr(ev, "reason", "")))

                    case InterruptEvent():
                        app.post_message(
                            HITLRequired(
                                run_id,
                                getattr(ev, "thread_id", ""),
                                getattr(ev, "interrupt_id", ""),
                                getattr(ev, "action_requests", []) or [],
                            )
                        )

                    case AskUserRequestEvent():
                        app.post_message(
                            AskUserRequired(
                                run_id,
                                getattr(ev, "thread_id", ""),
                                getattr(ev, "questions", []) or [],
                            )
                        )

                    case InterruptAutoApprovedEvent():
                        app.post_message(HITLAutoApproved(run_id, getattr(ev, "tool_names", []) or []))

                    case InterruptResolvedEvent():
                        app.post_message(HITLResolved(run_id, getattr(ev, "decision", "")))

                    case SessionApprovedEvent():
                        app.post_message(SessionApproved(run_id, getattr(ev, "tool_name", "")))

                    case PlanModeEnteredEvent():
                        app.post_message(PlanModeEntered(run_id))

                    case PlanReadyEvent():
                        app.post_message(
                            PlanReady(
                                run_id,
                                getattr(ev, "plan", "") or "",
                                getattr(ev, "approve_url", "") or "",
                                getattr(ev, "reject_url", "") or "",
                            )
                        )

                    case PlanApprovedEvent():
                        app.post_message(PlanDone(run_id, approved=True))

                    case PlanRejectedEvent():
                        app.post_message(
                            PlanDone(run_id, approved=False, feedback=getattr(ev, "feedback", "") or "")
                        )

                    case StepCompleteEvent():
                        app.post_message(
                            StepComplete(
                                run_id,
                                getattr(ev, "step_number", 0) or 0,
                                getattr(ev, "description", "") or "",
                            )
                        )

                    case StepStartEvent():
                        app.post_message(
                            StepStarted(
                                run_id,
                                getattr(ev, "step_number", 0) or 0,
                                getattr(ev, "description", "") or "",
                            )
                        )

                    case StepBlockedEvent():
                        app.post_message(
                            StepBlocked(
                                run_id,
                                getattr(ev, "step_number", 0) or 0,
                                getattr(ev, "description", "") or "",
                                getattr(ev, "reason", "") or "",
                            )
                        )

                    case PlanCompletedEvent():
                        app.post_message(
                            PlanCompleted(run_id, getattr(ev, "total_steps", 0) or 0)
                        )

                    case SubagentStartedEvent():
                        app.post_message(
                            SubagentAdded(
                                run_id,
                                getattr(ev, "task_id", ""),
                                getattr(ev, "agent_name", ""),
                            )
                        )

                    case SubagentTokenEvent():
                        app.post_message(SubagentToken(getattr(ev, "task_id", ""), ev.content or ""))

                    case SubagentThinkingEvent():
                        app.post_message(SubagentThinking(getattr(ev, "task_id", ""), ev.content or ""))

                    case SubagentToolStartEvent():
                        app.post_message(
                            SubagentToolStarted(
                                getattr(ev, "task_id", ""),
                                getattr(ev, "tool_name", ""),
                                getattr(ev, "tool_input", {}) or {},
                            )
                        )

                    case SubagentToolEndEvent():
                        app.post_message(
                            SubagentToolFinished(
                                getattr(ev, "task_id", ""),
                                getattr(ev, "tool_name", ""),
                                getattr(ev, "tool_output", None),
                                getattr(ev, "tool_use_id", ""),
                            )
                        )

                    case SubagentInterruptEvent():
                        app.post_message(
                            SubagentHITL(
                                getattr(ev, "task_id", ""),
                                getattr(ev, "interrupt_id", ""),
                                getattr(ev, "action_requests", []) or [],
                            )
                        )

                    case SubagentInterruptResolvedEvent():
                        pass  # informational

                    case SubagentCompletedEvent():
                        app.post_message(
                            SubagentDone(
                                getattr(ev, "task_id", ""),
                                getattr(ev, "status", "completed"),
                                getattr(ev, "output_preview", "") or "",
                            )
                        )

                    case SubagentErrorEvent():
                        app.post_message(
                            SubagentDone(
                                getattr(ev, "task_id", ""),
                                "error",
                                getattr(ev, "message", "") or "",
                            )
                        )

                    case SubagentResumedEvent() | SubagentTurnCompleteEvent():
                        pass  # no UI action needed

                    case NotificationEvent():
                        app.post_message(
                            NotificationArrived(
                                getattr(ev, "task_id", ""),
                                getattr(ev, "agent_name", ""),
                                getattr(ev, "status", ""),
                                getattr(ev, "output_preview", "") or "",
                            )
                        )

                    case PipelineCreatedEvent() | PipelineBatchStartedEvent() | PipelineBatchEvent() | PipelineEndEvent() | PipelineErrorEvent():
                        app.post_message(
                            PipelineUpdate(
                                getattr(ev, "pipeline_id", ""),
                                type(ev).__name__,
                                ev.data if hasattr(ev, "data") else {},
                            )
                        )

                    case WatcherInvokeEvent() | TaskCreatedEvent() | TaskStatusEvent() | TaskCompletedEvent():
                        pass  # informational

                    case RunEndEvent():
                        app.post_message(
                            RunCompleted(
                                run_id,
                                getattr(ev, "status", "completed") or "completed",
                                getattr(ev, "output", "") or "",
                                getattr(ev, "duration_ms", None),
                            )
                        )
                        return

                    case ErrorEvent():
                        app.post_message(RunError(run_id, getattr(ev, "message", "unknown error") or ""))
                        return

                    case RateLimitEvent():
                        app.post_message(RateLimited(run_id, getattr(ev, "resets_at", "") or ""))

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            app.post_message(RunError(run_id, str(exc)))

