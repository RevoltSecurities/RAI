"""RAI memory middleware — injects a file index instead of file contents.

Instead of dumping memory file contents into the system prompt, this middleware
injects only the file names, sizes, and paths. The agent reads specific files
on demand using read_file when relevant context is needed.

This keeps the system prompt stable and minimal across all turns, enabling
prompt cache hits and avoiding the multi-MB injection cost of large engagement
files (target.md, methodology.md, findings.md).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired  # type: ignore[assignment]

from langchain.agents.middleware.types import AgentMiddleware, AgentState, ModelRequest, ModelResponse, PrivateStateAttr

logger = logging.getLogger(__name__)


class _RAIMemoryState(AgentState):
    """State schema for RAIMemoryMiddleware — private, never propagated to subagents."""

    _rai_memory_block: NotRequired[Annotated[str, PrivateStateAttr]]


class RAIMemoryMiddleware(AgentMiddleware):
    """Injects a memory file index into the system prompt instead of file contents.

    All memory files appear as named pointers (filename + size + path).
    The agent reads them on demand via read_file when the content is relevant.
    No file contents are ever injected — keeps the system prompt minimal and stable.

    Uses LangGraph state (via state_schema + before_agent) to load the file index
    ONCE per session rather than on every model call. The index is stored as a
    private state field so it is not propagated to subagents or the output schema.
    On session resume the field is already present and before_agent is a no-op.
    """

    state_schema = _RAIMemoryState

    def __init__(self, sources: list[str]) -> None:
        self._sources = sources
        self._cached_block: str = ""
        self._cached_mtime_sum: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle hook — load once, skip on session resume
    # ------------------------------------------------------------------

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if "_rai_memory_block" in state:
            return None  # already loaded (session resume)
        return {"_rai_memory_block": self._build_block()}

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_agent(state, runtime)

    # ------------------------------------------------------------------
    # Block construction (mtime-cached instance fallback)
    # ------------------------------------------------------------------

    def _build_block(self) -> str:
        entries: list[str] = []

        for src in self._sources:
            p = Path(src)
            if not p.exists():
                continue
            size = p.stat().st_size
            if size == 0:
                continue
            kb = size / 1024
            entries.append(f"- {p.name}  ({kb:.1f} KB)  →  {src}")

        if not entries:
            return ""

        return (
            "## Memory files (read with read_file when relevant)\n"
            + "\n".join(entries)
            + "\n\nRead the relevant files before starting any task: "
            "read target.md for engagement scope, findings.md before reporting, "
            "methodology.md before planning, user.md / feedback.md for preferences."
        )

    def _get_block(self) -> str:
        # Recompute only when files have changed (mtime sum as cheap cache key)
        try:
            mtime_sum = sum(
                Path(s).stat().st_mtime for s in self._sources if Path(s).exists()
            )
        except Exception:
            mtime_sum = 0.0

        if mtime_sum != self._cached_mtime_sum:
            self._cached_block = self._build_block()
            self._cached_mtime_sum = mtime_sum
        return self._cached_block

    # ------------------------------------------------------------------
    # Injection — prefer state-loaded block, fall back to instance cache
    # ------------------------------------------------------------------

    def _inject(self, request: ModelRequest) -> ModelRequest:
        # Prefer state (loaded once by before_agent); fall back to mtime cache
        # in case before_agent wasn't called (non-LangGraph test harnesses).
        block = request.state.get("_rai_memory_block") or self._get_block()
        if not block:
            return request
        try:
            from deepagents.middleware._utils import append_to_system_message
            new_sm = append_to_system_message(request.system_message, block)
            return request.override(system_message=new_sm)
        except Exception:
            # Fallback: deprecated string property (keeps agent working if SDK changes)
            return request.override(
                system_prompt=(request.system_prompt or "") + "\n\n" + block
            )

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        return handler(self._inject(request))

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        return await handler(self._inject(request))
