from __future__ import annotations

from typing import Any, AsyncIterator

from rai.client.claude._config import ClaudeServeConfig
from rai.client.claude._sse import ClaudeSSEStream
from rai.client.claude._transport import ClaudeTransport


class ChatAPI:
    def __init__(self, transport: ClaudeTransport, cfg: ClaudeServeConfig) -> None:
        self._t = transport
        self._cfg = cfg

    def stream(
        self,
        prompt: str,
        *,
        session_id: str = "default",
        resume: str | None = None,
        agent: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        hitl: bool = True,
        plan_mode: bool = False,
    ) -> ClaudeSSEStream:
        """POST /chat → async-iterable SSE stream yielding typed event dataclasses.

        Iterate with `async for ev in client.chat.stream(...)`.
        Terminal event: ClaudeRunEndEvent (or ClaudeErrorEvent on failure).

        HITL flow:
          - ClaudeHITLRequestEvent arrives → agent frozen
          - Call client.sessions.resolve_permission(session_id, ev.tool_use_id, "allow"|"deny")
          - ClaudeHITLResponseEvent follows; agent resumes

        Plan mode:
          - ClaudePlanBlockedEvent arrives when plan_mode=True (tools were denied)
          - Switch mode: client.sessions.set_permission_mode(session_id, "default")
          - Re-send the original prompt to execute
        """
        body: dict[str, Any] = {
            "prompt":     prompt,
            "session_id": session_id,
            "hitl":       hitl,
            "plan_mode":  plan_mode,
        }
        if resume is not None:
            body["resume"] = resume
        if agent is not None:
            body["agent"] = agent
        if model is not None:
            body["model"] = model
        if effort is not None:
            body["effort"] = effort

        return ClaudeSSEStream(self._t, body)

    async def resolve_permission(
        self, session_id: str, tool_use_id: str, decision: str
    ) -> dict[str, Any]:
        """POST /agents/{session_id}/permission — resolve pending HITL.
        Convenience alias; same as client.sessions.resolve_permission()."""
        return await self._t.post(
            f"/agents/{session_id}/permission",
            {"tool_use_id": tool_use_id, "decision": decision},
        )

    async def set_permission_mode(self, session_id: str, mode: str) -> dict[str, Any]:
        """POST /agents/{session_id}/permission-mode — switch permission mode.
        Convenience alias; same as client.sessions.set_permission_mode()."""
        return await self._t.post(
            f"/agents/{session_id}/permission-mode",
            {"mode": mode},
        )
