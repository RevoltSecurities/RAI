"""ClaudeAgentBuilder — fluent builder for RunnableClaudeAgent.

Mirrors RAIAgentBuilder from rai.sdk.builder but wraps claude-agent-sdk
instead of deepagents/LangGraph.  build() is synchronous — the subprocess
spawns lazily on connect() inside the async context manager.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class ClaudeAgentBuilder:
    """Fluent builder.  All methods return self for chaining.

    Call .build() to get a RunnableClaudeAgent (use as async context manager).

    Example::

        async with (
            ClaudeAgent.builder()
                .agent_name("pentest")
                .model("claude-opus-4-8")
                .without_hitl()
                .build()
        ) as agent:
            result = await agent.run("scan example.com")
    """

    def __init__(self) -> None:
        self._agent_name: str = "rai"
        self._model: str | None = None
        self._effort: str | None = None
        self._system_prompt: str | None = None
        self._permission_mode: str = "bypassPermissions"
        self._include_partial_messages: bool = True
        self._session_id: str | None = None
        self._resume: str | None = None
        self._max_turns: int | None = None

        self._thinking: dict | None = None

        self._extra_agents: dict | None = None
        self._no_subagents: bool = False

        self._mcp_servers: dict | None = None
        self._no_mcp: bool = False

        self._extra_hooks: dict | None = None
        self._can_use_tool: Any | None = None

    # ---------------------------------------------------------------- identity

    def agent_name(self, name: str) -> "ClaudeAgentBuilder":
        self._agent_name = name
        return self

    def model(self, model: str) -> "ClaudeAgentBuilder":
        self._model = model
        return self

    def effort(self, level: str) -> "ClaudeAgentBuilder":
        self._effort = level
        return self

    def session_id(self, sid: str) -> "ClaudeAgentBuilder":
        self._session_id = sid
        return self

    def resume(self, sid: str) -> "ClaudeAgentBuilder":
        """Resume an existing session by ID."""
        self._resume = sid
        return self

    def max_turns(self, n: int) -> "ClaudeAgentBuilder":
        self._max_turns = n
        return self

    # ---------------------------------------------------------------- prompt

    def system_prompt(self, prompt: str) -> "ClaudeAgentBuilder":
        self._system_prompt = prompt
        return self

    # ----------------------------------------------------------- permissions

    def with_hitl(self) -> "ClaudeAgentBuilder":
        """Use 'default' permission mode — CLI prompts for dangerous tools."""
        self._permission_mode = "default"
        return self

    def without_hitl(self) -> "ClaudeAgentBuilder":
        """Bypass all permission checks — agent runs fully autonomously."""
        self._permission_mode = "bypassPermissions"
        return self

    def plan_mode(self) -> "ClaudeAgentBuilder":
        """Enable plan mode — Claude reasons and produces a plan but executes no tools.

        Equivalent to RAI's PlanModeMiddleware: the agent can think, write a plan,
        and respond in text, but every tool call is blocked by the SDK before
        execution. Use this to get a structured plan from the agent before
        switching to without_hitl() for autonomous execution.
        """
        self._permission_mode = "plan"
        return self

    # ------------------------------------------------------------- thinking

    def with_thinking(self) -> "ClaudeAgentBuilder":
        """Enable adaptive thinking (Opus 4.6+)."""
        self._thinking = {"type": "adaptive"}
        return self

    def without_thinking(self) -> "ClaudeAgentBuilder":
        """Disable extended thinking."""
        self._thinking = {"type": "disabled"}
        return self

    # --------------------------------------------------------------- streaming

    def with_partial_messages(self) -> "ClaudeAgentBuilder":
        """Include StreamEvent objects for token-by-token UI rendering."""
        self._include_partial_messages = True
        return self

    def without_partial_messages(self) -> "ClaudeAgentBuilder":
        self._include_partial_messages = False
        return self

    # ------------------------------------------------------------- subagents

    def add_agent(self, name: str, defn: Any) -> "ClaudeAgentBuilder":
        """Add a custom AgentDefinition (supplements AGENTS.md subagents)."""
        if self._extra_agents is None:
            self._extra_agents = {}
        self._extra_agents[name] = defn
        return self

    def without_subagents(self) -> "ClaudeAgentBuilder":
        """Skip all subagent loading (AGENTS.md from ~/.rai/agents/).

        The 'rai' agent is always excluded regardless — its system prompt
        conflicts with the Claude SDK's native prompt injection.
        """
        self._no_subagents = True
        return self

    # ------------------------------------------------------------------- MCP

    def with_mcp(self, servers: dict) -> "ClaudeAgentBuilder":
        """Override MCP servers dict (merged on top of loaded .mcp.json)."""
        if self._mcp_servers is None:
            self._mcp_servers = {}
        self._mcp_servers.update(servers)
        return self

    def without_mcp(self) -> "ClaudeAgentBuilder":
        """Disable all MCP server loading."""
        self._no_mcp = True
        return self

    # ------------------------------------------------------------------ hooks

    def can_use_tool(self, callback: Any) -> "ClaudeAgentBuilder":
        """Set async HITL callback (tool_name, input, ToolPermissionContext) → PermissionResult.

        Requires permission_mode='default' (i.e. .with_hitl()) — with bypassPermissions
        the callback is never invoked because tool calls skip the permission check entirely.
        """
        self._can_use_tool = callback
        return self

    def add_hook(self, event: str, matcher: Any) -> "ClaudeAgentBuilder":
        """Add a HookMatcher for the given event name."""
        if self._extra_hooks is None:
            self._extra_hooks = {}
        if event not in self._extra_hooks:
            self._extra_hooks[event] = []
        self._extra_hooks[event].append(matcher)
        return self

    # ------------------------------------------------------------------ build

    def build(self) -> "RunnableClaudeAgent":
        """Build a RunnableClaudeAgent — synchronous, subprocess spawns on connect()."""
        from rai.engine.claude_sdk.agent import RunnableClaudeAgent
        from rai.engine.claude_sdk.options import build_claude_options

        # Merge extra_agents with None to signal "load from AGENTS.md"
        agents: dict | None = None
        if self._no_subagents:
            agents = {}
        elif self._extra_agents:
            agents = self._extra_agents  # options.py will not call load_claude_subagents when non-None

        options = build_claude_options(
            self._agent_name,
            model=self._model,
            effort=self._effort,
            system_prompt=self._system_prompt,
            permission_mode=self._permission_mode,
            extra_hooks=self._extra_hooks,
            include_partial_messages=self._include_partial_messages,
            session_id=self._session_id,
            resume=self._resume,
            max_turns=self._max_turns,
            mcp_servers=({} if self._no_mcp else self._mcp_servers),
            agents=agents,
            can_use_tool=self._can_use_tool,
        )

        if self._thinking is not None:
            options.thinking = self._thinking

        return RunnableClaudeAgent(options=options, agent_name=self._agent_name)
