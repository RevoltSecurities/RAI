from __future__ import annotations

from typing import Any

from rai.client.claude._transport import ClaudeTransport


class SessionsAPI:
    def __init__(self, transport: ClaudeTransport) -> None:
        self._t = transport

    async def list(self) -> dict[str, Any]:
        """GET /agents — list all live sessions with active_stream and mode."""
        return await self._t.get("/agents")

    async def delete(self, session_id: str) -> dict[str, Any]:
        """DELETE /agents/{session_id} — disconnect and destroy session subprocess."""
        return await self._t.delete(f"/agents/{session_id}")

    async def interrupt(self, session_id: str) -> dict[str, Any]:
        """POST /agents/{session_id}/interrupt — interrupt the current run.
        The subprocess stays alive; stream ends with run_end(is_error=True)."""
        return await self._t.post(f"/agents/{session_id}/interrupt")

    async def cancel_task(self, session_id: str, task_id: str) -> dict[str, Any]:
        """POST /agents/{session_id}/cancel-task — cancel a specific subagent task.
        A subagent_done with status='stopped' follows in the stream."""
        return await self._t.post(f"/agents/{session_id}/cancel-task", {"task_id": task_id})

    async def set_model(self, session_id: str, model: str | None) -> dict[str, Any]:
        """POST /agents/{session_id}/model — switch model mid-session.
        Takes effect on the next prompt."""
        return await self._t.post(f"/agents/{session_id}/model", {"model": model})

    async def set_permission_mode(self, session_id: str, mode: str) -> dict[str, Any]:
        """POST /agents/{session_id}/permission-mode — switch permission mode.
        Modes: default | bypassPermissions | acceptEdits | plan | dontAsk | auto
        After switching plan → default, re-send the original prompt to execute."""
        return await self._t.post(f"/agents/{session_id}/permission-mode", {"mode": mode})

    async def resolve_permission(
        self, session_id: str, tool_use_id: str, decision: str
    ) -> dict[str, Any]:
        """POST /agents/{session_id}/permission — resolve a pending HITL request.
        decision: "allow" | "deny". Resolving this unblocks the frozen agent subprocess."""
        return await self._t.post(
            f"/agents/{session_id}/permission",
            {"tool_use_id": tool_use_id, "decision": decision},
        )

    async def resolve_plan_feedback(
        self, session_id: str, tool_use_id: str, feedback: str
    ) -> dict[str, Any]:
        """POST /agents/{session_id}/permission — reject ExitPlanMode with edit feedback.
        Claude stays in plan mode and revises before calling ExitPlanMode again."""
        return await self._t.post(
            f"/agents/{session_id}/permission",
            {"tool_use_id": tool_use_id, "decision": "plan_feedback", "feedback": feedback},
        )

    async def resolve_ask_user(
        self, session_id: str, tool_use_id: str, answers: dict[str, str]
    ) -> dict[str, Any]:
        """POST /agents/{session_id}/permission — submit AskUserQuestion answers.
        Injects answers into the tool input via PermissionResultAllow(updated_input)."""
        return await self._t.post(
            f"/agents/{session_id}/permission",
            {"tool_use_id": tool_use_id, "decision": "ask_user_answer", "answers": answers},
        )

    async def get_mcp(self, session_id: str) -> dict[str, Any]:
        """GET /agents/{session_id}/mcp — live MCP server connection status."""
        return await self._t.get(f"/agents/{session_id}/mcp")

    async def get_context(self, session_id: str) -> dict[str, Any]:
        """GET /agents/{session_id}/context — context window token breakdown."""
        return await self._t.get(f"/agents/{session_id}/context")

    async def get_server_info(self, session_id: str) -> dict[str, Any]:
        """GET /agents/{session_id}/server-info — server initialization data."""
        return await self._t.get(f"/agents/{session_id}/server-info")
