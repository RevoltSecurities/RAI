"""09_sdk_cookbook.py — Comprehensive RAI SDK cookbook.

Covers every major extension point:

  A. Custom main agent — own tools, no native RAI tools
  B. Native tools + extras — keep all 80+ RAI tools, add your own
  C. Custom subagents — plain spec, with own tools, exclude native subagents
  D. Custom backend — read-only filesystem, no shell
  E. Custom memory — inject project files, arbitrary .md paths, target scope
  F. Extra middleware — custom harness layers (logging, rate limit, audit)
  G. Custom system prompt — replace or extend the RAI prompt
  H. Headless / CI mode — no HITL, no interactive prompts
  I. Session resume — persist and reload from a thread ID
  J. Low-level factory — call create_rai_agent() directly for full control
  K. HTTP server + TUI — RAIHTTPServer + RaiHttpTUI + BrandingConfig from rai.sdk
  L. Multiple custom tools — @tool, StructuredTool, BaseTool subclass, add_tools()
  M. MCP tools — file path, inline dict, auto-discovery, disable, combined

Usage:
    python examples/09_sdk_cookbook.py --example A
    python examples/09_sdk_cookbook.py --example B --task "scan 192.168.1.0/24"
    python examples/09_sdk_cookbook.py --example K
    python examples/09_sdk_cookbook.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool, tool
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ResponseT,
)
from langchain_core.messages import ToolMessage

from rai.sdk import (
    # High-level
    RAIAgent,
    RAIAgentBuilder,
    # Backends
    CompositeBackend,
    FilesystemBackend,
    # Middleware
    MemoryMiddleware,
    # Subagent types
    SubAgent,
    # Tools
    get_security_tools,
    get_builtin_tools,
    # Config
    AgentConfig,
    DEFAULT_MODEL,
    # HTTP server + client
    RAIHTTPServer,
    HTTPConfig,
)

# Modular sub-package imports (these are always available independently)
from rai.sdk.tui import RaiHttpTUI, BrandingConfig


# ===========================================================================
# A. Custom main agent — own tools only, zero RAI native tools
# ===========================================================================

@tool
def dns_resolve(hostname: str) -> str:
    """Resolve a hostname to its IP addresses."""
    import socket
    try:
        return json.dumps(socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP))
    except socket.gaierror as e:
        return f"DNS error: {e}"


@tool
def tls_cert_info(hostname: str, port: int = 443) -> str:
    """Retrieve TLS certificate details for a host."""
    import ssl, socket
    ctx = ssl.create_default_context()
    try:
        with ctx.wrap_socket(socket.socket(), server_hostname=hostname) as s:
            s.settimeout(5)
            s.connect((hostname, port))
            cert = s.getpeercert()
            return json.dumps(cert, default=str)
    except Exception as e:
        return f"TLS error: {e}"


async def example_a(task: str) -> None:
    """A: Minimal agent with only custom tools — no RAI defaults."""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("dns-checker")
        # Drop every RAI built-in (80+ security/bash/memory tools)
        .without_native_tools()
        # No subagents — single-agent operation
        .without_subagents()
        # No memory files injected into system prompt
        .without_memory()
        .without_skills()
        .without_audit_log()
        # No HITL — fully autonomous
        .without_hitl()
        # Provide only your own tools
        .add_tools([dns_resolve, tls_cert_info])
        # Full custom system prompt
        .system_prompt(
            "You are a DNS and TLS analyst. "
            "Use dns_resolve and tls_cert_info to investigate hostnames. "
            "Report findings in structured markdown."
        )
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)
        print(f"thread_id: {agent.thread_id}")


# ===========================================================================
# B. Keep all native RAI tools + add your own on top
# ===========================================================================

def _check_waf(url: str) -> str:
    """Detect common WAF signatures in HTTP response headers."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            headers = dict(resp.headers)
            wafs = {
                "Cloudflare": "cf-ray",
                "Akamai": "x-check-cacheable",
                "AWS WAF": "x-amzn-requestid",
                "Sucuri": "x-sucuri-id",
            }
            detected = [name for name, header in wafs.items() if header in {h.lower() for h in headers}]
            return f"WAFs detected: {detected or ['none']}\nHeaders: {list(headers.keys())}"
    except Exception as e:
        return f"Request failed: {e}"


waf_detect = StructuredTool.from_function(
    func=_check_waf,
    name="waf_detect",
    description="Detect WAF presence by inspecting HTTP response headers.",
)


async def example_b(task: str) -> None:
    """B: Full RAI toolset + a custom WAF detection tool added on top."""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("web-recon")
        .target("example.com")           # scopes target memory
        # Keep all 80+ RAI native tools
        # .with_native_tools() is the default — no need to call it
        # Append your own tools on top of the native set
        .add_tool(waf_detect)
        # Context hint appended AFTER the default RAI system prompt
        .system_prompt_extra(
            "## Engagement Context\n"
            "Always start by running waf_detect on the target before any other scans. "
            "Use the result to choose appropriate bypass techniques."
        )
        .rate_limit("stealth")           # 'aggressive' | 'normal' | 'stealth'
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


# ===========================================================================
# C. Custom subagents — three patterns
# ===========================================================================

async def example_c_plain(task: str) -> None:
    """C1: Plain SubAgent spec — agent is built by the SDK at runtime."""
    recon_subagent: SubAgent = {
        "name": "recon",
        "description": "Performs passive reconnaissance: DNS, WHOIS, certificate transparency",
        "system_prompt": (
            "You are a passive recon specialist. "
            "Use dns_resolve and tls_cert_info only — no active probing."
        ),
        # Per-subagent tool list — overrides the default (parent tools are NOT passed)
        "tools": [dns_resolve, tls_cert_info],
        # Per-subagent model — falls back to parent's model when omitted
        "model": DEFAULT_MODEL,
    }

    report_subagent: SubAgent = {
        "name": "reporter",
        "description": "Synthesises findings into a structured markdown report",
        "system_prompt": (
            "You are a technical report writer. "
            "Produce clear, structured markdown security reports. "
            "Include: Executive Summary, Findings, Risk Ratings, Remediation."
        ),
        "tools": [],    # no tools — this subagent is reasoning-only
    }

    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("coordinator")
        # Disable AGENTS.md loading — use ONLY the subagents defined here
        .without_subagents()
        .add_subagents([recon_subagent, report_subagent])
        # Main agent keeps its own tools; subagents get only what is in their spec
        .without_native_tools()
        .add_tools([dns_resolve, tls_cert_info])
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


async def example_c_merge(task: str) -> None:
    """C2: Merge a custom subagent with AGENTS.md subagents.

    AGENTS.md subagents (researcher, coder, …) are loaded AND your custom
    subagent is appended. On name collision your version wins.
    """
    my_threat_modeler: SubAgent = {
        "name": "threat-model",           # overrides the AGENTS.md threat-model if it exists
        "description": "Builds STRIDE-based threat models for architectures",
        "system_prompt": (
            "You are a threat modelling expert. "
            "Apply STRIDE to enumerate threats. "
            "Output threat tables with ID, category, description, and mitigation."
        ),
    }

    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        # with_subagents() is default — AGENTS.md subagents are loaded
        .add_subagent(my_threat_modeler)  # appended; wins on name collision
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


async def example_c_tools_map(task: str) -> None:
    """C3: Give a NAMED EXISTING subagent extra tools via subagent_tools_map.

    Use this when the subagent is defined in AGENTS.md but you want to
    inject additional tools (e.g. MCP tools) at runtime without editing the file.
    """
    from rai.engine.factory import create_rai_agent
    from rai.sessions.store import build_stream_config, generate_thread_id, get_checkpointer

    # Tools that will be injected into the "researcher" subagent only
    extra_researcher_tools = [dns_resolve, tls_cert_info]

    async with get_checkpointer() as checkpointer:
        agent, backend = create_rai_agent(
            model=DEFAULT_MODEL,
            agent_name="rai",
            disable_native_tools=False,
            auto_approve=True,
            # Map: subagent name → extra tools for that subagent only
            subagent_tools_map={
                "researcher": extra_researcher_tools,
                # You can inject into multiple subagents:
                # "coder": [my_code_analysis_tool],
            },
            checkpointer=checkpointer,
        )
        thread_id = generate_thread_id()
        config = build_stream_config(thread_id, "rai", str(Path.cwd()))
        from rai.engine.runner import run_agent
        result = await run_agent(task, agent, thread_id=thread_id, config=config)
        print(result)


# ===========================================================================
# D. Custom backend — read-only filesystem, no shell
# ===========================================================================

async def example_d(task: str, source_dir: str = ".") -> None:
    """D: Read-only FilesystemBackend — no shell access.

    Useful for SAST-style agents that only read source files,
    or for sandboxed environments where shell execution is forbidden.
    """
    root = Path(source_dir).resolve()

    # FilesystemBackend: read/write files, no shell execution
    fs_backend = FilesystemBackend(root_dir=root)

    # CompositeBackend routes large outputs to a separate temp dir
    import tempfile
    large_results = FilesystemBackend(
        root_dir=tempfile.mkdtemp(prefix="rai_large_"),
        virtual_mode=True,
    )
    custom_backend = CompositeBackend(
        default=fs_backend,
        routes={"/large_tool_results/": large_results},
    )

    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("sast")
        # Supply our own backend — factory skips LocalShellBackend setup
        .backend(custom_backend)
        # Disable shell tools (bash, execute) since we have no shell backend
        .without_shell()
        # Keep memory + skills for context, disable HITL for CI
        .without_hitl()
        .system_prompt(
            "You are a static security analyzer. "
            "Read source files and identify vulnerabilities. "
            "Do NOT execute any commands."
        )
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


# ===========================================================================
# E. Custom memory — project files, arbitrary paths, target scope
# ===========================================================================

async def example_e_project_md(task: str) -> None:
    """E1: Load project-specific context files into agent memory.

    RAI automatically picks up these files if they exist in the CWD:
      - AGENTS.md
      - .rai/AGENTS.md
      - CLAUDE.md
      - .claude/CLAUDE.md

    These are loaded AFTER agent memory so they can override instructions.
    Just set cwd= to the project root and place an AGENTS.md there.

    Example AGENTS.md content:
        # Project Context
        Target: internal API at api.corp.local
        Scope: /api/v1/*, /admin/* — EXCLUDE /api/v1/health
        Auth: Bearer token in X-Auth header
        Do not test endpoints outside scope.
    """
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("pentest")
        # Point to the project root — AGENTS.md / CLAUDE.md there is auto-loaded
        .cwd("/path/to/project")          # change to your project root
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


async def example_e_arbitrary_paths(task: str) -> None:
    """E2: Inject arbitrary .md files as agent memory via MemoryMiddleware.

    Use when your context files don't live in the standard project layout.
    Files are loaded in order — last file wins on duplicated keys.
    """
    from rai.engine.factory import create_rai_agent
    from rai.sessions.store import build_stream_config, generate_thread_id, get_checkpointer

    # Create any extra memory files you want — absolute paths
    extra_memory_sources = [
        "/path/to/projects/acme-corp/scope.md",      # engagement scope
        "/path/to/projects/acme-corp/architecture.md", # system diagram + notes
        "/home/user/.rai/shared/owasp_checklist.md",   # shared methodology
    ]

    # Build a MemoryMiddleware that loads these on top of agent memory
    custom_memory = MemoryMiddleware(
        backend=FilesystemBackend(),
        sources=extra_memory_sources,
    )

    async with get_checkpointer() as checkpointer:
        agent, backend = create_rai_agent(
            model=DEFAULT_MODEL,
            agent_name="rai",
            # Disable the built-in MemoryMiddleware so it doesn't double-load
            enable_memory=False,
            # Inject our memory middleware via extra_middleware
            extra_middleware=[custom_memory],
            auto_approve=True,
            checkpointer=checkpointer,
        )
        thread_id = generate_thread_id()
        config = build_stream_config(thread_id, "rai", str(Path.cwd()))
        from rai.engine.runner import run_agent
        result = await run_agent(task, agent, thread_id=thread_id, config=config)
        print(result)


async def example_e_target_memory(task: str) -> None:
    """E3: Target-scoped memory.

    Setting .target() loads ~/.rai/targets/<target>/ memory files on top of
    agent memory. Use this for engagements where you accumulate recon notes,
    findings, and methodology per target.

    Memory hierarchy (loaded in order, later files override earlier):
      1. ~/.rai/user/*.md          — global user profile
      2. ~/.rai/agents/rai/memory/ — agent memories (feedback, engagement, …)
      3. ~/.rai/targets/<t>/memory/ — target-specific recon, findings, notes
      4. CWD/AGENTS.md             — project-level override

    The agent's memory_write tool also understands scope="target" so it
    writes to the correct directory automatically.
    """
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("pentest")
        .target("api.acme-corp.com")     # activates target-scoped memory
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


async def example_e_inline_context(task: str, project_brief: str) -> None:
    """E4: Inject context inline via system_prompt_extra — no files needed.

    Useful when your context is dynamic (fetched from a ticket system, DB,
    API) and you don't want to write it to disk first.
    """
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("api-auditor")
        # Appended AFTER the full RAI system prompt — keeps RAI persona intact
        .system_prompt_extra(
            f"## Active Engagement\n\n{project_brief}\n\n"
            "Apply all findings to the scope above. "
            "Rate risks according to CVSS v3."
        )
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


# ===========================================================================
# F. Extra middleware — custom harness layers
# ===========================================================================


class ToolCallLoggerMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Example middleware: print every tool call + result to stdout."""

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        tool_name = getattr(request, "tool_name", "?")
        tool_args = getattr(request, "tool_call", {}).get("args", {})
        print(f"[TOOL] → {tool_name}({json.dumps(tool_args, default=str)[:200]})")
        result = await handler(request)
        content = ""
        if isinstance(result, ToolMessage):
            c = result.content
            content = c[:300] if isinstance(c, str) else str(c)[:300]
        print(f"[TOOL] ← {tool_name}: {content}")
        return result


class ScopeEnforcerMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Example middleware: block http_request calls outside allowed scope."""

    def __init__(self, allowed_hosts: list[str]) -> None:
        self._allowed = set(allowed_hosts)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        tool_name = getattr(request, "tool_name", "")
        if tool_name == "http_request":
            url: str = getattr(request, "tool_call", {}).get("args", {}).get("url", "")
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            if host and host not in self._allowed:
                return ToolMessage(
                    content=f"[BLOCKED] {url} is outside the allowed scope: {self._allowed}",
                    tool_call_id=getattr(request, "tool_call", {}).get("id", ""),
                )
        return await handler(request)


async def example_f(task: str) -> None:
    """F: Stack custom middleware on top of RAI's built-in middleware."""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("scoped-pentest")
        .add_middleware(ToolCallLoggerMiddleware())
        .add_middleware(ScopeEnforcerMiddleware(allowed_hosts=["api.acme-corp.com", "192.168.1.1"]))
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


# ===========================================================================
# G. Custom system prompt — replace or extend
# ===========================================================================

CUSTOM_PROMPT = """\
# API Security Specialist

You are an expert in API security assessment. Your methodology:

1. **Enumerate** — map all endpoints, parameters, and auth schemes
2. **Fuzz** — test each input for injection, logic errors, and type confusion
3. **Escalate** — test BOLA/BFLA using multiple auth levels
4. **Report** — document every finding with CVSS score and reproduction steps

Always use findings_add to record confirmed vulnerabilities.
"""


async def example_g_replace(task: str) -> None:
    """G1: Fully replace the RAI system prompt with your own."""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("api-auditor")
        # Replaces the entire RAI prompt — RAI persona is gone
        .system_prompt(CUSTOM_PROMPT)
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


async def example_g_extend(task: str) -> None:
    """G2: Keep RAI's full persona and add an engagement brief on top."""
    engagement = """\
## Engagement Scope — Acme Corp Bug Bounty

- In scope: api.acme-corp.com paths /v2/*, /admin/*
- Out of scope: /healthz, /metrics, anything on legacy.acme-corp.com
- Auth tokens: admin=Bearer eyJ..., user=Bearer eyJ...
- Max impact: critical/high only (P1/P2 bounty)
"""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        # system_prompt_extra appends AFTER the full default RAI prompt
        .system_prompt_extra(engagement)
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


# ===========================================================================
# H. Headless / CI mode — no HITL, explicit auto-approve
# ===========================================================================

async def example_h(task: str) -> None:
    """H: Fully autonomous agent for CI pipelines (no interactive prompts)."""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("ci-sast")
        .without_hitl()          # disables all LangGraph interrupt_on gates
        .without_audit_log()     # skip writing ~/.rai/audit.log in CI
        .rate_limit("aggressive") # no per-tool delays — run as fast as possible
        .build()
    ) as agent:
        result = await agent.run(task, timeout=600.0)  # 10-minute hard cap
        print(result)


# ===========================================================================
# I. Session resume — save and reload thread_id
# ===========================================================================

async def example_i_start(task: str) -> str:
    """I1: Start a session and save the thread_id for resumption."""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("pentest")
        .without_hitl()
        .build()
    ) as agent:
        await agent.run(task)
        print(f"Session started. Resume with: --resume {agent.thread_id}")
        return agent.thread_id


async def example_i_resume(thread_id: str, follow_up: str) -> None:
    """I2: Resume an existing session from a saved thread_id.

    The agent loads its full checkpoint — all previous messages, tool calls,
    memory writes, and subagent results are available.
    """
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("pentest")
        .thread_id(thread_id)    # resume from this thread
        .without_hitl()
        .build()
    ) as agent:
        result = await agent.run(follow_up)
        print(result)


# ===========================================================================
# J. Low-level factory — full manual control
# ===========================================================================

async def example_j_factory(task: str) -> None:
    """J: Call create_rai_agent() directly for maximum control.

    This is the escape hatch when the builder doesn't expose what you need.
    Everything the builder does internally is accessible here.
    """
    from rai.engine.factory import create_rai_agent
    from rai.sessions.store import build_stream_config, generate_thread_id, get_checkpointer
    from rai.engine.runner import run_agent

    # Explicit AgentConfig — bypasses TOML file loading entirely
    cfg = AgentConfig(
        model="litellm:openai/bedrock-claude-sonnet-4.6-(US)",
        api_key="sk-...",
        base_url="https://llmproxy.example.com",
        temperature=0.3,
        max_tokens=16000,
    )

    # Custom subagents — only these two, no AGENTS.md
    custom_subs = [
        {
            "name": "web-scanner",
            "description": "Scans web targets for OWASP Top 10",
            "system_prompt": "You are a web scanner. Run nuclei and report findings.",
            "tools": get_security_tools(),    # give this subagent the security tool suite
        },
        {
            "name": "reporter",
            "description": "Writes pentesting reports",
            "system_prompt": "You write professional pentest reports in markdown.",
            "tools": [],
        },
    ]

    # Extra middleware appended after all RAI built-ins
    extra_mw = [
        ToolCallLoggerMiddleware(),
        ScopeEnforcerMiddleware(allowed_hosts=["target.local"]),
    ]

    async with get_checkpointer() as checkpointer:
        agent, backend = create_rai_agent(
            model=DEFAULT_MODEL,
            agent_name="full-pentest",
            # Credentials
            agent_config=cfg,
            # Prompt
            system_prompt_extra="Scope: target.local only. All findings must be P1 or P2.",
            # Tools
            disable_native_tools=False,     # keep all 80+ RAI tools
            extra_tools=[waf_detect],       # append custom tools
            # Subagents
            disable_subagents=True,         # don't load AGENTS.md
            custom_subagents=custom_subs,   # use these subagents only
            subagent_tools_map={
                # Inject extra tools into the web-scanner subagent at build time
                "web-scanner": [waf_detect],
            },
            # Memory
            enable_memory=True,
            target="target.local",          # activate target-scoped memory
            # Feature flags
            auto_approve=True,              # no HITL
            enable_audit_log=False,
            rate_limit_profile="stealth",
            # Middleware
            extra_middleware=extra_mw,
            # Infrastructure
            checkpointer=checkpointer,
            cwd=Path.cwd(),
        )

        thread_id = generate_thread_id()
        config = build_stream_config(thread_id, "full-pentest", str(Path.cwd()))
        result = await run_agent(
            task, agent,
            thread_id=thread_id,
            config=config,
            agent_name="full-pentest",
        )
        print(result)
        print(f"thread_id: {thread_id}")


# ===========================================================================
# K. HTTP server + TUI — RAIHTTPServer + RaiHttpTUI + BrandingConfig
# ===========================================================================

def example_k_tui(_task: str = "") -> None:  # noqa: ARG001
    """K: Build a custom agent, serve it over HTTP, and open the TUI window.

    Uses:
      rai.sdk.harness  → RAIHTTPServer, HTTPConfig
      rai.sdk.tui      → RaiHttpTUI, BrandingConfig
      rai.sdk.builder  → RAIAgentBuilder  (via RAIAgent.builder())

    BrandingConfig white-labels the TUI: custom icon, title, tips, and info rows.
    The HTTP server runs in a background thread so it doesn't block the TUI.
    On TUI exit the uvicorn server is signalled to stop.
    """
    import os, tempfile, threading, time
    import httpx, uvicorn

    host, port = "127.0.0.1", 8765
    agent_name = "cookbook-agent"

    builder = (
        RAIAgentBuilder()
        .agent_name(agent_name)
        .model(DEFAULT_MODEL)
        .add_tools(get_security_tools() + get_builtin_tools())
        .without_hitl()
    )

    config = HTTPConfig(host=host, port=port)
    server = RAIHTTPServer(config)
    server.register(builder)

    # --- Start uvicorn in a background thread ---
    log_file = os.path.join(tempfile.gettempdir(), "rai-cookbook-k.log")
    log_cfg = {
        "version": 1, "disable_existing_loggers": False,
        "formatters": {"d": {"fmt": "%(asctime)s %(levelname)s — %(message)s"}},
        "handlers": {"f": {
            "class": "logging.FileHandler", "filename": log_file, "mode": "a", "formatter": "d"
        }},
        "loggers": {
            "uvicorn":        {"handlers": ["f"], "level": "INFO",    "propagate": False},
            "uvicorn.access": {"handlers": ["f"], "level": "INFO",    "propagate": False},
            "uvicorn.error":  {"handlers": ["f"], "level": "WARNING", "propagate": False},
        },
    }
    app = server._build_app()
    uv_cfg = uvicorn.Config(app, host=host, port=port, log_config=log_cfg, loop="asyncio")
    uv = uvicorn.Server(uv_cfg)

    def _run_uv() -> None:
        import asyncio as _aio
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)
        loop.run_until_complete(uv.serve())

    threading.Thread(target=_run_uv, daemon=True).start()

    # Poll readiness
    base = f"http://{host}:{port}"
    print(f"Starting {base} …", end="", flush=True)
    for _ in range(40):
        try:
            if httpx.get(f"{base}/ok", timeout=0.5, verify=False).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.25)
        print(".", end="", flush=True)
    print(" ready")

    # Open TUI — blocks until user closes it.
    # BrandingConfig customizes the welcome banner: icon, title, tips, info rows.
    # Use BrandingConfig(show_banner=False) to suppress the banner entirely.
    branding = BrandingConfig(
        icon="[bold green]🍳  Cookbook Agent[/bold green]",
        title="SDK Cookbook",
        subtitle="Example K",
        tips="[dim]/new[/dim] thread  ·  [dim]ctrl+t[/dim] theme  ·  [dim]ctrl+b[/dim] bg runs",
        info_rows=[("example", "K — HTTP server + TUI")],
    )
    RaiHttpTUI(base_url=base, agent=agent_name, branding=branding).run()

    # Graceful shutdown
    uv.should_exit = True


# ===========================================================================
# L. Multiple custom tools — all four ways to add tools
# ===========================================================================

# ---- Pattern 1: @tool decorator (simplest) ----
@tool
def whois_lookup(domain: str) -> str:
    """Return WHOIS registrar and expiry info for a domain."""
    import subprocess
    try:
        out = subprocess.check_output(["whois", domain], timeout=10, text=True)
        # Extract the three most useful lines
        lines = [l for l in out.splitlines() if any(
            k in l.lower() for k in ("registrar:", "expiry date:", "creation date:")
        )]
        return "\n".join(lines) or out[:500]
    except Exception as e:
        return f"whois error: {e}"


@tool
def asn_lookup(ip: str) -> str:
    """Return the ASN and organisation name for an IP address."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"https://ipinfo.io/{ip}/json", timeout=5) as r:
            data = json.loads(r.read())
            return f"ASN: {data.get('org', 'unknown')}  City: {data.get('city')}  Country: {data.get('country')}"
    except Exception as e:
        return f"ASN lookup error: {e}"


# ---- Pattern 2: StructuredTool.from_function (when you need explicit control) ----
def _banner_grab(host: str, port: int = 80) -> str:
    """Read the first 512 bytes from an open TCP port."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=5) as s:
            s.sendall(b"HEAD / HTTP/1.0\r\n\r\n")
            return s.recv(512).decode(errors="replace")
    except Exception as e:
        return f"Banner grab failed: {e}"


banner_grab = StructuredTool.from_function(
    func=_banner_grab,
    name="banner_grab",
    description="Grab the service banner from a TCP port (default port 80).",
)


# ---- Pattern 3: a BaseTool subclass (full control — custom _run + _arun) ----
from langchain_core.tools import BaseTool as _BaseTool
from pydantic import BaseModel as _BaseModel


class _SubdomainEnumInput(_BaseModel):
    domain: str
    wordlist: list[str] = ["www", "api", "mail", "dev", "staging", "admin"]


class SubdomainEnumTool(_BaseTool):
    name: str = "subdomain_enum"
    description: str = "Brute-force common subdomains against a domain via DNS."
    args_schema: type[_BaseModel] = _SubdomainEnumInput

    def _run(self, domain: str, wordlist: list[str] = None) -> str:  # type: ignore[override]
        import socket
        if wordlist is None:
            wordlist = ["www", "api", "mail", "dev", "staging", "admin"]
        found = []
        for sub in wordlist:
            fqdn = f"{sub}.{domain}"
            try:
                socket.getaddrinfo(fqdn, None)
                found.append(fqdn)
            except socket.gaierror:
                pass
        return f"Found: {found}" if found else "No subdomains resolved."

    async def _arun(self, domain: str, wordlist: list[str] = None) -> str:  # type: ignore[override]
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run, domain, wordlist or [])


async def example_l_multi_tools(task: str) -> None:
    """L: Four ways to add custom tools — @tool, StructuredTool, BaseTool subclass, add_tools list.

    Patterns shown:
      1. @tool          — decorator, simplest for quick tools
      2. StructuredTool — from_function(), explicit name/description
      3. BaseTool       — full subclass with async _arun support
      4. add_tools()    — pass a list to add multiple tools at once
         add_tool()     — singular, for one tool at a time

    All patterns work with or without native RAI tools.
    Mix and match freely — they're all BaseTool under the hood.
    """
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("recon-custom")
        # Drop ALL 80+ RAI built-ins — agent gets ONLY the tools below.
        # Remove this line to keep native tools and add yours on top.
        .without_native_tools()
        .without_subagents()
        .without_memory()
        .without_skills()
        .without_hitl()

        # Pattern 4a: add_tools() — pass a list (most common for multiple tools)
        .add_tools([
            whois_lookup,       # @tool
            asn_lookup,         # @tool
            banner_grab,        # StructuredTool
        ])
        # Pattern 4b: add_tool() — singular, chain as many times as needed
        .add_tool(SubdomainEnumTool())   # BaseTool subclass

        .system_prompt(
            "You are a passive reconnaissance specialist. "
            "Use whois_lookup, asn_lookup, banner_grab, and subdomain_enum "
            "to map the attack surface of the given domain. "
            "Report all findings in structured markdown."
        )
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)
        print(f"thread_id: {agent.thread_id}")


# ===========================================================================
# M. MCP tools — file path, inline dict, auto-discovery, and disable
# ===========================================================================

async def example_m_mcp(task: str) -> None:
    """M: Three ways to wire up MCP servers, plus how to disable MCP entirely.

    MCP tools are loaded as BaseTool instances and appended to extra_tools
    automatically — they work alongside @tool / StructuredTool / native tools.

    Patterns shown:
      M1. Auto-discovery   — default; reads ~/.rai/.mcp.json + project .mcp.json
      M2. with_mcp(path)   — load from an explicit JSON file path
      M3. with_mcp_servers — inline dict, no file needed
      M4. without_mcp()    — disable ALL MCP loading (useful in CI / sandboxed envs)
      M5. MCP + native RAI tools combined
      M6. MCP-only (no native tools)
    """

    # ---- M1: Auto-discovery (default behaviour — no extra calls needed) ----
    # RAI reads ~/.rai/.mcp.json and project .mcp.json automatically.
    # Just build the agent normally — MCP tools are already in the toolset.
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("mcp-auto")
        .without_hitl()
        .build()
    ) as agent:
        print("[M1] auto-discovery — MCP tools loaded from ~/.rai/.mcp.json")
        result = await agent.run(task)
        print(result)

    # ---- M2: with_mcp(path) — explicit config file ----
    # Pass one or more paths to .mcp.json files.
    # Useful when the config lives outside ~/.rai/ (e.g. per-project or per-customer).
    mcp_config_path = str(Path.home() / ".rai" / ".mcp.json")  # use your own path
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("mcp-explicit")
        .without_hitl()
        # Multiple files? Chain calls: .with_mcp(path1).with_mcp(path2)
        # Or pass all at once: .with_mcp(path1, path2)
        .with_mcp(mcp_config_path)
        .build()
    ) as agent:
        print(f"[M2] explicit path — {mcp_config_path}")
        result = await agent.run(task)
        print(result)

    # ---- M3: with_mcp_servers — inline dict, no file on disk ----
    # Accepts Claude-format {"mcpServers": {...}} or bare server dict.
    # Ideal for programmatic or dynamic server registration.
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("mcp-inline")
        .without_hitl()
        .with_mcp_servers({
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                },
                "brave-search": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-brave-search"],
                    "env": {"BRAVE_API_KEY": "your-key-here"},
                },
            }
        })
        # Bare dict also accepted (auto-wrapped in {"mcpServers": ...}):
        # .with_mcp_servers({"filesystem": {"command": "npx", "args": [...]}})
        .build()
    ) as agent:
        print("[M3] inline dict — filesystem + brave-search MCP servers")
        result = await agent.run(task)
        print(result)

    # ---- M4: without_mcp() — disable ALL MCP loading ----
    # Useful in CI, sandboxed environments, or when you want a pure tool set.
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("mcp-disabled")
        .without_hitl()
        .without_mcp()   # skip ~/.rai/.mcp.json, project .mcp.json, everything
        .build()
    ) as agent:
        print("[M4] MCP disabled — no MCP tools loaded")
        result = await agent.run(task)
        print(result)

    # ---- M5: MCP + native RAI tools combined ----
    # Native tools (bash, http_request, nuclei, etc.) + MCP tools coexist.
    # No extra wiring needed — they all land in the same tool list.
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("mcp-plus-native")
        .without_hitl()
        # with_native_tools() is the default — just add MCP on top
        .with_mcp_servers({
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            }
        })
        # Also add a custom @tool on top
        .add_tool(whois_lookup)
        .build()
    ) as agent:
        print("[M5] native RAI tools + MCP + custom @tool — all combined")
        result = await agent.run(task)
        print(result)

    # ---- M6: MCP-only — disable native tools, load only MCP ----
    # Clean slate: agent receives exactly what the MCP servers expose.
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("mcp-only")
        .without_native_tools()   # drop all 80+ RAI built-ins
        .without_subagents()
        .without_memory()
        .without_hitl()
        .with_mcp_servers({
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            }
        })
        .build()
    ) as agent:
        print("[M6] MCP-only — zero native tools, only filesystem MCP server")
        result = await agent.run(task)
        print(result)


# ===========================================================================
# CLI dispatcher
# ===========================================================================

EXAMPLES = {
    "A": ("Custom agent — own tools only", example_a, "DNS resolve and TLS check example.com"),
    "B": ("Native RAI tools + custom WAF tool", example_b, "Check WAF on example.com then scan for OWASP Top 10"),
    "C1": ("Custom subagent — plain spec", example_c_plain, "Perform passive recon on example.com and write a report"),
    "C2": ("Custom subagent — merge with AGENTS.md", example_c_merge, "Build a STRIDE threat model for a REST API"),
    "D": ("Read-only filesystem backend", example_d, "Find SQL injection in src/"),
    "E3": ("Target-scoped memory", example_e_target_memory, "Enumerate attack surface"),
    "F": ("Custom middleware — logging + scope enforcer", example_f, "Test HTTP endpoints on api.acme-corp.com"),
    "G1": ("Custom system prompt — replace", example_g_replace, "Audit the login endpoint at api.acme-corp.com/v2/auth"),
    "G2": ("Custom system prompt — extend", example_g_extend, "Find broken access control issues"),
    "H": ("Headless CI mode", example_h, "SAST scan of src/ directory"),
    "J": ("Low-level factory", example_j_factory, "Full pentest with custom harness"),
    "K": ("HTTP server + TUI", example_k_tui, ""),
    "L": ("Multiple custom tools — @tool / StructuredTool / BaseTool / add_tools", example_l_multi_tools, "Enumerate example.com attack surface"),
    "M": ("MCP tools — auto-discovery / file / inline / disable / combined", example_m_mcp, "List files in /tmp"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="RAI SDK Cookbook")
    parser.add_argument("--example", choices=list(EXAMPLES.keys()), default="A")
    parser.add_argument("--task", default="")
    parser.add_argument("--list", action="store_true", help="List all examples")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if args.list:
        print("\nAvailable examples:")
        for key, (desc, _, default_task) in EXAMPLES.items():
            print(f"  {key:4s} — {desc}")
            print(f"       default task: {default_task}")
        return

    desc, fn, default_task = EXAMPLES[args.example]
    task = args.task or default_task
    print(f"\nRunning example {args.example}: {desc}")
    print(f"Task: {task}\n")

    if args.example == "K":
        example_k_tui(task)
    elif args.example == "C3":
        asyncio.run(example_c_tools_map(task))
    elif args.example == "E1":
        asyncio.run(example_e_project_md(task))
    elif args.example == "E2":
        asyncio.run(example_e_arbitrary_paths(task))
    elif args.example == "I":
        tid = asyncio.run(example_i_start(task))
        print("Running follow-up...")
        asyncio.run(example_i_resume(tid, "Summarise the findings found so far."))
    elif fn.__code__.co_varcount == 2:  # two-arg functions (task + extra)
        asyncio.run(fn(task, ""))
    else:
        asyncio.run(fn(task))


if __name__ == "__main__":
    main()
