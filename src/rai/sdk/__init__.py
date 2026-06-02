"""rai.sdk — full SDK for embedding and extending RAI as a library.

Submodules
----------
  rai.sdk.agent      — RunableAgent, RAIAgent
  rai.sdk.builder    — RAIAgentBuilder (fluent builder)
  rai.sdk.serve      — ServeConfig, serve_module (LangGraph API serving)
  rai.sdk.client     — RAIClient, all SSE event types, request/response types
  rai.sdk.tui        — RaiHttpTUI, RaiHttpTUIApp (Textual terminal UI)
  rai.sdk.engine     — create_rai_agent, run_agent, build_model, ModelConfig
  rai.sdk.harness    — RAIHTTPServer, HTTPConfig (RAI HTTP streaming server)
  rai.sdk.middleware — all middleware classes
  rai.sdk.tools      — all agent tools organized by domain
  rai.sdk.config     — settings, AgentConfig, load_agent_config
  rai.sdk.mcp        — load_mcp_tools, load_subagents_mcp_tools_map

Quick start::

    import asyncio
    from rai.sdk import RAIAgent

    async def main():
        async with RAIAgent.builder()
                .model("anthropic:claude-sonnet-4-6")
                .agent_name("pentest")
                .target("example.com")
                .build() as agent:
            result = await agent.run("Find OWASP Top 10 issues on the login endpoint")
            print(agent.thread_id)

    asyncio.run(main())
"""

from __future__ import annotations

# ---- High-level SDK --------------------------------------------------------
from rai.sdk.agent import RAIAgent, RunableAgent
from rai.sdk.builder import RAIAgentBuilder

# ---- Serve API -------------------------------------------------------------
from rai.sdk.serve import ServeConfig, ServeError, serve_module

# ---- HTTP harness ----------------------------------------------------------
from rai.sdk.harness import HTTPConfig, RAIHTTPServer

# ---- TUI -------------------------------------------------------------------
from rai.sdk.tui import RaiHttpTUI, RaiHttpTUIApp

# ---- HTTP client -----------------------------------------------------------
from rai.sdk.client import (
    RAIClient,
    ClientConfig,
    SSEEvent,
    PlanTimeoutError,
    # Event types — most commonly used
    RunStartEvent,
    RunEndEvent,
    TokenEvent,
    ThinkingEvent,
    ToolStartEvent,
    ToolEndEvent,
    InterruptEvent,
    InterruptResolvedEvent,
    PlanModeEnteredEvent,
    PlanReadyEvent,
    PlanApprovedEvent,
    PlanRejectedEvent,
    PlanCompletedEvent,
    StepStartEvent,
    StepCompleteEvent,
    StepBlockedEvent,
    SubagentStartedEvent,
    SubagentCompletedEvent,
    SubagentErrorEvent,
    TaskCreatedEvent,
    TaskStatusEvent,
    TaskCompletedEvent,
    ErrorEvent,
    NotificationEvent,
)

# ---- Engine ----------------------------------------------------------------
from rai.sdk.engine import (
    create_rai_agent,
    get_system_prompt,
    DEFAULT_AGENT_NAME,
    DEFAULT_MODEL,
    build_model,
    ModelConfig,
    list_providers,
    run_agent,
)

# ---- Middleware -------------------------------------------------------------
from rai.sdk.middleware import (
    AuditLogMiddleware,
    ExecuteInterceptorMiddleware,
    FindingsEnrichmentMiddleware,
    HooksMiddleware,
    ModelCallLoggerMiddleware,
    ModelOverrideMiddleware,
    OPPLANMiddleware,
    RateLimitMiddleware,
    EmptyContentSanitizerMiddleware,
    RAIPromptCachingMiddleware,
    MemoryMiddleware,
    SkillsMiddleware,
    LocalAsyncAgentMiddleware,
    RTKToolMiddleware,
    MessageCompressionMiddleware,
    ToolResultCompressionMiddleware,
    LoopDetectionMiddleware,
)

# ---- Tools (top-level getters) ---------------------------------------------
from rai.sdk.tools import (
    get_security_tools,
    get_builtin_tools,
    get_memory_tools,
    get_web_tools,
    get_cloud_tools,
    get_ad_tools,
    get_reversing_tools,
    get_android_tools,
    get_container_tools,
    get_opplan_tools,
    get_reference_tools,
    FindingsAddTool,
    FindingsExportTool,
    FindingsListTool,
    init_findings_store,
)

# ---- Config ----------------------------------------------------------------
from rai.sdk.config import settings, AgentConfig, load_agent_config

# ---- MCP -------------------------------------------------------------------
from rai.sdk.mcp import load_mcp_tools, load_subagents_mcp_tools_map

# ---- Session helpers -------------------------------------------------------
from rai.sessions.store import build_stream_config, generate_thread_id, get_checkpointer

# ---- Subagent / backend types (deepagents primitives) ----------------------
from deepagents import AsyncSubAgent, CompiledSubAgent, create_deep_agent
from deepagents.middleware.subagents import SubAgent
from deepagents.backends import CompositeBackend, LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend

__all__ = [
    # High-level SDK
    "RAIAgent",
    "RAIAgentBuilder",
    "RunableAgent",
    # Serve API
    "ServeConfig",
    "ServeError",
    "serve_module",
    # Factory / engine
    "create_rai_agent",
    "create_deep_agent",
    "get_system_prompt",
    "DEFAULT_AGENT_NAME",
    "DEFAULT_MODEL",
    # Runner
    "run_agent",
    # Session
    "generate_thread_id",
    "get_checkpointer",
    "build_stream_config",
    # Subagent types
    "SubAgent",
    "AsyncSubAgent",
    "CompiledSubAgent",
    # Backends
    "CompositeBackend",
    "LocalShellBackend",
    "FilesystemBackend",
    # Middleware
    "MemoryMiddleware",
    "SkillsMiddleware",
    "AuditLogMiddleware",
    "ExecuteInterceptorMiddleware",
    "FindingsEnrichmentMiddleware",
    "HooksMiddleware",
    "ModelCallLoggerMiddleware",
    "ModelOverrideMiddleware",
    "OPPLANMiddleware",
    "RateLimitMiddleware",
    "EmptyContentSanitizerMiddleware",
    "LocalAsyncAgentMiddleware",
    "RAIPromptCachingMiddleware",
    "RTKToolMiddleware",
    "MessageCompressionMiddleware",
    "ToolResultCompressionMiddleware",
    "LoopDetectionMiddleware",
    # Model SDK
    "build_model",
    "ModelConfig",
    "list_providers",
    # Tools
    "get_security_tools",
    "get_builtin_tools",
    "get_memory_tools",
    "get_web_tools",
    "get_cloud_tools",
    "get_ad_tools",
    "get_reversing_tools",
    "get_android_tools",
    "get_container_tools",
    "get_opplan_tools",
    "get_reference_tools",
    "FindingsAddTool",
    "FindingsExportTool",
    "FindingsListTool",
    "init_findings_store",
    # MCP
    "load_mcp_tools",
    "load_subagents_mcp_tools_map",
    # Config
    "settings",
    "AgentConfig",
    "load_agent_config",
    # HTTP Server
    "RAIHTTPServer",
    "HTTPConfig",
    # TUI
    "RaiHttpTUI",
    "RaiHttpTUIApp",
    # HTTP Client
    "RAIClient",
    "ClientConfig",
    "SSEEvent",
    "PlanTimeoutError",
    # Common event types
    "RunStartEvent",
    "RunEndEvent",
    "TokenEvent",
    "ThinkingEvent",
    "ToolStartEvent",
    "ToolEndEvent",
    "InterruptEvent",
    "InterruptResolvedEvent",
    "PlanModeEnteredEvent",
    "PlanReadyEvent",
    "PlanApprovedEvent",
    "PlanRejectedEvent",
    "PlanCompletedEvent",
    "StepStartEvent",
    "StepCompleteEvent",
    "StepBlockedEvent",
    "SubagentStartedEvent",
    "SubagentCompletedEvent",
    "SubagentErrorEvent",
    "TaskCreatedEvent",
    "TaskStatusEvent",
    "TaskCompletedEvent",
    "ErrorEvent",
    "NotificationEvent",
]
