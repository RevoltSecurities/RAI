"""rai.sdk.builder — RAIAgentBuilder fluent builder."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from deepagents.backends import CompositeBackend
    from rai.sdk.agent import RunableAgent
    from rai.sdk.serve import ServeConfig
    from rai.harness import HTTPConfig


class RAIAgentBuilder:
    """Fluent builder for RAI agents.

    All methods return ``self`` for chaining.  Call ``await builder.build()``
    or use it as ``async with builder.build() as agent:`` to get a
    :class:`RunableAgent`.

    Default state mirrors the CLI: all RAI tools enabled, memory on, skills on,
    HITL on, subagents loaded from AGENTS.md.  Toggle individual features off
    with the ``without_*`` helpers or pass ``disable_native_tools=True`` etc.
    """

    def __init__(self) -> None:
        # ---- identity ----
        self._model: str | BaseChatModel = "anthropic:claude-sonnet-4-6"
        self._agent_name: str = "rai"
        self._target: str = ""
        self._api_key: str = ""
        self._base_url: str = ""

        # ---- prompt ----
        self._system_prompt: str | None = None
        self._system_prompt_extra: str | None = None

        # ---- feature flags ----
        self._enable_native_tools: bool = True
        self._enable_memory: bool = True
        self._enable_skills: bool = True
        self._enable_shell: bool = True
        self._enable_audit_log: bool = True
        self._auto_approve: bool = False
        self._disable_subagents: bool = False
        self._suppress_local_async: bool = False
        self._disable_opplan: bool = False
        self._enable_rtk: bool = True

        # ---- custom items ----
        self._extra_tools: list[Any] = []
        self._custom_subagents: list[Any] = []
        self._custom_middleware: list[Any] = []
        self._custom_backend: Any = None

        # ---- config ----
        self._rate_limit_profile: str = ""
        self._hooks_config_path: str | None = None
        self._agent_config: Any = None
        self._checkpointer: Any = None
        self._cwd: Path | None = None

        # ---- MCP ----
        self._mcp_paths: list[str] = []
        self._mcp_inline: list[dict] = []
        self._no_mcp: bool = False
        self._auto_mcp: bool = True

        # ---- session ----
        self._thread_id: str | None = None

        # ---- http harness ----
        self._allowed_tools_list: list[str] | None = None

    # ---------------------------------------------------------------- model

    def model(self, model: str | BaseChatModel) -> RAIAgentBuilder:
        """Set the LLM. Accepts LangChain provider strings, LiteLLM format, or a BaseChatModel."""
        self._model = model
        return self

    # ---------------------------------------------------------------- identity

    def agent_name(self, name: str) -> RAIAgentBuilder:
        """Set agent name — determines memory/skills/subagent directory under ~/.rai/agents/."""
        self._agent_name = name
        return self

    def target(self, target: str) -> RAIAgentBuilder:
        """Set engagement target for pentest-style agents."""
        self._target = target
        return self

    def api_key(self, key: str) -> RAIAgentBuilder:
        """Override API key (highest priority over config.toml and env)."""
        self._api_key = key
        return self

    def base_url(self, url: str) -> RAIAgentBuilder:
        """Override base URL (for custom endpoints / proxies)."""
        self._base_url = url
        return self

    # ---------------------------------------------------------------- prompt

    def system_prompt(self, prompt: str) -> RAIAgentBuilder:
        """Fully replace the RAI system prompt."""
        self._system_prompt = prompt
        return self

    def system_prompt_extra(self, extra: str) -> RAIAgentBuilder:
        """Append extra instructions after the resolved system prompt."""
        self._system_prompt_extra = extra
        return self

    # ----------------------------------------------------------- feature flags

    def with_native_tools(self) -> RAIAgentBuilder:
        """Enable all RAI built-in tools (default)."""
        self._enable_native_tools = True
        return self

    def without_native_tools(self) -> RAIAgentBuilder:
        """Skip all RAI built-in tools. Agent receives only extra_tools."""
        self._enable_native_tools = False
        return self

    def with_memory(self) -> RAIAgentBuilder:
        """Enable per-agent memory loaded into the system prompt (default)."""
        self._enable_memory = True
        return self

    def without_memory(self) -> RAIAgentBuilder:
        """Disable memory middleware entirely."""
        self._enable_memory = False
        return self

    def with_skills(self) -> RAIAgentBuilder:
        """Enable skills loaded from ~/.rai/skills/ and agent skills dir (default)."""
        self._enable_skills = True
        return self

    def without_skills(self) -> RAIAgentBuilder:
        """Disable skills middleware."""
        self._enable_skills = False
        return self

    def with_shell(self) -> RAIAgentBuilder:
        """Use LocalShellBackend for full shell access (default)."""
        self._enable_shell = True
        return self

    def without_shell(self) -> RAIAgentBuilder:
        """Use FilesystemBackend only — no shell execution."""
        self._enable_shell = False
        return self

    def with_audit_log(self) -> RAIAgentBuilder:
        """Enable audit logging of all tool calls to ~/.rai/audit.log (default)."""
        self._enable_audit_log = True
        return self

    def without_audit_log(self) -> RAIAgentBuilder:
        """Disable audit logging."""
        self._enable_audit_log = False
        return self

    def with_hitl(self) -> RAIAgentBuilder:
        """Enable human-in-the-loop interrupts for destructive tools (default)."""
        self._auto_approve = False
        return self

    def without_hitl(self) -> RAIAgentBuilder:
        """Disable all HITL interrupts — agent runs autonomously."""
        self._auto_approve = True
        return self

    def with_subagents(self) -> RAIAgentBuilder:
        """Load subagents from AGENTS.md and TOML files (default)."""
        self._disable_subagents = False
        return self

    def without_subagents(self) -> RAIAgentBuilder:
        """Skip AGENTS.md / TOML subagent loading. custom_subagents still apply."""
        self._disable_subagents = True
        return self

    def without_local_async(self) -> RAIAgentBuilder:
        """Skip LocalAsyncAgentMiddleware even when subagents are loaded."""
        self._suppress_local_async = True
        return self

    def without_opplan(self) -> RAIAgentBuilder:
        """Skip opplan tools + middleware in HTTP harness mode."""
        self._disable_opplan = True
        return self

    def with_rtk(self) -> RAIAgentBuilder:
        """Enable RTK command rewriting via ``rtk rewrite`` (default when rtk is installed)."""
        self._enable_rtk = True
        return self

    def without_rtk(self) -> RAIAgentBuilder:
        """Disable RTK command rewriting."""
        self._enable_rtk = False
        return self

    # ----------------------------------------------------------------- tools

    def add_tool(self, tool: Any) -> RAIAgentBuilder:
        """Add a single tool (BaseTool, @tool callable, or JSON schema dict)."""
        self._extra_tools.append(tool)
        return self

    def add_tools(self, tools: list[Any]) -> RAIAgentBuilder:
        """Add multiple tools."""
        self._extra_tools.extend(tools)
        return self

    # --------------------------------------------------------------- subagents

    def add_subagent(self, subagent: Any) -> RAIAgentBuilder:
        """Add a SubAgent, AsyncSubAgent, or CompiledSubAgent spec."""
        self._custom_subagents.append(subagent)
        return self

    def add_subagents(self, subagents: list[Any]) -> RAIAgentBuilder:
        """Add multiple subagent specs."""
        self._custom_subagents.extend(subagents)
        return self

    # ------------------------------------------------------------- middleware

    def add_middleware(self, middleware: Any) -> RAIAgentBuilder:
        """Append a custom AgentMiddleware to the stack (runs after RAI built-ins)."""
        self._custom_middleware.append(middleware)
        return self

    # --------------------------------------------------------------- backend

    def backend(self, backend: Any) -> RAIAgentBuilder:
        """Override the CompositeBackend. Skips RAI's default LocalShellBackend setup."""
        self._custom_backend = backend
        return self

    # ---------------------------------------------------------------- config

    def rate_limit(self, profile: str) -> RAIAgentBuilder:
        """Rate limit profile: 'aggressive', 'normal', or 'stealth'."""
        self._rate_limit_profile = profile
        return self

    def hooks(self, config_path: str) -> RAIAgentBuilder:
        """Path to an extra hooks.json file — merged on top of ~/.rai/hooks.json."""
        self._hooks_config_path = config_path
        return self

    def agent_config(self, config: Any) -> RAIAgentBuilder:
        """Pass an explicit AgentConfig (overrides config.toml loading)."""
        self._agent_config = config
        return self

    def checkpointer(self, checkpointer: Any) -> RAIAgentBuilder:
        """Supply a pre-built LangGraph checkpointer (caller owns lifetime)."""
        self._checkpointer = checkpointer
        return self

    def cwd(self, path: str | Path) -> RAIAgentBuilder:
        """Override working directory (default: Path.cwd())."""
        self._cwd = Path(path)
        return self

    # ------------------------------------------------------------------- MCP

    def with_mcp(self, *paths: str) -> RAIAgentBuilder:
        """Add one or more explicit MCP config JSON paths."""
        self._mcp_paths.extend(paths)
        return self

    def with_mcp_servers(self, config: dict) -> "RAIAgentBuilder":
        """Add MCP servers from an inline dict — no file needed.

        Accepts either the standard Claude format::

            {"mcpServers": {"name": {"command": "...", "args": [...]}}}

        or a bare servers dict (automatically wrapped)::

            {"name": {"command": "...", "args": [...]}}
        """
        if "mcpServers" in config:
            self._mcp_inline.append(config)
        else:
            self._mcp_inline.append({"mcpServers": config})
        return self

    def without_mcp(self) -> RAIAgentBuilder:
        """Disable all MCP loading."""
        self._no_mcp = True
        return self

    # --------------------------------------------------------------- session

    def thread_id(self, tid: str) -> RAIAgentBuilder:
        """Set an explicit thread ID (resume an existing session)."""
        self._thread_id = tid
        return self

    # --------------------------------------------------------------- http harness

    def allowed_tools(self, tools: list[str]) -> RAIAgentBuilder:
        """Restrict which tools this agent may call without HITL approval."""
        self._allowed_tools_list = list(tools)
        return self

    # ------------------------------------------------------------------ serve

    def serve(
        self,
        config: "ServeConfig | None" = None,
        **kwargs: Any,
    ) -> NoReturn:
        """Serve this agent via the LangGraph API (blocks until Ctrl-C)."""
        from rai.sdk.serve import ServeConfig as _SC, _serve_from_builder
        if config is None:
            config = _SC(**kwargs) if kwargs else _SC()
        _serve_from_builder(self, config)

    def serve_http(
        self,
        config: "HTTPConfig | None" = None,
        **kwargs: Any,
    ) -> NoReturn:
        """Serve this agent via the RAI HTTP streaming API (blocks until Ctrl-C)."""
        from rai.harness import RAIHTTPServer
        from rai.harness import HTTPConfig as _HC
        if config is None:
            config = _HC(**kwargs) if kwargs else _HC()
        RAIHTTPServer(config).register(self).run()

    # ----------------------------------------------------------------- build

    async def build(self) -> "RunableAgent":
        """Build and return a RunableAgent.

        Handles MCP loading, checkpointer creation, and agent compilation.
        The returned RunableAgent is an async context manager.
        """
        from rai.engine.factory import create_rai_agent
        from rai.mcp.loader import resolve_and_load_mcp_tools
        from rai.sessions.store import build_stream_config, generate_thread_id, get_checkpointer
        from rai.sdk.agent import RunableAgent

        effective_cwd = self._cwd or Path.cwd()
        thread_id = self._thread_id or generate_thread_id()

        # ---- MCP loading ----
        mcp_tools: list[Any] = []
        mcp_session: Any = None
        if not self._no_mcp:
            if self._mcp_paths:
                for path in self._mcp_paths:
                    tools, session, _ = await resolve_and_load_mcp_tools(
                        explicit_config_path=path,
                        agent_name=self._agent_name,
                        cwd=effective_cwd,
                    )
                    mcp_tools.extend(tools)
                    if session and mcp_session is None:
                        mcp_session = session
            else:
                tools, session, _ = await resolve_and_load_mcp_tools(
                    agent_name=self._agent_name,
                    cwd=effective_cwd,
                )
                mcp_tools.extend(tools)
                mcp_session = session

            # ---- inline MCP (with_mcp_servers) ----
            if self._mcp_inline:
                from rai.mcp.loader import _load_tools_from_config
                from rai.mcp.config import merge_mcp_configs
                merged = merge_mcp_configs(self._mcp_inline)
                if merged.get("mcpServers"):
                    tools, session, _ = await _load_tools_from_config(merged)
                    mcp_tools.extend(tools)
                    if session and mcp_session is None:
                        mcp_session = session

        all_extra_tools = list(self._extra_tools) + mcp_tools

        # ---- Checkpointer ----
        checkpointer_ctx: Any = None
        checkpointer = self._checkpointer
        if checkpointer is None:
            checkpointer_ctx = get_checkpointer()
            checkpointer = await checkpointer_ctx.__aenter__()

        # ---- Build agent ----
        agent, backend = create_rai_agent(
            model=self._model,
            agent_name=self._agent_name,
            system_prompt=self._system_prompt,
            system_prompt_extra=self._system_prompt_extra,
            extra_tools=all_extra_tools,
            enable_memory=self._enable_memory,
            enable_skills=self._enable_skills,
            enable_shell=self._enable_shell,
            enable_audit_log=self._enable_audit_log,
            disable_native_tools=not self._enable_native_tools,
            disable_subagents=self._disable_subagents and not self._custom_subagents,
            custom_subagents=self._custom_subagents or None,
            extra_middleware=self._custom_middleware or None,
            custom_backend=self._custom_backend,
            auto_approve=self._auto_approve,
            api_key=self._api_key,
            base_url=self._base_url,
            target=self._target or "",
            rate_limit_profile=self._rate_limit_profile,
            hooks_config_path=self._hooks_config_path,
            agent_config=self._agent_config,
            checkpointer=checkpointer,
            cwd=effective_cwd,
            suppress_local_async=self._suppress_local_async,
            disable_opplan=self._disable_opplan,
            disable_rtk=not self._enable_rtk,
        )

        config = build_stream_config(thread_id, self._agent_name, str(effective_cwd))

        return RunableAgent(
            graph=agent,
            backend=backend,
            thread_id=thread_id,
            config=config,
            agent_name=self._agent_name,
            cwd=str(effective_cwd),
            mcp_session=mcp_session,
            _checkpointer_ctx=checkpointer_ctx,
        )
