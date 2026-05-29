"""rai.sdk.agent — RunableAgent and RAIAgent runtime classes."""

from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# RunableAgent
# ---------------------------------------------------------------------------


class RunableAgent:
    """A compiled RAI agent with managed checkpointer and MCP session lifetimes.

    Intended to be used as an async context manager (via RAIAgentBuilder.build()).
    Can also be used stand-alone when the caller owns the checkpointer.

    Attributes:
        thread_id: LangGraph thread ID — save this for session resume.
        graph:     The compiled CompiledStateGraph (deepagents Pregel graph).
        backend:   The CompositeBackend wired into the agent.
    """

    def __init__(
        self,
        graph: Any,
        backend: Any,
        thread_id: str,
        config: dict,
        agent_name: str = "rai",
        cwd: str = ".",
        mcp_session: Any = None,
        _checkpointer_ctx: Any = None,
    ) -> None:
        self.graph = graph
        self.backend = backend
        self.thread_id = thread_id
        self._config = config
        self._agent_name = agent_name
        self._cwd = cwd
        self._mcp_session = mcp_session
        self._checkpointer_ctx = _checkpointer_ctx

    async def run(self, prompt: str, *, timeout: float = 3600.0) -> Any:
        """Run one user turn to completion, including all spawned background subagents."""
        from rai.engine.runner import run_agent
        return await run_agent(
            prompt, self.graph,
            thread_id=self.thread_id,
            config=self._config,
            agent_name=self._agent_name,
            cwd=self._cwd,
            timeout=timeout,
        )

    async def stream(self, prompt: str) -> Any:
        """Stream events for one user turn (raw LangGraph astream_events output)."""
        from langchain_core.messages import HumanMessage
        async for event in self.graph.astream_events(
            {"messages": [HumanMessage(content=prompt)]},
            self._config,
            version="v2",
        ):
            yield event

    async def get_state(self) -> Any:
        """Return the current LangGraph checkpoint state for this thread."""
        return await self.graph.aget_state(self._config)

    async def aclose(self) -> None:
        """Release MCP sessions and checkpointer. Called automatically by __aexit__."""
        if self._mcp_session is not None:
            try:
                await self._mcp_session.cleanup()
            except Exception:
                pass
        if self._checkpointer_ctx is not None:
            try:
                await self._checkpointer_ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def __aenter__(self) -> RunableAgent:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"RunableAgent(agent_name={self._agent_name!r}, thread_id={self.thread_id!r})"


# ---------------------------------------------------------------------------
# RAIAgent — top-level entry point
# ---------------------------------------------------------------------------


class RAIAgent:
    """Top-level SDK entry point for building RAI agents.

    Use :meth:`builder` to get a fluent :class:`RAIAgentBuilder`::

        async with RAIAgent.builder().model("...").build() as agent:
            await agent.run("...")

    Use :meth:`from_graph` to wrap an already-compiled graph::

        compiled = create_deep_agent(...)
        agent = RAIAgent.from_graph(compiled, thread_id="abc")
        await agent.run("...")
    """

    @classmethod
    def builder(cls) -> "RAIAgentBuilder":
        """Return a new fluent RAIAgentBuilder with RAI defaults enabled."""
        from rai.sdk.builder import RAIAgentBuilder
        return RAIAgentBuilder()

    @classmethod
    def from_graph(
        cls,
        graph: Any,
        *,
        thread_id: str | None = None,
        agent_name: str = "rai",
        cwd: str | Path = ".",
        backend: Any = None,
    ) -> RunableAgent:
        """Wrap a pre-compiled LangGraph graph as a RunableAgent."""
        from rai.sessions.store import build_stream_config, generate_thread_id
        tid = thread_id or generate_thread_id()
        config = build_stream_config(tid, agent_name, str(cwd))
        return RunableAgent(
            graph=graph,
            backend=backend,
            thread_id=tid,
            config=config,
            agent_name=agent_name,
            cwd=str(cwd),
        )


# avoid circular — builder imports RunableAgent lazily
from typing import TYPE_CHECKING  # noqa: E402
if TYPE_CHECKING:
    from rai.sdk.builder import RAIAgentBuilder
