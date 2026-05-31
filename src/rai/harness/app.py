"""FastAPI application factory for the RAI HTTP server."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

if TYPE_CHECKING:
    from rai.harness import HTTPConfig
    from rai.sdk import RAIAgentBuilder

logger = logging.getLogger(__name__)


class AgentPool:
    """Lazily compiles and caches compiled Pregel graphs per agent name."""

    def __init__(self) -> None:
        self._builders: dict[str, RAIAgentBuilder] = {}
        self._graphs: dict[str, Any] = {}
        self._runnables: dict[str, Any] = {}   # RunableAgent per name (for cleanup)
        self._runtime_names: set[str] = set()
        self._checkpointer: Any = None
        self._allowed_tools: dict[str, list[str] | None] = {}  # E1 — per-agent tool whitelist

    def set_checkpointer(self, cp: Any) -> None:
        self._checkpointer = cp

    def register(self, builder: RAIAgentBuilder) -> None:
        name = builder._agent_name or "rai"
        self._builders[name] = builder
        allowed = getattr(builder, "_allowed_tools_list", None)
        if allowed is not None:
            self._allowed_tools[name] = list(allowed)

    def register_runtime(self, name: str, builder: RAIAgentBuilder, allowed_tools: "list[str] | None" = None) -> None:
        self._builders[name] = builder
        self._graphs.pop(name, None)
        self._runnables.pop(name, None)
        self._runtime_names.add(name)
        self._allowed_tools[name] = allowed_tools

    def remove(self, name: str) -> None:
        if name not in self._runtime_names:
            raise ValueError(f"Cannot remove startup-registered agent '{name}'")
        self._builders.pop(name, None)
        self._graphs.pop(name, None)
        self._runnables.pop(name, None)
        self._runtime_names.discard(name)

    def agent_names(self) -> list[str]:
        return list(self._builders.keys())

    def is_runtime(self, name: str) -> bool:
        return name in self._runtime_names

    def get_builder(self, name: str) -> RAIAgentBuilder | None:
        return self._builders.get(name)

    def get_model_hint(self, name: str) -> str:
        """Return the configured model string for an agent, empty string if unknown."""
        builder = self._builders.get(name)
        if builder is None:
            return ""
        model = getattr(builder, "_model", None)
        if isinstance(model, str):
            return model
        if model is not None:
            return (
                getattr(model, "model_name", None)
                or getattr(model, "model", None)
                or getattr(model, "model_id", None)
                or ""
            )
        return ""

    def get_allowed_tools(self, name: str) -> "list[str] | None":
        """Return the agent-level allowed_tools list (E1), or None if unrestricted."""
        return self._allowed_tools.get(name)

    async def build_model_variant(
        self, name: str, thread_id: str, model: str, cwd: str, checkpointer: Any
    ) -> Any:
        """Compile a fresh RunableAgent with a per-run model override (B2).

        Uses the pool builder's non-model settings (agent_name, system_prompt,
        auto_approve, api_key, base_url) but substitutes the caller-supplied model.
        MCP tools are NOT loaded since they need a long-lived async context —
        use the runtime endpoint for full customisation including MCP.
        """
        from rai.engine.factory import create_rai_agent
        from rai.sdk import RunableAgent
        from rai.sessions.store import build_stream_config

        builder = self._builders.get(name)
        if builder is None:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

        from rai.harness.subagents.tools import get_http_subagent_tools
        graph, _ = create_rai_agent(
            model=model,
            agent_name=builder._agent_name,
            system_prompt=builder._system_prompt,
            system_prompt_extra=builder._system_prompt_extra,
            disable_native_tools=not builder._enable_native_tools,
            disable_subagents=builder._disable_subagents,
            extra_tools=get_http_subagent_tools(),
            checkpointer=checkpointer,
            interactive=False,
            auto_approve=builder._auto_approve,
            api_key=builder._api_key,
            base_url=builder._base_url,
            suppress_local_async=True,
            disable_opplan=True,
        )
        config = build_stream_config(thread_id, builder._agent_name, cwd)
        return RunableAgent(
            graph=graph,
            backend=None,
            thread_id=thread_id,
            config=config,
            agent_name=builder._agent_name,
            cwd=cwd,
        )

    async def get_graph(self, name: str) -> Any:
        if name in self._graphs:
            return self._graphs[name]
        builder = self._builders.get(name)
        if builder is None:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

        cp = self._checkpointer
        if cp is not None:
            builder = builder.checkpointer(cp)

        try:
            # Suppress LocalAsyncAgentMiddleware so its deepagents tool names
            # (start_agent_task, check_agent_task, etc.) don't shadow the HTTP
            # subagent tools which use the same names with better capabilities.
            builder = builder.without_local_async()
            builder = builder.without_opplan()
            # Inject HTTP subagent tools — same names as deepagents tools so the
            # LLM needs no relearning. _RUN_CONTEXT supplies per-run context.
            from rai.harness.subagents.tools import get_http_subagent_tools
            builder = builder.add_tools(get_http_subagent_tools())

            # build() is async — await the coroutine to get a RunableAgent
            runnable = await builder.build()
            graph = runnable.graph
            self._runnables[name] = runnable
            self._graphs[name] = graph
            return graph
        except Exception as exc:
            logger.error("Failed to compile agent '%s': %s", name, exc)
            raise HTTPException(status_code=500, detail=f"Agent compilation failed: {exc}") from exc

    def create_per_run_runnable(self, name: str, thread_id: str, agent_name: str, cwd: str) -> Any:
        """Create a lightweight per-request RunableAgent that shares the compiled graph.

        Each run gets its own config (thread_id, cwd) while the expensive compiled
        graph/MCP sessions are reused from the pool — same pattern as the CLI TUI.
        """
        from rai.sdk import RunableAgent
        from rai.sessions.store import build_stream_config

        graph = self._graphs.get(name)
        if graph is None:
            raise HTTPException(
                status_code=400,
                detail=f"Agent '{name}' not yet compiled — POST /agents/{name}/runs first",
            )
        config = build_stream_config(thread_id, agent_name, cwd=cwd)
        return RunableAgent(
            graph=graph,
            backend=None,   # backend already compiled into graph
            thread_id=thread_id,
            config=config,
            agent_name=agent_name,
            cwd=cwd,
        )

    async def cleanup(self) -> None:
        """Close all RunableAgent instances (MCP sessions, checkpointers)."""
        for runnable in self._runnables.values():
            try:
                await runnable.aclose()
            except Exception:
                pass
        self._runnables.clear()
        self._graphs.clear()


def create_app(pool: AgentPool, config: HTTPConfig) -> FastAPI:
    """Create the FastAPI application, mount routers, and configure middleware."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from rai.sessions.store import get_checkpointer
        from rai.sessions.tasks import get_task_store
        from rai.harness.subagents.registry import set_task_store
        async with get_checkpointer() as cp, get_task_store() as task_store:
            set_task_store(task_store)
            pool.set_checkpointer(cp)
            app.state.pool = pool
            app.state.config = config
            app.state.checkpointer = cp
            app.state.task_store = task_store
            # Pre-compile all startup-registered agents in THIS lifespan task.
            # anyio cancel scopes (used by MCP stdio clients) must be entered and
            # exited in the same task — pre-compiling here satisfies that constraint
            # and avoids the "exit cancel scope in a different task" RuntimeError on shutdown.
            for name in pool.agent_names():
                if not pool.is_runtime(name):
                    try:
                        await pool.get_graph(name)
                        logger.info("Pre-compiled agent '%s'", name)
                    except Exception as exc:
                        logger.warning("Failed to pre-compile agent '%s': %s", name, exc)
            try:
                from rai.harness.subagents.executor import recover_incomplete_subagents
                await recover_incomplete_subagents(cp)
            except Exception as exc:
                logger.warning("Subagent recovery scan failed: %s", exc)
            try:
                yield
            finally:
                # Explicit cleanup in the SAME task as compilation — no cancel scope violations.
                set_task_store(None)
                await pool.cleanup()

    app = FastAPI(
        title="RAI HTTP Server",
        description="RAI agents exposed via HTTP streaming API",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS
    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # API key guard
    if config.api_key:
        @app.middleware("http")
        async def _api_key_guard(request: Request, call_next):
            if request.url.path in ("/ok", "/docs", "/openapi.json", "/redoc"):
                return await call_next(request)
            key = request.headers.get("X-API-Key", "")
            if key != config.api_key:
                return Response(
                    content='{"detail":"Unauthorized"}',
                    status_code=401,
                    media_type="application/json",
                )
            return await call_next(request)

    # Mount routers — order matters: literal paths before path params
    from rai.harness.routes.system import router as system_router
    from rai.harness.routes.agents import router as agents_router
    from rai.harness.routes.runtime import router as runtime_router
    from rai.harness.routes.runs import router as runs_router
    from rai.harness.routes.threads import router as threads_router
    from rai.harness.routes.tasks import router as tasks_router
    from rai.harness.routes.pipelines import router as pipelines_router
    from rai.harness.routes.hitl import router as hitl_router
    from rai.harness.routes.notifications import router as notif_router
    from rai.harness.subagents.routes import router as subagents_router

    app.include_router(system_router)
    app.include_router(runtime_router)  # /agents/runtime/... before /agents/{name}/...
    app.include_router(agents_router)
    app.include_router(runs_router)
    app.include_router(threads_router)
    app.include_router(tasks_router)
    app.include_router(pipelines_router)
    app.include_router(hitl_router)
    app.include_router(notif_router)
    app.include_router(subagents_router)

    return app
