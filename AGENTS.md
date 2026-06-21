# RAI — Developer & Agent Guide

> Canonical reference for all AI agents and developers working on this project.
> Claude Code: `CLAUDE.md` routes here via `@AGENTS.md`.

---

## Project Overview

**revolt-rai** (`rai`) is an open-source AI security operator — autonomous, adaptive, full-spectrum cybersecurity: threat modeling, SAST, pentesting, red team, bug bounty, VAPT, SOC.

- **Python**: 3.11+ | **Current version**: `2.1.0` (see `pyproject.toml`)
- **Core framework**: `deepagents==0.5.3` (pinned — do not upgrade without full regression)
- **Agent graph**: LangGraph `CompiledStateGraph` + SQLite checkpointer (`~/.rai/sessions.db`)
- **Model default**: `anthropic:claude-sonnet-4-6`
- **Claude SDK**: `claude-agent-sdk>=0.2.89` — no API key, uses Claude Code subscription (`src/rai/engine/claude_sdk/`)

---

## Repository Layout

```
rai/
├── src/rai/                    # Python core package
│   ├── __init__.py             # version = 2.1.0, top-level exports
│   ├── cli/                    # CLI entry points
│   │   ├── main.py             # Typer app: chat, http, agents, threads, tools, etc.
│   │   ├── http_server.py      # `rai http serve` — uvicorn launcher
│   │   ├── serve.py            # `rai serve` — legacy alias
│   │   ├── claude_server.py    # `rai claude-serve` — FastAPI SSE proxy for Claude SDK
│   │   ├── explorer.py         # `rai explore` — interactive codebase explorer
│   │   └── refs.py             # `rai refs` — reference management
│   ├── sdk/                    # Public Python SDK surface
│   ├── engine/                 # Agent factory + model builder
│   │   ├── factory.py          # create_rai_agent() — LangGraph graph compilation + 22-layer middleware
│   │   ├── model.py            # Model string builder + provider routing
│   │   ├── runner.py           # run_agent() coroutine (CLI mode)
│   │   └── claude_sdk/         # Claude SDK engine (no API key — Claude Code subscription)
│   │       ├── __init__.py     # ClaudeAgent, ClaudeAgentBuilder, ClaudeAgentRegistry
│   │       ├── agent.py        # RunnableClaudeAgent — async context manager + stream API
│   │       ├── builder.py      # ClaudeAgentBuilder — fluent builder
│   │       ├── registry.py     # ClaudeAgentRegistry — multi-agent register/build
│   │       ├── options.py      # build_claude_options() — RAI config → ClaudeAgentOptions
│   │       ├── hooks.py        # Hook factories: audit, rate_limit, build_default_hooks
│   │       ├── session.py      # ClaudeSessionManager — list/read/mutate Claude sessions
│   │       └── subagents.py    # load_claude_subagents() — AGENTS.md → AgentDefinition dict
│   ├── harness/                # FastAPI HTTP server + SSE + HITL + plan + subagents
│   │   ├── app.py              # AgentPool + FastAPI app wiring + lifespan
│   │   ├── runner.py           # execute_run() coroutine — streaming, HITL, subagent watcher
│   │   ├── sse.py              # RunEventBus (replay buffer) + ThreadNotifBus
│   │   ├── models.py           # Pydantic request/response models
│   │   ├── selflearn.py        # Self-learning memory phase after runs
│   │   ├── tasks.py            # Background task store helpers
│   │   ├── plan/               # Plan mode tools (write_plan, enter_step, etc.)
│   │   │   └── tools.py        # 7 plan tools + PlanModeMiddleware integration
│   │   ├── routes/             # FastAPI routers
│   │   │   ├── system.py       # /ok, /agents, /server-info
│   │   │   ├── agents.py       # /agents/{name}/runs
│   │   │   ├── runs.py         # /agents/{name}/runs/{id}/stream, cancel
│   │   │   ├── runtime.py      # /agents/runtime — dynamic agent registration
│   │   │   ├── threads.py      # /threads — list, history, delete, compact
│   │   │   ├── hitl.py         # /threads/{id}/interrupt, ask_user, session approval
│   │   │   ├── tasks.py        # /tasks — background task listing
│   │   │   ├── pipelines.py    # /pipelines — DAG pipeline management
│   │   │   └── notifications.py # /threads/{id}/notifications — SSE notification stream
│   │   ├── subagents/          # v1 subagent system (deepagents LocalAsync bridge)
│   │   │   ├── registry.py     # _RUN_CONTEXT, _SUBAGENT_REGISTRY, _SUBAGENT_BUSES
│   │   │   ├── executor.py     # recover_incomplete_subagents()
│   │   │   ├── routes.py       # /subagents/{id}/... endpoints
│   │   │   └── tools.py        # Legacy subagent tools
│   │   └── subagents_v2/       # v2 async subagent system (production)
│   │       ├── service.py      # SubagentService singleton — lifecycle, HITL, completion queues
│   │       ├── executor.py     # run_subagent() — compile, stream, HITL loop, output discipline
│   │       ├── tools.py        # 10 LLM tools: Subagent, MultiSubagents, Pipeline, etc.
│   │       ├── models.py       # AsyncTask TypedDict, CompletionNotification, TERMINAL_STATUSES
│   │       ├── middleware.py   # WriteTruncateMiddleware for subagent output
│   │       └── routes.py       # /subagents/v2/... endpoints
│   ├── tui/                    # Textual TUI (HTTP harness mode)
│   │   ├── app.py              # RaiHttpTUIApp (~2200 lines)
│   │   ├── runner.py           # SSEEventPump — translates SSE → Textual Messages
│   │   ├── history.py          # Persistent prompt history (~/.rai/history.jsonl)
│   │   ├── themes.py           # 4 themes: rai, github-dark, glass, claude
│   │   ├── branding.py         # ASCII art logo + startup text
│   │   ├── screens/            # 7 modal screens
│   │   │   ├── agents_screen.py    # Agent browser + subagent tree
│   │   │   ├── thread_browser.py   # Thread resume picker
│   │   │   ├── runs_browser.py     # Active runs list
│   │   │   ├── run_detail.py       # Live run detail view
│   │   │   ├── theme_picker.py     # Theme selection
│   │   │   ├── model_picker.py     # Model override picker
│   │   │   └── mcp_viewer.py       # MCP server status viewer
│   │   ├── widgets/            # 16 custom widgets
│   │   │   ├── messages.py         # UserMsg, AssistantMsg, ToolCallMsg, HistoryDivider, CompactMsg
│   │   │   ├── chat_input.py       # InputBar with history + auto-complete
│   │   │   ├── header.py           # HeaderBar (agent, thread, model)
│   │   │   ├── status_bar.py       # StatusBar (branch, keybindings)
│   │   │   ├── welcome.py          # WelcomeBanner (recent threads)
│   │   │   ├── hitl_panel.py       # HITL approval/deny/edit panel
│   │   │   ├── ask_user_panel.py   # AskUser inline buttons + radio/checkbox
│   │   │   ├── plan_panel.py       # Plan display panel
│   │   │   ├── plan_step_panel.py  # Plan step progress tracker
│   │   │   ├── subagent_tree.py    # SubagentTreePanel — live subagent status tree
│   │   │   ├── bg_panel.py         # BackgroundRunsPanel
│   │   │   ├── findings_panel.py   # Findings summary panel
│   │   │   ├── approval.py         # Approval button widgets
│   │   │   ├── json_viewer.py      # JSON pretty-print viewer
│   │   │   └── wizard_step.py      # Step wizard widget
│   │   └── claude_tui/         # Claude SDK TUI (separate app)
│   │       ├── app.py              # ClaudeTUIApp — Textual terminal chat UI
│   │       ├── screens/            # 10 modal screens (agents, hitl, mcp, settings, etc.)
│   │       └── widgets/            # 11 widgets (agent_bar, sidebar, thinking, etc.)
│   ├── client/                 # Python HTTP client for harness
│   │   ├── __init__.py         # RAIClient aggregate
│   │   ├── _config.py          # ClientConfig
│   │   ├── _events.py          # 40+ typed event dataclasses + parse_event()
│   │   ├── _sse.py             # SSEStream — chunked SSE parser
│   │   ├── _transport.py       # HTTP transport (httpx-based)
│   │   ├── _types.py           # Shared types
│   │   ├── agents.py           # AgentsClient
│   │   ├── runs.py             # RunsClient
│   │   ├── threads.py          # ThreadsClient (history, compact, delete)
│   │   ├── tasks.py            # TasksClient
│   │   ├── subagents.py        # SubagentsClient
│   │   ├── pipelines.py        # PipelinesClient
│   │   ├── runtime.py          # RuntimeClient
│   │   ├── system.py           # SystemClient
│   │   └── claude/             # ClaudeServeClient (8 modules)
│   │       ├── chat.py         # ChatNamespace — stream()
│   │       ├── sessions.py     # SessionsNamespace — CRUD + permissions
│   │       ├── history.py      # HistoryNamespace — list, messages, subagents
│   │       └── health.py       # HealthNamespace — ok()
│   ├── middleware/             # 26 middleware modules
│   │   ├── allowed_tools.py    # Per-run tool whitelist (ContextVar-based)
│   │   ├── audit.py            # Async tool call logging to ~/.rai/audit.log
│   │   ├── cache_split.py      # Static/dynamic prompt cache breakpoint
│   │   ├── cache_ttl.py        # Cache TTL management
│   │   ├── compression.py      # Layer 1: char-based message trimming (≤75k budget)
│   │   ├── cost_tracking.py    # Per-session cost accumulation
│   │   ├── docker_router.py    # Route bash/execute to Docker container (--docker)
│   │   ├── execute.py          # Route execute → RAI BashTool
│   │   ├── findings.py         # Findings count enrichment in system prompt
│   │   ├── hooks.py            # Claude Code PreToolUse/PostToolUse hooks
│   │   ├── loop_detection.py   # Blocks duplicate tool calls (dedup window)
│   │   ├── memory.py           # Inline ≤4k files, index large ones
│   │   ├── message_cache.py    # Message-level caching
│   │   ├── model_logger.py     # Debug logging (RAI_DEBUG_LOG_CALLS=1)
│   │   ├── model_override.py   # Per-call model switching via env var
│   │   ├── opplan.py           # OPPLAN middleware (CLI mode)
│   │   ├── plan_mode.py        # PlanModeMiddleware (HTTP harness mode)
│   │   ├── prompt_cache.py     # Anthropic prompt caching headers
│   │   ├── ratelimit.py        # Per-tool rate limits (aggressive/normal/stealth)
│   │   ├── request_inspector.py # MITM proxy debug (RAI_INSPECT=1)
│   │   ├── retry.py            # Automatic retry on transient failures
│   │   ├── rtk.py              # RTK tool rewriting
│   │   ├── sanitizer.py        # Empty content block sanitization (Bedrock)
│   │   ├── tool_compaction.py  # Layer 2: truncate old tool results
│   │   └── write_truncate.py   # Truncate file-write tool args in ALL messages
│   ├── tools/                  # 8 tool domains
│   │   ├── core/               # bash, findings, memory, opplan, references
│   │   ├── security/           # http_request, nuclei, nmap, web_search, web_fetch, subagent
│   │   ├── web/                # jwt_decode, jwt_forge, oauth_audit, graphql_introspect
│   │   ├── cloud/              # aws_cli, gcp_cli, az_cli, kubectl, terraform_scan
│   │   ├── container/          # docker_audit, docker_escape_check, k8s_pod_escape
│   │   ├── active_directory/   # bloodhound, kerberoast, dcsync, ldap_enum
│   │   ├── android/            # apk_decompile, manifest_audit, frida_inject
│   │   └── reversing/          # binary_info, rop_gadgets, disassemble
│   ├── agents/                 # AGENTS.md parser + subagent loader
│   │   ├── parser.py           # YAML frontmatter + markdown body parser
│   │   ├── loader.py           # load_subagents_for() — discovers ~/.rai/agents/
│   │   └── background.py       # Background agent runner (CLI mode)
│   ├── mcp/                    # MCP (Model Context Protocol) integration
│   │   ├── config.py           # MCP config loader (.mcp.json)
│   │   ├── loader.py           # resolve_and_load_mcp_tools() — async tool loading
│   │   └── session.py          # MCP session lifecycle management
│   ├── sessions/               # Thread persistence
│   │   ├── store.py            # SQLite helpers, build_stream_config(), get_checkpointer()
│   │   └── tasks.py            # Task store (SQLite-backed background task persistence)
│   ├── config/                 # Configuration
│   │   ├── agent.py            # load_agent_config() → AgentConfig (config.toml)
│   │   └── settings.py         # RAISettings singleton
│   └── data/                   # Bundled prompts, skills, OPPLAN templates
│       ├── prompts/            # Per-agent system prompts (rai/, recon/, sast/, etc.)
│       └── skills/             # Built-in skill definitions
│
├── static/                     # Static assets (rai-logo.png) — copied into Docker
├── docker/
│   └── Dockerfile              # Multi-stage build, non-root rai user, port 8000
├── tests/
│   └── test_agentic_loop.py
├── examples/                   # 17 Python SDK examples + 10 Claude SDK examples
├── .github/workflows/
│   ├── publish-pypi.yml        # PyPI publish on GitHub Release
│   └── publish-docker-image.yml# GHCR publish on Release / tag / main push
├── pyproject.toml              # revolt-rai 2.1.0, hatchling build
├── AGENTS.md                   # This file
├── CLAUDE.md                   # Routes to @AGENTS.md
└── release.md                  # Release notes
```

---

## Architecture — Execution Flow

### CLI → HTTP → Agent → SSE → TUI

```
User input
    │
    ▼
CLI (main.py) ─── spawns ──→ HTTP Server (uvicorn + FastAPI)
    │                              │
    ▼                              ▼
TUI (app.py) ←── SSE stream ─── Runner (execute_run)
    │                              │
    │                              ▼
    │                         AgentPool.get_graph()
    │                              │
    │                              ▼
    │                         LangGraph.astream_events()
    │                              │
    │                              ├──→ 22 middleware layers
    │                              ├──→ LLM API call
    │                              └──→ Tool execution
    │                              │
    ▼                              ▼
Textual widgets ←── SSE ──── RunEventBus (replay buffer=500)
```

### Context Segregation (Main Agent vs Subagents)

```
Main Agent                          Subagent (per task_id)
─────────────────                   ─────────────────────
thread_id: user's thread            thread_id: task_id (unique)
graph: pool compiled graph          graph: freshly compiled
config: user's stream config        config: task-specific
middleware: own instances           middleware: own instances
checkpointer: shared SQLite         checkpointer: same SQLite (isolated by thread_id)
```

**Key guarantee**: Subagent messages NEVER enter the main agent's checkpoint.
The only cross-pollination is completion notifications injected as HumanMessage
into the main agent's context (≤400 char preview + output file path).

---

## Critical Bug Fixes

### v2.0.1 — Original 9 critical fixes

| # | File | Fix | Severity |
|---|------|-----|----------|
| 1 | `middleware/audit.py` | `await asyncio.to_thread()` — non-blocking audit log | Critical |
| 2 | `harness/routes/hitl.py` | `_SESSION_APPROVED[thread_id]` — session approval actually works | Critical |
| 3 | `harness/sse.py` | `RunEventBus.cleanup()` on shutdown — no memory leak | High |
| 4 | `tools/core/memory.py` | `content_file` allowlist — path traversal blocked | Security |
| 5 | `sessions/store.py` | `get_thread_by_id_sync()` — O(1) thread lookup | Performance |
| 6 | `harness/routes/threads.py` | Correct agent graph used per thread | High |
| 7 | `engine/factory.py` | Credentials bridged to env for `ConfigurableModelMiddleware` | Critical |
| 8 | `engine/factory.py` | `setdefault` → `if not os.environ.get()` — empty env vars handled | Critical |
| 9 | `cli/http_server.py` | `try/finally` + SIGTERM — server always stops cleanly | Critical |

**Build fix**: `pyproject.toml` — `src/rai/**` added to sdist `include`.

### v2.0.2 — Token optimisation fixes

| # | File | Fix | Impact |
|---|------|-----|--------|
| 10 | `middleware/compression.py` | `_char_estimate()` now counts tool_call args | Critical |
| 11 | `engine/factory.py` | `TOOL_RESULT_TOKEN_LIMIT` 8000→4000 tokens | High |
| 12 | `engine/factory.py` | Summarization uses `cfg.compact_model` (cheap model) | High |
| 13 | `harness/plan/tools.py` | `exit_plan_mode` result stripped full step digest | Medium |
| 14 | `harness/plan/tools.py` | `list_plan_steps` compact for done steps | Medium |
| 15 | `tui/app.py` + `client/threads.py` | `history_tail()` — loads last 50 not first 50 | UX |
| 16 | `tui/app.py` | Filter `<system-reminder>` human messages from history | UX |

### v2.1.0 — Stability + subagent system

| # | File | Fix | Impact |
|---|------|-----|--------|
| 17 | `harness/runner.py` | Remove `<system-info>cwd:</system-info>` injection — BashTool ContextVar handles CWD | Critical (caused new thread no-response) |
| 18 | `tui/app.py` | HistoryDivider only shown when messages exist — no "Resumed thread (0 messages)" on fresh threads | UX |
| 19 | `harness/sse.py` | RunEventBus.subscribe() replays ALL buffered events on first connection (last_event_id=None) | High (publish-before-subscribe race) |
| 20 | `tools/security/web_search.py` | Multi-engine search via ddgs + SSL verify flag + DDG lite fallback | High |
| 21 | `tools/security/web_fetch.py` | Chunked reading + markdownify extraction | High |

---

## Middleware Stack (outermost → innermost)

| # | Class | File | Role |
|---|-------|------|------|
| 1 | `ConfigurableModelMiddleware` | deepagents-cli | Per-request model override via runtime context |
| 2 | `AuditLogMiddleware` | `middleware/audit.py` | Async tool call logging to `~/.rai/audit.log` |
| 3 | `HooksMiddleware` | `middleware/hooks.py` | Claude Code PreToolUse/PostToolUse hooks |
| 3.5 | `LoopDetectionMiddleware` | `middleware/loop_detection.py` | Blocks duplicate tool calls — cached result + warning |
| 3.6 | `ReadFileDedupMiddleware` | `middleware/read_dedup.py` | Dedup file re-reads beyond LoopDetection's window via mtime check |
| 3.7 | `SubagentRestrictMiddleware` | `middleware/subagent_restrict.py` | Blocks `task` tool for subagents (prevents recursive nesting) |
| 4 | `DockerRouterMiddleware` | `middleware/docker_router.py` | Routes bash/execute to Docker container (when `--docker` active) |
| 4.1 | `RTKToolMiddleware` | `middleware/rtk.py` | Rewrites bash commands via `rtk rewrite` |
| 5 | `ExecuteInterceptorMiddleware` | `middleware/execute.py` | Routes execute → RAI BashTool |
| 6 | `RateLimitMiddleware` | `middleware/ratelimit.py` | Per-tool delays (aggressive/normal/stealth) |
| 7 | `AllowedToolsMiddleware` | `middleware/allowed_tools.py` | Per-run tool whitelist enforcement (ContextVar) |
| 8 | `AskUserMiddleware` | deepagents-cli | Injects ask_user tool |
| 9 | `RAIMemoryMiddleware` | `middleware/memory.py` | Inlines ≤4k files, indexes large ones |
| 10 | `SkillsMiddleware` | deepagents-cli | Loads skills from 6 dirs |
| 11 | `LocalContextMiddleware` | deepagents-cli | Security-aware env detection |
| 12 | `StaticSystemPromptCacheBreakpointMiddleware` | `middleware/cache_split.py` | Splits static/dynamic for Anthropic prompt caching |
| 13 | `FindingsEnrichmentMiddleware` | `middleware/findings.py` | Findings count in system prompt |
| 14 | `OPPLANMiddleware` OR `PlanModeMiddleware` | `middleware/opplan.py` / `plan_mode.py` | OPPLAN (CLI) or HTTP plan mode |
| 15 | `ModelOverrideMiddleware` | `middleware/model_override.py` | Per-call model switching via env var |
| 16 | `MessageCompressionMiddleware` | `middleware/compression.py` | **Layer 1** — trim history to ≤75k chars. Zero LLM cost |
| 16.5 | `WriteTruncateMiddleware` | `middleware/write_truncate.py` | Truncate file-write tool args — keeps last 5 writes full, older get 25-line preview + write-info enrichment. Cache-safe (recent writes in dynamic tail) |
| 17 | `ToolResultCompressionMiddleware` | `middleware/tool_compaction.py` | **Layer 2** — truncate old tool results + bash args. Saves 60-80% |
| 18 | `SummarizationToolMiddleware` | `middleware/summarization.py` | **Layer 3** — LLM summarization at 100k tokens or 40 messages. `/compact` |
| 19 | `RAIPromptCachingMiddleware` | `middleware/prompt_cache.py` | Anthropic prompt caching headers |
| 20 | `ModelCallLoggerMiddleware` | `middleware/model_logger.py` | Debug logging when `RAI_DEBUG_LOG_CALLS=1` |
| 21 | `EmptyContentSanitizerMiddleware` | `middleware/sanitizer.py` | Strips empty text blocks (Bedrock compatibility) |

**Subagent-specific**: Subagents get the same middleware stack minus OPPLAN, PlanMode, and subagent spawning tools. The `is_subagent=True` flag in `create_rai_agent()` controls this.

---

## Subagents v2 — Async Background Agent System

### Architecture

```
Main Agent (execute_run)
    │
    ├── calls Subagent/MultiSubagents/Pipeline tool
    │   └── asyncio.create_task(run_subagent(...))
    │
    ├── SubagentService (singleton) — lifecycle + HITL + queues
    │
    ├── Watcher loop — polls for CompletionNotifications
    │   └── On completion → injects <task-notification> → _stream_once()
    │
    └── run_subagent() coroutine per task:
        1. Compile fresh graph (create_rai_agent + is_subagent=True)
        2. build_stream_config(task_id, ...) — task_id IS the thread_id
        3. Create per-task RunEventBus
        4. Stream initial turn
        5. HITL loop (with session-approve bypass)
        6. Output discipline enforcement (write to /tmp/rai_{task_id}_output.md)
        7. Extract output (discipline file > checkpoint > streaming fallback)
        8. Push CompletionNotification to parent's queue
```

### LLM Tools (10 total)

| Tool | Description |
|------|-------------|
| `Subagent` | Launch single background agent. Returns task_id immediately |
| `MultiSubagents` | Launch multiple agents in parallel |
| `Pipeline` | Launch DAG of tasks with dependency ordering |
| `SubagentStatus` | Get current status + output of a task |
| `SubagentCancel` | Cancel a running task |
| `SubagentUpdate` | Send follow-up message to completed task (resumes from checkpoint) |
| `SubagentResponse` | Block and wait for task completion (up to timeout) |
| `SubagentProgress` | Read live checkpoint of running task |
| `ListSubagents` | List available subagent types |
| `ListTasks` | List all tasks for current run with statuses |

### Output Discipline

Every subagent prompt gets `_output_discipline_suffix(task_id)` appended, instructing
it to write structured output to `/tmp/rai_{task_id}_output.md`. After the main stream
completes, `executor.py` enforces this with a second turn if the file doesn't exist.

Priority: `.md file` > `checkpoint last AIMessage` > `streaming text fallback`

---

## Tools (8 domains)

| Domain | Getter | Key tools |
|--------|--------|-----------|
| Core | `get_core_tools()` | `bash`, `findings`, `memory`, `opplan`, `references` |
| Security | `get_security_tools()` | `http_request`, `nuclei_scan`, `nmap_scan`, `web_search`, `web_fetch`, `create_subagent` |
| Web | `get_web_tools()` | `jwt_decode`, `jwt_forge`, `oauth_audit`, `graphql_introspect` |
| Cloud | `get_cloud_tools()` | `aws_cli`, `gcp_cli`, `az_cli`, `kubectl`, `terraform_scan` |
| Container | `get_container_tools()` | `docker_audit`, `docker_escape_check`, `k8s_pod_escape` |
| AD | `get_ad_tools()` | `bloodhound_collect`, `kerberoast`, `dcsync`, `ldap_enum` |
| Android | `get_android_tools()` | `apk_decompile`, `android_manifest_audit`, `frida_inject` |
| Reversing | `get_reversing_tools()` | `binary_info`, `rop_gadgets`, `disassemble` |

---

## HTTP API

Server default: `http://127.0.0.1:8000`

```
GET  /ok                                    Health check
GET  /server-info                           Server metadata (agents, version, model)
GET  /agents                                List registered agents

POST /agents/{name}/runs                    Create run (returns run_id)
GET  /agents/{name}/runs/{id}/stream        SSE stream of run events
POST /agents/{name}/runs/{id}/cancel        Cancel run
POST /agents/{name}/runs/{id}/plan/approve  Approve plan
POST /agents/{name}/runs/{id}/plan/reject   Reject plan

POST /agents/runtime/register               Register runtime agent
DELETE /agents/runtime/{name}               Remove runtime agent

GET  /threads                               List threads (filterable by agent)
GET  /threads/{id}/history                  Paginated message history (offset+limit)
GET  /threads/{id}/history/tail             Last N messages (for TUI)
DELETE /threads/{id}                        Delete thread
POST /threads/{id}/interrupt                Submit HITL decision
POST /threads/{id}/ask_user                 Submit ask_user answers
POST /threads/{id}/compact                  Manual context compaction
GET  /threads/{id}/compact/status           Compaction stats

GET  /threads/{id}/notifications            SSE notification stream (ThreadNotifBus)

GET  /tasks                                 Background tasks list
POST /subagents/{id}/interrupt              Subagent HITL decision
GET  /subagents/v2/tasks                    v2 subagent task listing
GET  /subagents/v2/{id}/stream              Per-subagent SSE stream
POST /subagents/v2/{id}/interrupt           v2 subagent HITL

GET  /pipelines                             Pipeline status
```

### SSE Events (40+ total)

**Run lifecycle**: `run_start` → `run_keepalive` → `run_end`

**Content**: `token` → `thinking` → `tool_start` → `tool_end`

**HITL**: `interrupt` → `session_approved` → `ask_user_request`

**Plan mode**: `plan_mode_entered` → `plan_ready` → `step_start` → `step_complete` → `plan_completed`

**Subagents**: `subagent_started` → `subagent_token` → `subagent_thinking` → `subagent_tool_start` → `subagent_tool_end` → `subagent_interrupt` → `subagent_completed`

**Watcher**: `watcher_invoke` → `notification` → `task_status` → `task_completed`

**Pipeline**: `pipeline_batch` → `pipeline_end`

---

## SSE Replay Buffer

`RunEventBus` maintains a capped replay buffer (500 frames) with monotonically
increasing event IDs. Clients reconnect with `Last-Event-ID` header to receive
missed events without duplicates.

**First connection (no Last-Event-ID)**: replays ALL buffered events to cover
the race where `execute_run` publishes before the client subscribes.

**Reconnection (Last-Event-ID=N)**: replays only events with ID > N.

**Closed run**: replays buffer then returns immediately (no hang).

---

## Config Paths

```
~/.rai/
├── agents/{name}/
│   ├── config.toml          # model, api_key, base_url, temperature, max_tokens
│   ├── AGENTS.md            # Subagent definitions (YAML frontmatter + system prompt)
│   ├── prompt.md            # System prompt override (highest priority)
│   ├── mcp.json             # Per-agent MCP servers {"mcpServers": {...}}
│   └── memory/              # user.md, feedback.md, engagement.md, findings.md
├── .mcp.json                # Global MCP servers
├── audit.log                # All tool calls (JSON lines)
├── history.jsonl            # TUI prompt history (1000 entries max)
├── sessions.db              # LangGraph SQLite checkpointer
└── debug/
    ├── model-calls.jsonl    # ModelCallLoggerMiddleware output
    └── requests.jsonl       # RequestInspectorMiddleware output
```

### config.toml format

```toml
model = "litellm:openai/bedrock-claude-sonnet-4.6-(US)"
api_key = "sk-..."
base_url = "https://llmproxy.example.com"
temperature = 0.7
max_tokens = 8192
rate_limit_profile = "normal"  # aggressive | normal | stealth

# Optional: cheaper model for context summarization (empty = inherit main model)
# Set via: rai agents config-set --compact-model "litellm:openai/bedrock-claude-haiku-4.5-(US)"
compact_model = ""           # empty = use same model as above
compact_api_key = ""         # empty = inherit api_key
compact_base_url = ""        # empty = inherit base_url
```

---

## AGENTS.md Format (subagent definitions)

```markdown
---
name: recon
description: Network reconnaissance and asset discovery
model: inherit
api_key: inherit
base_url: inherit
---

You are a reconnaissance specialist…
```

For Claude SDK subagents, additional frontmatter fields:
```yaml
mcp_servers: server1,server2   # name refs from parent MCP config
max_turns: 30
effort: high
permission_mode: bypassPermissions
```

---

## Plan Mode

Structured multi-step execution with approval gate.

1. Agent calls `enter_plan_mode()` → `plan_mode_entered` SSE
2. Agent calls `write_plan(steps=[...])` → `plan_ready` SSE
3. User approves: `POST /agents/{name}/runs/{id}/plan/approve`
4. Agent iterates: `enter_step(n)` → work → `mark_step_done(n)`
5. `exit_plan_mode()` → optional self-learning phase

**Rule**: `_PLAN_FUTURES` is only set when `plan_ready` fires. Calling approve before that returns 409.

Plan exec tools suppressed from UI: `enter_plan_mode`, `enter_step`, `mark_step_done`, `mark_step_blocked`, `exit_plan_mode`, `list_plan_steps`

---

## MCP Integration

```
src/rai/mcp/
├── config.py     # Load .mcp.json files (per-agent + global)
├── loader.py     # resolve_and_load_mcp_tools() — async tool loading
└── session.py    # MCP session lifecycle (stdio client management)
```

MCP tools are loaded during agent compilation (`AgentPool.get_graph()`).
The lifespan task owns the async context — MCP stdio clients require
entering/exiting cancel scopes in the same task.

Per-agent MCP: `~/.rai/agents/{name}/mcp.json`
Global MCP: `~/.rai/.mcp.json`

---

## Claude SDK — `rai.engine.claude_sdk`

No Anthropic API key required. Uses the user's Claude Code subscription. Claude Code's native tools (Bash, Read, Edit, WebFetch, etc.) cover all security needs — no custom tool wrapping.

### Architecture

| Class | File | Role |
|---|---|---|
| `ClaudeAgent` | `engine/claude_sdk/__init__.py` | Entry point — `ClaudeAgent.builder()` / `.registry()` |
| `ClaudeAgentBuilder` | `engine/claude_sdk/builder.py` | Fluent builder mirroring `RAIAgentBuilder`; `build()` is sync |
| `RunnableClaudeAgent` | `engine/claude_sdk/agent.py` | Async context manager; `.run()` blocking + `.stream()` token-by-token |
| `ClaudeAgentRegistry` | `engine/claude_sdk/registry.py` | Multi-agent registration; `.default()` auto-discovers from `~/.rai/agents/` |
| `build_claude_options()` | `engine/claude_sdk/options.py` | Loads system prompt / subagents / MCP / model from `~/.rai/` → `ClaudeAgentOptions` |
| `load_claude_subagents()` | `engine/claude_sdk/subagents.py` | Scans `~/.rai/agents/` → `dict[str, AgentDefinition]`; always excludes `"rai"` |
| `build_default_hooks()` | `engine/claude_sdk/hooks.py` | Returns `PreToolUse` (rate limit) + `PostToolUse` (audit) `HookMatcher` dict |
| `ClaudeSessionManager` | `engine/claude_sdk/session.py` | List / read / mutate Claude Code sessions and subagent transcripts |

### What RAI injects into the SDK (only 3 things)

1. **`system_prompt`** — `~/.rai/agents/{name}/prompt.md` → bundled `src/rai/data/prompts/{name}/prompt.md` → full RAI security prompt
2. **`agents`** — all `~/.rai/agents/*/AGENTS.md` subdirs → `AgentDefinition` objects (excludes `"rai"` + calling agent itself)
3. **`mcp_servers`** — merge `~/.rai/agents/{name}/mcp.json` + global `~/.rai/.mcp.json`

### Builder API

```python
ClaudeAgent.builder()
    .agent_name("rai")               # ~/.rai/agents/rai/ — prompt + subagents + mcp
    .model("claude-opus-4-8")        # bare model ID or anthropic:/litellm: prefix (stripped)
    .effort("high")                  # low | medium | high | xhigh | max
    .session_id("uuid")              # use specific session
    .resume("uuid")                  # resume existing session
    .max_turns(30)
    .system_prompt("custom prompt")  # fully override
    .with_hitl()                     # permission_mode = "default"
    .without_hitl()                  # permission_mode = "bypassPermissions" (default)
    .plan_mode()                     # permission_mode = "plan" — agent plans but executes no tools
    .with_thinking()                 # {"type": "adaptive"}
    .without_thinking()              # {"type": "disabled"}
    .with_partial_messages()         # include StreamEvent for token streaming
    .add_agent("name", AgentDefinition(...))   # add custom subagent
    .without_subagents()             # skip AGENTS.md loading
    .with_mcp({"server": {...}})     # add MCP servers
    .without_mcp()                   # disable all MCP
    .add_hook("PreToolUse", HookMatcher(hooks=[my_fn]))
    .can_use_tool(my_async_callback)  # HITL: async (tool_name, input, ctx) → PermissionResult
    .build()                         # → RunnableClaudeAgent (sync)
```

### Session management API

```python
from rai.sdk import ClaudeSessionManager, SDKSessionInfo, SessionMessage

mgr = ClaudeSessionManager(directory="/path/to/project")

sessions: list[SDKSessionInfo] = mgr.list(limit=20, offset=0)
info: SDKSessionInfo | None = mgr.info(session_id)
msgs: list[SessionMessage] = mgr.messages(session_id, limit=50, offset=0)
agent_ids: list[str] = mgr.subagents(session_id)
sub_msgs: list[SessionMessage] = mgr.subagent_messages(session_id, agent_ids[0])
history = mgr.full_history(session_id)

mgr.rename(session_id, "Pentest — example.com 2026-06")
mgr.tag(session_id, "pentest")
fork = mgr.fork(session_id, title="Branch from step 3")
mgr.delete(session_id)
```

### Model string handling

`build_claude_options()` strips provider prefixes before passing to the SDK:
- `anthropic:claude-sonnet-4-6` → `claude-sonnet-4-6`
- `litellm:anthropic/claude-opus-4-8` → `claude-opus-4-8`
- `openai/gpt-4o` → `None` (non-Claude → let CLI use its default)
- `sonnet` / `opus` / `haiku` → resolved to full model IDs

---

## Adding New Features — File Checklists

### New tool

1. `src/rai/tools/{domain}/my_tool.py` — `BaseTool` subclass, `_run()` + `_arun()`
2. `src/rai/tools/{domain}/__init__.py` — export the class
3. `src/rai/engine/factory.py` — add to domain getter + `create_rai_agent()` tools list
4. `src/rai/sdk/__init__.py` — export if public
5. `AGENTS.md` — add to Tools table

### New middleware

1. `src/rai/middleware/my_middleware.py` — `AgentMiddleware` subclass (NOT `MiddlewareBase` — use `langchain.agents.middleware.types.AgentMiddleware`). Implement `wrap_tool_call` / `awrap_tool_call` for tool interception OR `wrap_model_call` / `awrap_model_call` for model call interception. Always fail-open.
2. `src/rai/middleware/__init__.py` — add import + add to `__all__`
3. `src/rai/engine/factory.py` — `agent_middleware.append(MyMiddleware())` at correct position (see stack table). Use lazy import inside factory function.
4. `src/rai/sdk/middleware/middleware.py` — add import + add to `__all__`
5. `src/rai/sdk/middleware/__init__.py` — add to imports from `.middleware` + add to `__all__`
6. `src/rai/sdk/__init__.py` — add to `from rai.sdk.middleware import (...)` + add to `__all__`
7. `AGENTS.md` — add row to Middleware Stack table with correct position number, file, and role
8. Add env vars to Environment Variables table if tunable

**Return type for tool middleware**: `ToolMessage` (from `langchain_core.messages`). See `loop_detection.py` for the exact pattern.
**Return type for model middleware**: pass through via `handler(request)` or `request.override(messages=...)`. See `compression.py`.

### New HTTP route

1. `src/rai/harness/routes/my_route.py` — `APIRouter`
2. `src/rai/harness/models.py` — request/response Pydantic models
3. `src/rai/harness/app.py` — include router
4. `src/rai/client/` — add client method
5. `src/rai/sdk/__init__.py` — export
6. `AGENTS.md` — add to HTTP API table

### New SSE event

1. `src/rai/harness/runner.py` — `await bus.publish("my_event", {...})`
2. `src/rai/client/_events.py` — dataclass
3. `src/rai/client/_sse.py` — dispatcher branch
4. `src/rai/sdk/__init__.py` — export Python class
5. `AGENTS.md` — add to SSE events list

### New claude-agent-sdk feature (Python)

1. Implement in `src/rai/engine/claude_sdk/{module}.py`
2. `src/rai/engine/claude_sdk/__init__.py` — add import + `__all__` entry
3. `src/rai/sdk/__init__.py` — add to `from rai.engine.claude_sdk import (...)` + `__all__`
4. `src/rai/__init__.py` — add inside `try/except ImportError` block + `__all__`
5. `AGENTS.md` — update Claude SDK table / builder API section

---

## Debug Tools

### Request Inspector (MITM proxy + request logging)
**File**: `src/rai/middleware/request_inspector.py`

Completely inert in production. Activate for debugging only:
```bash
# Log all LLM requests to ~/.rai/debug/requests.jsonl
RAI_INSPECT=1 rai chat

# Route through Burp Suite / mitmproxy
RAI_INSPECT=1 RAI_INSPECT_PROXY=http://127.0.0.1:8080 rai chat
```

Wire temporarily into factory.py when needed:
```python
if os.environ.get("RAI_INSPECT", "0") == "1":
    from rai.middleware.request_inspector import RequestInspectorMiddleware
    agent_middleware.append(RequestInspectorMiddleware())
```

**What it logs**: `ts`, `model`, `elapsed_ms`, `msg_count`, `total_chars`, `estimated_tokens`, `has_cache_control`, `by_type`, per-message preview.
**Cross-check with**: `~/.rai/debug/model-calls.jsonl` from `ModelCallLoggerMiddleware` — both should show same message counts.

---

## Coding Conventions

- **Python 3.11+**: `from __future__ import annotations`, `X | Y` unions, `TypedDict`
- **Async I/O**: blocking calls in async context → `await asyncio.to_thread(fn, *args)`
- **No comments** narrating what code does — names convey that
- **Commit policy**: never commit unless user explicitly asks; no Co-Authored-By
- **Textual widgets**: always use `_escape()` helper — never `rich.markup.escape()`
- **Security**: `content_file` paths must validate against `/tmp/`, `$TMPDIR/`, `~/.rai/`
---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RAI_DEBUG_LOG_CALLS` | `0` | Enable `ModelCallLoggerMiddleware` → `~/.rai/debug/model-calls.jsonl` |
| `RAI_DEBUG_LOG_FILE` | `~/.rai/debug/model-calls.jsonl` | Override debug log path |
| `RAI_COMPACT_MSG_TRIGGER` | `40` | Message count that fires `SummarizationMiddleware` |
| `RAI_COMPACT_TOKEN_TRIGGER` | `100000` | Token count that fires `SummarizationMiddleware` |
| `RAI_COMPACT_KEEP` | `20` | Messages kept after summarization compaction |
| `RAI_COMPACT_TRUNCATE_AT` | `30` | Depth at which old tool call args are truncated |
| `RAI_COMPACT_TRUNCATE_MAX` | `2000` | Max chars per tool arg after truncation |
| `RAI_COMPACT_RESULT_KEEP` | `20` | `ToolResultCompressionMiddleware`: last N messages never touched |
| `RAI_COMPACT_RESULT_MAX` | `1500` | `ToolResultCompressionMiddleware`: max chars for old tool results |
| `RAI_COMPACT_CMD_MAX` | `600` | `ToolResultCompressionMiddleware`: max chars for old bash args |
| `RAI_COMPACT_FINDINGS_ARG_MAX` | `250` | `ToolResultCompressionMiddleware`: max chars for old findings args |
| `RAI_LOOP_WINDOW` | `10` | `LoopDetectionMiddleware`: recent tool calls to track |
| `RAI_LOOP_DISABLED` | `0` | Set to `1` to disable loop detection |
| `RAI_DEDUP_DISABLED` | `0` | Set to `1` to disable read-file deduplication |
| `RAI_KEEP_RECENT_WRITES` | `5` | `WriteTruncateMiddleware`: last N write/edit tool calls kept full (0 = truncate all immediately) |
| `RAI_SEARCH_BACKEND` | `auto` | `web_search` backend(s): `auto`, `brave`, `duckduckgo`, `google`, `startpage`, `yahoo`, `yandex`, `mojeek` |
| `RAI_SEARCH_MAX_RESULTS` | `10` | Max results per `web_search` query |
| `RAI_SEARCH_FETCH_TOP_N` | `5` | Top result pages to fetch content from |
| `RAI_FETCH_TIMEOUT` | `20` | HTTP timeout (seconds) for web fetch/search |
| `RAI_FETCH_CHUNK_SIZE` | `5000` | `web_fetch` chars per call (chunked reading) |
| `RAI_FETCH_MAX_TOTAL` | `200000` | Hard cap on page size before chunking |
| `RAI_COMPACT_MODEL` | — | Cheaper model for summarization |
| `RAI_INSPECT` | `0` | Enable `RequestInspectorMiddleware` |
| `RAI_INSPECT_PROXY` | — | MITM proxy URL (requires `RAI_INSPECT=1`) |
| `RAI_INSPECT_LOG_FILE` | `~/.rai/debug/requests.jsonl` | Override inspector log path |
| `RAI_MODEL_OVERRIDE` | — | Override model for all calls |
| `RAI_RATE_LIMIT_PROFILE` | `normal` | `aggressive` / `normal` / `stealth` |
| `LITELLM_API_KEY` | — | API key for LiteLLM proxy |
| `LITELLM_BASE_URL` | — | Base URL for LiteLLM proxy |
| `OPENAI_API_KEY` | — | OpenAI / LiteLLM proxy key |
| `OPENAI_BASE_URL` | — | OpenAI / LiteLLM proxy URL |
| `RAI_DOCKER_ENV` | `0` | Enable Docker-routed bash execution |
| `RAI_DOCKER_IMAGE` | — | Docker image (required when `RAI_DOCKER_ENV=1`) |
| `RAI_DOCKER_CONTAINER` | `rai-kali` | Container name |
| `RAI_DOCKER_MEMORY` | `4g` | Container memory limit |
| `RAI_DOCKER_NETWORK` | — | Custom Docker network |
| `RAI_DOCKER_SUDO` | `0` | Force `sudo docker` |
| `RAI_DOCKER_SETUP_CMD` | — | Post-start setup command inside container |

---

## Release

```bash
# Python (revolt-rai on PyPI)
git tag v2.1.0
git push origin main && git push origin v2.1.0
# → publish-pypi.yml triggers on GitHub Release

# Docker (GHCR)
# publish-docker-image.yml triggers on release / v* tag / main push
```

---

## Install

```bash
pip install revolt-rai                    # Python CLI + TUI + HTTP server + Claude SDK
pip install revolt-rai[bedrock]           # + AWS Bedrock provider
```

> No `[http]` extra — `fastapi` and `uvicorn` are core deps since v2.0.0.
> `claude-agent-sdk` is bundled since v2.0.3 — no extra needed.

### `rai chat --docker` — Container-Routed Execution

RAI can route all bash/execute tool calls to a Docker container instead of the host.
No default image is provided — users bring their own container (Kali, Parrot, custom pentest images, etc.).

```bash
# Basic — specify any Docker image
rai chat --docker kalilinux/kali-rolling
rai chat --docker parrotsec/security
rai chat --docker myorg/pentest-toolkit:v3

# Via environment variables (equivalent)
RAI_DOCKER_ENV=1 RAI_DOCKER_IMAGE=kalilinux/kali-rolling rai chat

# Custom container name + memory limit
RAI_DOCKER_CONTAINER=my-pentest RAI_DOCKER_MEMORY=8g rai chat --docker kalilinux/kali-rolling

# Attach to a custom Docker network (for target-lab access)
RAI_DOCKER_NETWORK=lab-network rai chat --docker kalilinux/kali-rolling

# Force sudo for Docker commands (Ubuntu/Debian without docker group)
RAI_DOCKER_SUDO=1 rai chat --docker kalilinux/kali-rolling
```

**How it works:**
- A persistent container is started on harness boot (`docker run -d --init ...`)
- All `bash` and `execute` tool calls route through `docker exec` into the container
- A shared workspace is mounted: `<cache_dir>/docker/workspace` → `/workspace`
- Output spill directory: `<cache_dir>/docker/output` → `/tmp/rai_output`
- Container is stopped and removed on harness shutdown

**Cache directory** (via `platformdirs`):
- Linux: `~/.cache/rai/docker/`
- macOS: `~/Library/Caches/rai/docker/`
- Windows: `%LOCALAPPDATA%/rai/Cache/docker/`

**Container capabilities** (security-research oriented):
`NET_RAW`, `NET_ADMIN`, `NET_BIND_SERVICE`, `SYS_PTRACE`, `SETUID`, `SETGID`, `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `KILL`

**Fallback behavior:**
- No image specified → Docker routing disabled, uses normal host bash
- Docker not installed / not accessible → falls back to host bash (warning logged)
- Container fails to start → falls back to host bash

**Building a custom image for RAI:**

```dockerfile
FROM kalilinux/kali-rolling

RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap sqlmap nikto gobuster hydra metasploit-framework \
    python3 python3-pip curl wget git jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
CMD ["tail", "-f", "/dev/null"]
```

```bash
docker build -t my-rai-image .
rai chat --docker my-rai-image
```

**Environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `RAI_DOCKER_ENV` | `0` | Enable Docker-routed execution (set automatically by `--docker`) |
| `RAI_DOCKER_IMAGE` | — | Docker image to use (required — no default) |
| `RAI_DOCKER_CONTAINER` | `rai-kali` | Container name |
| `RAI_DOCKER_MEMORY` | `4g` | Container memory limit |
| `RAI_DOCKER_NETWORK` | — | Custom Docker network to attach |
| `RAI_DOCKER_SUDO` | `0` | Force `sudo docker` commands |
| `RAI_DOCKER_SETUP_CMD` | — | Shell command to run inside container after start (e.g. `apt install -y nmap`) |

---

### `rai claude` — Textual TUI (Claude SDK)

```bash
rai claude                                  # HITL on, agent=rai, port=8001
rai claude --agent pentest --port 8080      # custom agent + port
rai claude --model claude-opus-4-8          # model override
rai claude --no-hitl                        # autonomous (bypassPermissions)
rai claude --theme rai                      # theme: rai, github-dark, glass, claude
```

Starts `rai claude-serve` as a background process, then launches the Textual TUI.
Streams tokens live, shows tool call cards, subagent progress, thinking blocks.
HITL prompts appear as interactive approval/deny panels. Server stops on TUI exit.

### `rai claude-serve` — FastAPI SSE proxy

```bash
rai claude-serve                            # agent=rai, port=8001
rai claude-serve --agent recon --port 8002
rai claude-serve --api-key secret123        # optional Bearer auth
```

```bash
# POST /chat → text/event-stream
curl -N -X POST http://127.0.0.1:8001/chat \
     -H 'Content-Type: application/json' \
     -d '{"prompt": "say hello", "session_id": "s1"}'

# GET /ok → health check
curl http://127.0.0.1:8001/ok
```

SSE event types: `session_id`, `token`, `thinking`, `tool_start`, `tool_result`,
`subagent_start`, `subagent_progress`, `subagent_done`,
`hitl_request`, `hitl_response`, `plan_blocked`, `rate_limit`, `run_end`, `error`, `[DONE]`.

### `ClaudeServeClient` — Python async client for `rai claude-serve`

```python
from rai.sdk import (
    ClaudeServeClient, ClaudeServeConfig,
    ClaudeTokenEvent, ClaudeRunEndEvent, ClaudeHITLRequestEvent,
    ClaudeThinkingEvent, ClaudeToolStartEvent, ClaudeToolResultEvent,
    ClaudeSubagentStartEvent, ClaudeSubagentProgressEvent, ClaudeSubagentDoneEvent,
    ClaudeHITLResponseEvent, ClaudePlanBlockedEvent, ClaudeRateLimitEvent,
    ClaudeSessionIdEvent, ClaudeErrorEvent, parse_claude_event,
)
from rai import ClaudeServeClient

async with ClaudeServeClient("http://127.0.0.1:8001", api_key="secret") as c:
    await c.health.ok()

    async for ev in c.chat.stream("scan example.com", session_id="s1"):
        if isinstance(ev, ClaudeTokenEvent):
            print(ev.text, end="", flush=True)
        elif isinstance(ev, ClaudeHITLRequestEvent):
            await c.sessions.resolve_permission(ev.session_id, ev.tool_use_id, "allow")
        elif isinstance(ev, ClaudeRunEndEvent):
            print(f"\ncost=${ev.cost:.4f}, turns={ev.turns}")

    sessions = await c.sessions.list()
    await c.sessions.set_model("s1", "claude-opus-4-8")
    await c.sessions.interrupt("s1")
    await c.sessions.delete("s1")

    history = await c.history.list(limit=20)
    msgs = await c.history.messages("session-uuid", limit=50)
```

**Namespaces**: `c.chat`, `c.sessions`, `c.history`, `c.health`

**Source**: `src/rai/client/claude/` (8 modules)

### Claude SDK usage (no API key)

```python
from rai.sdk import ClaudeAgent, AgentDefinition, HookMatcher, RunResult
from rai import ClaudeAgent

async with ClaudeAgent.builder().agent_name("rai").without_hitl().build() as agent:
    result = await agent.run("scan example.com for vulnerabilities")
```

Requires: Claude Code CLI authenticated (`claude` in PATH). No `ANTHROPIC_API_KEY` needed.
