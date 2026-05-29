# RAI — Developer & Agent Guide

> This file is the canonical reference for all AI agents and developers working on this project.
> Claude Code users: `CLAUDE.md` routes here via `@AGENTS.md`.

---

## Project Overview

**revolt-rai** (`rai`) is an open-source AI security operator — a deployable agent system for penetration testing, red team operations, security research, and automated code analysis. It ships as a Python package with a CLI (`rai`), an HTTP harness (FastAPI), a Textual TUI, and a full SDK for embedding.

- **Python**: 3.11+
- **Current version**: see `pyproject.toml`
- **Core framework**: `deepagents==0.5.3` (pinned — do not upgrade without full regression)
- **Agent graph**: LangGraph `CompiledStateGraph` + SQLite checkpointer (`~/.rai/sessions.db`)
- **Model default**: `anthropic:claude-sonnet-4-6`

---

## Repository Layout

```
rai/
├── src/rai/                # Core package (all source lives here)
│   ├── __init__.py         # Top-level exports: create_rai_agent, build_model
│   ├── cli/                # CLI (main.py, serve.py, explorer.py, refs.py)
│   ├── sdk/                # Public SDK surface (agent, builder, client, tui, etc.)
│   ├── engine/             # Agent factory (factory.py) + model builder (model.py)
│   ├── harness/            # FastAPI HTTP server + SSE + HITL + plan routes
│   │   ├── app.py          # AgentPool + FastAPI app wiring
│   │   ├── runner.py       # execute_run() coroutine, _RUN_REGISTRY, _HITL_FUTURES
│   │   ├── sse.py          # RunEventBus + ThreadNotifBus
│   │   ├── models.py       # Pydantic request/response models
│   │   ├── selflearn.py    # Self-learning memory phase after runs
│   │   ├── plan/           # Plan mode tools (write_plan, enter_step, etc.)
│   │   ├── routes/         # FastAPI routers
│   │   │   ├── agents.py       # /agents — registry, compile, model info
│   │   │   ├── runs.py         # /runs — create, stream, cancel, approve
│   │   │   ├── threads.py      # /threads — list, get, state, history, compact, inject
│   │   │   ├── hitl.py         # /threads/{id}/interrupt — HITL interrupt handling
│   │   │   ├── tasks.py        # /tasks — background task tracker
│   │   │   ├── pipelines.py    # /pipelines — multi-agent pipeline execution
│   │   │   ├── notifications.py# /notifications — server push
│   │   │   ├── runtime.py      # /runtime — dynamic agent registration
│   │   │   └── system.py       # /ok — health check
│   │   └── subagents/      # Subagent HTTP execution layer
│   ├── tui/                # Textual TUI
│   │   ├── app.py          # RaiHttpTUIApp (2000 lines — main TUI)
│   │   ├── runner.py       # TUI → server bridge
│   │   ├── themes.py       # 4 themes: rai, github-dark, glass, claude
│   │   ├── screens/        # Modal screens (runs, threads, theme, model, mcp)
│   │   └── widgets/        # 15 custom widgets (see TUI section)
│   ├── middleware/         # 15+ middleware modules (see Middleware Stack)
│   ├── tools/              # 8 tool domains (see Tools section)
│   ├── agents/             # AGENTS.md parser + subagent loader + background runner
│   ├── skills/             # Skills discovery, invocation, CRUD
│   ├── mcp/                # MCP tool loading, session manager, config discovery
│   ├── sessions/           # Thread persistence (store.py — SQLite helpers)
│   ├── config/             # RAISettings singleton + per-agent config.toml
│   ├── data/               # Bundled prompts, skills, OPPLAN templates
│   │   ├── prompts/        # rai/, recon/, researcher/, coder/, sast-analyzer/, agent-creator/
│   │   └── skills/         # skill-creator/SKILL.md
│   ├── client/             # HTTP client for RAI server (_sse.py, _events.py, etc.)
│   ├── defaults/           # Default agent + skill seeding (agents.py, skills.py)
│   ├── hooks/              # Claude Code PreToolUse/PostToolUse hooks
│   └── update/             # Version check + upgrade helper
├── tests/
│   └── test_agentic_loop.py
├── examples/               # 12 runnable SDK examples
├── pyproject.toml
└── README.md
```

---

## Architecture: Agent Creation Pipeline

All agent creation flows through `src/rai/engine/factory.py` → `create_rai_agent()`.

### What `create_rai_agent()` assembles

1. **Model** — via `build_model()` (LiteLLM resolver: prefix `anthropic:`, `openai:`, `google:`, etc.)
2. **System prompt** — resolved in priority order:
   - `~/.rai/agents/<name>/prompt.md` (user override)
   - `src/rai/data/prompts/<name>/prompt.md` (bundled)
   - Body of `~/.rai/agents/<name>/AGENTS.md` (agent creator flow)
   - Slim subagent default (`data/prompts/subagent/prompt.md`)
   - Full RAI prompt (`data/prompts/rai/prompt.md` — 14,500 tokens)
3. **Tools** — loaded per domain (see Tools section)
4. **Middleware stack** — 19 layers applied in fixed order (see Middleware section)
5. **Subagents** — from `AGENTS.md` entries + `~/.rai/agents/<name>/subagents/*.toml`
6. **Backend** — `CompositeBackend(LocalShellBackend, FilesystemBackend)`
7. **Checkpointer** — `AsyncSqliteSaver` → `~/.rai/sessions.db`

### SDK public surface (`src/rai/sdk/__init__.py`)

```python
from rai.sdk import (
    # High-level
    RAIAgent, RAIAgentBuilder, RunableAgent,
    # Serve
    ServeConfig, serve_module,
    # HTTP server
    RAIHTTPServer, HTTPConfig,
    # TUI
    RaiHttpTUI, RaiHttpTUIApp,
    # Client
    RAIClient, ClientConfig,
    # Engine
    create_rai_agent, build_model, ModelConfig, run_agent,
    # Config
    settings, AgentConfig, load_agent_config,
    # Middleware (all classes)
    AuditLogMiddleware, HooksMiddleware, RTKToolMiddleware,
    ExecuteInterceptorMiddleware, RateLimitMiddleware,
    AskUserMiddleware, RAIMemoryMiddleware, SkillsMiddleware,
    LocalContextMiddleware, SummarizationToolMiddleware,
    RAIPromptCachingMiddleware, PlanModeMiddleware, OPPLANMiddleware,
    FindingsEnrichmentMiddleware, MessageCompressionMiddleware,
    # Tools getters
    get_core_tools, get_security_tools, get_web_tools,
    get_cloud_tools, get_ad_tools, get_android_tools,
    get_container_tools, get_reversing_tools,
    # MCP
    load_mcp_tools, load_subagents_mcp_tools_map,
    # Session
    generate_thread_id, get_checkpointer, build_stream_config,
    # Deepagents primitives
    SubAgent, AsyncSubAgent, CompiledSubAgent,
    # SSE event types (20+)
    RunStartEvent, RunEndEvent, TokenEvent, ThinkingEvent,
    ToolStartEvent, ToolEndEvent, InterruptEvent,
    PlanModeEnteredEvent, PlanReadyEvent, StepStartEvent,
    StepCompleteEvent, SubagentStartedEvent,
)
```

---

## Middleware Stack (execution order, outermost → innermost)

| # | Class | File | Role |
|---|-------|------|------|
| 1 | `ConfigurableModelMiddleware` | deepagents-cli | Per-request model override via runtime context |
| 2 | `AuditLogMiddleware` | `middleware/audit.py` | Logs every tool call to `~/.rai/audit.log` (async) |
| 3 | `HooksMiddleware` | `middleware/hooks.py` | Claude Code PreToolUse/PostToolUse hooks |
| 4 | `RTKToolMiddleware` | `middleware/rtk.py` | Rewrites bash commands via `rtk rewrite` |
| 5 | `ExecuteInterceptorMiddleware` | `middleware/execute.py` | Routes `execute` → RAI BashTool |
| 6 | `RateLimitMiddleware` | `middleware/ratelimit.py` | Per-tool delay (profile: aggressive/normal/stealth) |
| 7 | `AskUserMiddleware` | deepagents-cli | Injects `ask_user` tool for HITL questions |
| 8 | `RAIMemoryMiddleware` | `middleware/memory.py` | Inlines ≤4k files, indexes large ones |
| 9 | `SkillsMiddleware` | `middleware/skills.py` (via deepagents-cli) | Loads skills from 6 directories |
| 10 | `LocalContextMiddleware` | deepagents-cli | Security-aware environment detection |
| 11 | `StaticSystemPromptCacheBreakpointMiddleware` | `middleware/cache_split.py` | Splits static/dynamic for Anthropic prompt caching |
| 12 | `FindingsEnrichmentMiddleware` | `middleware/findings.py` | Keeps findings count visible in system prompt |
| 13 | `OPPLANMiddleware` OR `PlanModeMiddleware` | `middleware/opplan.py` / `middleware/plan_mode.py` | OPPLAN (CLI) or HTTP plan mode |
| 14 | `ModelOverrideMiddleware` | `middleware/model_override.py` | Per-call model switching via env var |
| 15 | `MessageCompressionMiddleware` | `middleware/compression.py` | Trims history to ~30k tokens |
| 16 | `SummarizationToolMiddleware` | `middleware/summarization.py` | Auto-compact + manual `/compact` |
| 17 | `RAIPromptCachingMiddleware` | `middleware/prompt_cache.py` | Anthropic prompt caching with LiteLLM support |
| 18 | `ModelCallLoggerMiddleware` | `middleware/model_logger.py` | Debug logging (`RAI_DEBUG_LOG_CALLS=1`) |
| 19 | `EmptyContentSanitizerMiddleware` | `middleware/sanitizer.py` | Strips empty text blocks (Bedrock compatibility) |

**Rule**: When adding a new middleware, insert it at the correct logical position — do not append. Audit and hooks always come before tool execution layers.

---

## Tools

Tool result token limit: `TOOL_RESULT_TOKEN_LIMIT = 8000` (overrides deepagents default of 20000).

### Domain → Getter → Tools

| Domain | Getter | Key tools |
|--------|--------|-----------|
| Core | `get_core_tools()` | `bash`, `findings`, `memory`, `opplan`, `references` |
| Security | `get_security_tools()` | `http_request`, `nuclei_scan`, `nmap_scan`, `web_search`, `web_fetch`, `create_subagent` |
| Web | `get_web_tools()` | `jwt_decode`, `jwt_forge`, `jwt_crack`, `oauth_audit`, `graphql_introspect` |
| Cloud | `get_cloud_tools()` | `aws_cli`, `gcp_cli`, `az_cli`, `kubectl`, `k8s_audit`, `terraform_scan` |
| Container | `get_container_tools()` | `docker_audit`, `docker_escape_check`, `docker_image_scan`, `k8s_pod_escape` |
| Active Directory | `get_ad_tools()` | `bloodhound_collect`, `kerberoast`, `asreproast`, `dcsync`, `adcs_audit`, `ldap_enum` |
| Android | `get_android_tools()` | `apk_info`, `apk_decompile`, `android_manifest_audit`, `adb_shell`, `frida_inject` |
| Reversing | `get_reversing_tools()` | `binary_info`, `strings_extract`, `symbols_extract`, `packer_detect`, `rop_gadgets`, `disassemble` |

### Adding a new tool

1. Create `src/rai/tools/<domain>/my_tool.py` inheriting `BaseTool` (LangChain).
2. Implement `_run()` (sync) and `_arun()` (async) — both required.
3. Export from `src/rai/tools/<domain>/__init__.py` and add to the domain getter in `engine/factory.py`.
4. Add to `sdk/__init__.py` exports if it should be public API.

```python
from langchain.tools import BaseTool

class MyTool(BaseTool):
    name: str = "my_tool"
    description: str = "One-line description for the LLM."

    def _run(self, arg: str) -> str:
        ...

    async def _arun(self, arg: str) -> str:
        ...
```

---

## Config Paths

```
~/.rai/
├── agents/<name>/
│   ├── config.toml          # Model, api_key, base_url, temperature, max_tokens, rate_limit_profile
│   ├── AGENTS.md            # Subagent definitions for this agent (see format below)
│   ├── prompt.md            # System prompt override (takes priority over bundled prompts)
│   ├── mcp.json             # Per-agent MCP servers
│   ├── memory/
│   │   ├── user.md          # What the agent knows about the user
│   │   ├── feedback.md      # Behavioral feedback
│   │   ├── engagement.md    # Current engagement context
│   │   ├── target.md        # Target scope notes
│   │   ├── findings.md      # Persistent findings
│   │   └── methodology.md   # Attack/analysis methodology
│   └── skills/              # Agent-local skills
├── user/
│   ├── MEMORY.md            # User profile index
│   ├── profile.md
│   ├── preferences.md
│   └── context.md
├── targets/<target>/        # Per-target memory
├── skills/                  # Global user skills
├── .mcp.json                # Global MCP servers
├── hooks.json               # Claude Code hooks
├── audit.log                # All tool calls (JSON lines, rotated)
└── sessions.db              # LangGraph SQLite checkpointer
```

### config.toml format

```toml
model = "anthropic:claude-sonnet-4-6"
api_key = "sk-ant-..."       # or "" to use ANTHROPIC_API_KEY env var
base_url = ""                # for custom endpoints / LiteLLM proxies
temperature = 0.7
max_tokens = 8192
rate_limit_profile = "normal"  # aggressive | normal | stealth
```

---

## AGENTS.md Format (for defining subagents)

Both the global `~/.rai/agents/<name>/AGENTS.md` and per-project `AGENTS.md` use the same format.

```markdown
---
name: recon
description: Network reconnaissance and asset discovery specialist
model: anthropic:claude-sonnet-4-6
api_key: inherit
base_url: inherit
---

You are a reconnaissance specialist. Your goal is to map attack surfaces...

---
name: coder
description: Secure code analysis and development assistant
model: inherit
---

You are an expert software engineer with a focus on security...
```

**Frontmatter fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `name` | Yes | Agent identifier, used in CLI and API |
| `description` | Yes | Shown in `rai agents list` |
| `model` | No | LiteLLM model string; `"inherit"` = use parent's model |
| `api_key` | No | `"inherit"` = use parent's api_key |
| `base_url` | No | `"inherit"` = use parent's base_url |
| `skills` | No | List of skill paths to enable |

**Body**: Becomes the system prompt for this subagent. If empty, the bundled `data/prompts/<name>/prompt.md` is used if it exists, otherwise the slim subagent default.

---

## HTTP Server API

Server starts on `http://127.0.0.1:8000` by default (`rai chat` or `rai http serve`).

### Key endpoints

```
GET  /ok                                    Health check
GET  /agents                                List registered agents
GET  /agents/{name}/model                   Agent model info

POST /runs/{agent}                          Create a new run (returns run_id)
GET  /runs/{agent}/{run_id}/stream          SSE stream of run events
POST /runs/{agent}/{run_id}/cancel          Cancel a running run
POST /runs/{agent}/{run_id}/plan/approve    Approve a plan in plan mode

GET  /threads                               List threads
GET  /threads/{id}                          Get thread metadata
GET  /threads/{id}/state                    Current LangGraph state (messages, tasks, etc.)
GET  /threads/{id}/history                  Paginated message history
DELETE /threads/{id}                        Delete thread + all checkpoints
GET  /threads/{id}/compact/status           Token usage estimate
POST /threads/{id}/compact                  Trigger manual compaction
GET  /threads/{id}/summary                  Latest summarization text
POST /threads/{id}/messages                 Inject a HumanMessage (does not start execution)

GET  /threads/{id}/interrupt                Check if HITL interrupt is pending
POST /threads/{id}/interrupt                Submit HITL decision (approve/reject/edit/respond)
GET  /threads/{id}/interrupt/stream         SSE stream of interrupt events for this thread
POST /threads/{id}/ask_user                 Submit ask_user answers

GET  /tasks                                 List background tasks
GET  /tasks/{id}                            Background task status

POST /pipelines                             Launch multi-agent pipeline
```

### SSE event types (from `RunEventBus`)

All events carry `run_id`. Key events:

| Event | Fields | Description |
|-------|--------|-------------|
| `run_start` | `agent`, `thread_id` | Run started |
| `token` | `content` | LLM token stream |
| `thinking` | `content` | Extended thinking block |
| `tool_start` | `tool`, `args`, `tool_call_id` | Tool invocation started |
| `tool_end` | `tool`, `result`, `tool_call_id` | Tool returned |
| `interrupt` | `interrupt_id`, `action_requests` | HITL pause |
| `plan_mode_entered` | — | Agent entered plan mode |
| `plan_ready` | `plan` | Plan written, awaiting approval |
| `step_start` | `step_index`, `step` | Plan step starting |
| `step_complete` | `step_index` | Plan step done |
| `subagent_started` | `subagent`, `task` | Subagent dispatched |
| `run_end` | `status`, `message_count` | Run finished |
| `error` | `detail` | Run errored |

Reconnect with `Last-Event-ID` header — the bus replays up to 500 buffered frames.

---

## TUI (`src/rai/tui/`)

`RaiHttpTUIApp` is fully HTTP-connected — it does **not** inherit from deepagents or run a graph directly.

### Widgets

| Widget | File | Purpose |
|--------|------|---------|
| `MessagesWidget` | `widgets/messages.py` | Chat history + tool cards |
| `ChatInput` | `widgets/chat_input.py` | Input box + slash commands |
| `PlanPanel` | `widgets/plan_panel.py` | Plan display + step approval |
| `HITLPanel` | `widgets/hitl_panel.py` | HITL interrupt UI |
| `SubagentTree` | `widgets/subagent_tree.py` | Active subagent tree |
| `FindingsPanel` | `widgets/findings_panel.py` | Live findings sidebar |
| `StatusBar` | `widgets/status_bar.py` | Model/agent/thread status |
| `BgPanel` | `widgets/bg_panel.py` | Background runs panel |
| `ApprovalWidget` | `widgets/approval.py` | Tool approval cards |
| `AskUserPanel` | `widgets/ask_user_panel.py` | ask_user question UI |
| `WelcomeWidget` | `widgets/welcome.py` | Welcome screen |
| `WizardStep` | `widgets/wizard_step.py` | Setup wizard step |

### Slash commands (from `ChatInput`)

`/new`, `/threads`, `/compact`, `/model`, `/mcp`, `/skills`, `/findings`, `/create-agent`, `/plan`, `/target`, `/memory`, `/clear`, and more.

### Textual markup escaping — CRITICAL

**Always** use the project's `_escape()` helper (full bracket replacement) for any user-supplied or agent-supplied text rendered in Textual widgets. **Never** use `rich.markup.escape()` — it misses edge cases that crash the markup parser.

```python
# Correct
from rai.tui.widgets.messages import _escape
widget.update(_escape(untrusted_text))

# Wrong — do not use
from rich.markup import escape
widget.update(escape(untrusted_text))
```

### Keyboard shortcuts

`ctrl+b` (background runs), `ctrl+n` (new thread), `ctrl+t` (thread browser), `ctrl+p` (plan panel), `ctrl+a` (approve all), `ctrl+f` (findings), `ctrl+m` (model picker), `ctrl+c` (cancel), `escape` (close modal).

---

## CLI Commands

Entry point: `rai` (Typer app in `cli/main.py`)

```
rai chat            Interactive TUI (local server by default; remote via --remote-url)
rai run             Headless single-task execution
rai serve           LangGraph API server (port 2024, hot reload, Studio integration)
rai chat --remote-url <url> --server-key <key>   Connect TUI to an existing remote RAI HTTP server

rai agents list           List all agents
rai agents show <name>    Show agent config + prompt
rai agents config <name>  Edit agent config.toml
rai agents reset <name>   Reset to defaults

rai config show     Show global config
rai config init     Interactive setup wizard

rai mcp add         Add MCP server (global or per-agent)
rai mcp remove      Remove MCP server
rai mcp list        List all MCP servers
rai mcp get         Get server details

rai skills list     List all skills
rai skills create   Create new skill
rai skills add      Add skill from git repo
rai skills delete   Delete skill

rai threads list    List recent threads
rai threads delete  Delete a thread

rai refs            Reference repository management
rai version         Show version
rai update          Check for and install updates
rai http            HTTP client subcommands
```

**Global options**: `--model/-m`, `--agent/-a`, `--target/-t`, `--api-key`, `--base-url`, `--yes/-y`, `--rate-limit`, `--no-mcp`, `--no-rtk`, `--verbose/-v`

---

## Sessions & Thread Persistence

All thread state is persisted via LangGraph's `AsyncSqliteSaver` to `~/.rai/sessions.db`.

Every stream config embeds metadata: `agent_name`, `cwd`, `git_branch`, `updated_at`. These are stored in the `checkpoints.metadata` JSON column and are queryable without deserializing checkpoint blobs.

```python
from rai.sessions.store import build_stream_config, generate_thread_id

thread_id = generate_thread_id()  # UUID7 — time-ordered
config = build_stream_config(thread_id, agent_name="rai", cwd=str(Path.cwd()))
```

Key sync helpers (used from HTTP routes, which run in sync context):
- `list_threads_sync(agent_name, limit, sort_by)` — paginated list
- `get_thread_by_id_sync(thread_id)` — O(1) direct lookup by ID
- `thread_exists_sync(thread_id)` — boolean check
- `delete_thread_sync(thread_id)` — cascades to `writes` table

---

## Plan Mode

Plan mode is a structured multi-step approval gate built into `src/rai/harness/plan/tools.py` and `src/rai/middleware/plan_mode.py`.

### Flow

1. Agent calls `enter_plan_mode()` → state transitions to `plan_mode`
2. Agent calls `write_plan(steps=[...])` → server emits `plan_ready` SSE event
3. TUI shows plan; user approves/rejects/edits
4. HTTP `POST /runs/{agent}/{run_id}/plan/approve` unblocks execution
5. Agent iterates: `enter_step(n)` → do work → `mark_step_done(n)` or `mark_step_blocked(n, reason)`
6. `exit_plan_mode()` → optional self-learning memory phase (runs `selflearn.py`)

Plan tools suppressed during step execution to prevent re-entry: `_PLAN_EXEC_TOOLS = {"enter_plan_mode", "write_plan"}`.

---

## Memory Tool Security

`memory_write` with `content_file` parameter is restricted to safe directories only:

```
/tmp/
$TMPDIR/
~/.rai/
```

Any `content_file` path outside these prefixes returns an error. This prevents path traversal / exfiltration of system files into persistent agent memory. See `src/rai/tools/core/memory.py`.

---

## Skills

Skills are reusable prompt segments that augment agent capability without modifying the system prompt permanently.

### SKILL.md format

```markdown
---
name: web-recon
description: Web reconnaissance skill
allowed_tools:
  - bash
  - http_request
  - web_fetch
---

You have been activated with the web-recon skill. Focus on...
```

### Discovery sources (priority order)

1. Bundled (`src/rai/data/skills/`)
2. Global user (`~/.rai/skills/`)
3. Per-agent (`~/.rai/agents/<name>/skills/`)
4. Project (`./.rai/skills/`)
5. Claude user skills (`~/.claude/skills/`)
6. Claude project skills (`.claude/skills/`)

### Invocation

Prefix in message: `/skill:web-recon` — the middleware injects the skill's prompt segment and restricts tool use to `allowed_tools`.

---

## MCP Integration

MCP servers are loaded per-agent at startup. Config discovery merges:
1. `~/.rai/.mcp.json` (global)
2. `~/.rai/agents/<name>/mcp.json` (per-agent)
3. `./.mcp.json` (project-local)

```json
{
  "mcpServers": {
    "my-server": {
      "command": "npx",
      "args": ["@modelcontextprotocol/server-filesystem"],
      "transport": "stdio",
      "env": {}
    }
  }
}
```

Transports supported: `stdio`, `sse`, `streamable-http`. SSL: auto-retry with `verify=False` for self-signed certs (with warning logged).

---

## Default Agents

Seeded on first run from `src/rai/defaults/agents.py`. Main agent (`rai`) gets config only — always uses the bundled `data/prompts/rai/prompt.md`.

| Agent | Purpose | Default model |
|-------|---------|---------------|
| `rai` | Main security operator | `anthropic:claude-sonnet-4-6` |
| `recon` | Network/asset reconnaissance | inherit |
| `researcher` | OSINT, threat intelligence | inherit |
| `coder` | Secure code analysis + dev | inherit |
| `sast-analyzer` | Static code analysis | inherit |
| `agent-creator` | Creates new agents interactively | inherit |

---

## Environment Variables

| Variable | Effect |
|----------|--------|
| `ANTHROPIC_API_KEY` | Default API key for Anthropic models |
| `OPENAI_API_KEY` | Default API key for OpenAI models |
| `RAI_DEBUG_LOG_CALLS` | `1` → enable `ModelCallLoggerMiddleware` |
| `RAI_AUDIT_LOG` | Override audit log path (default: `~/.rai/audit.log`) |
| `RAI_DB_PATH` | Override sessions DB path (default: `~/.rai/sessions.db`) |
| `RAI_RATE_LIMIT_PROFILE` | `aggressive` / `normal` / `stealth` |

---

## Testing

```bash
pytest tests/
pytest tests/test_agentic_loop.py -v
```

Single test file currently: `tests/test_agentic_loop.py` (core agent loop tests).

When writing new tests:
- Do not mock the database; use a real SQLite checkpointer with a temp path.
- Use `create_rai_agent()` factory — do not construct middleware manually.
- Async tests: `pytest-asyncio` with `@pytest.mark.asyncio`.

---

## Coding Conventions

- **Python 3.11+**: use `from __future__ import annotations`, `X | Y` union types, `TypedDict`, `NotRequired`.
- **No docstrings** on obvious functions. One short line only when the WHY is non-obvious.
- **No comments** narrating what code does — names convey that.
- **No unused abstractions** — add only what the task requires.
- **Async I/O**: any blocking I/O in an async context must use `await asyncio.to_thread(fn, *args)`. Never call `open()`, `subprocess.run()` (blocking), or `time.sleep()` directly in an `async` function.
- **Textual widgets**: always escape user/agent content with `_escape()` (see TUI section).
- **Security**: never introduce new `content_file`-style parameters that read arbitrary paths. All file reads in tools must validate against an allowlist.
- **Commit policy**: never commit unless the user explicitly asks. Never add `Co-Authored-By` or AI attribution to commit messages.

---

## Adding New Features — File Checklists

Every change type has a fixed set of files that must all be updated together. Missing any one causes silent failures (tool not loaded, SDK symbol missing, agent not seeded, etc.).

---

### Adding a new tool

| # | File | What to do |
|---|------|-----------|
| 1 | `src/rai/tools/<domain>/my_tool.py` | Create tool class inheriting `BaseTool`. Implement `_run()` and `_arun()`. |
| 2 | `src/rai/tools/<domain>/__init__.py` | Export the class: `from .my_tool import MyTool` |
| 3 | `src/rai/engine/factory.py` | Add `MyTool()` to the relevant domain getter (e.g. `get_security_tools()`) and include the getter's output in the `tools=[...]` list inside `create_rai_agent()` |
| 4 | `src/rai/sdk/__init__.py` | Export `MyTool` and/or the getter in `__all__` if it should be part of the public SDK surface |
| 5 | `AGENTS.md` (this file) | Add row to the Tools domain table |

**Adding a new domain** (e.g. `iot`): also create `src/rai/tools/iot/__init__.py`, add `get_iot_tools()` getter to `engine/factory.py`, and add the `sdk` export.

---

### Adding a new middleware

| # | File | What to do |
|---|------|-----------|
| 1 | `src/rai/middleware/my_middleware.py` | Create class inheriting deepagents `MiddlewareBase`. Implement needed hooks: `before_model_call`, `after_model_call`, `awrap_tool_call`, `wrap_tool_call`. |
| 2 | `src/rai/middleware/__init__.py` | Export: `from .my_middleware import MyMiddleware` |
| 3 | `src/rai/engine/factory.py` | Insert into the middleware stack at the correct position (see Middleware Stack table). Add import at top. |
| 4 | `src/rai/sdk/__init__.py` | Add to `__all__` so SDK users can import and compose it |
| 5 | `AGENTS.md` (this file) | Add row to the Middleware Stack table with the correct position number |

---

### Adding a new HTTP route / endpoint

| # | File | What to do |
|---|------|-----------|
| 1 | `src/rai/harness/routes/my_route.py` | Create `APIRouter`. Define path functions with `async def`. |
| 2 | `src/rai/harness/models.py` | Add Pydantic `BaseModel` classes for request body and response. |
| 3 | `src/rai/harness/app.py` | `from .routes.my_route import router as my_router` and `app.include_router(my_router)` |
| 4 | `src/rai/client/` | Add client method in the relevant client module (e.g. `threads.py`, `runs.py`) so users can call it via `RAIClient` |
| 5 | `src/rai/sdk/__init__.py` | Export any new client method or model that belongs on the public surface |
| 6 | `AGENTS.md` (this file) | Add endpoint row to the HTTP Server API table |

---

### Adding a new SSE event type

| # | File | What to do |
|---|------|-----------|
| 1 | `src/rai/harness/runner.py` | Call `await bus.publish("my_event", {...})` at the right point in `execute_run()` |
| 2 | `src/rai/harness/sse.py` | No change needed unless adding a new bus type |
| 3 | `src/rai/client/_events.py` | Add a dataclass or TypedDict for the new event, e.g. `MyEvent` |
| 4 | `src/rai/client/_sse.py` | Add a branch in the SSE dispatcher to parse and yield the new event type |
| 5 | `src/rai/sdk/__init__.py` | Export the new event class in `__all__` |
| 6 | `src/rai/tui/app.py` | Add handler `on_my_event()` in `RaiHttpTUIApp` if the TUI needs to react to it |
| 7 | `AGENTS.md` (this file) | Add row to the SSE event types table |

---

### Adding a new default agent (seeded on first run)

| # | File | What to do |
|---|------|-----------|
| 1 | `src/rai/data/prompts/<name>/prompt.md` | Write the bundled system prompt |
| 2 | `src/rai/defaults/agents.py` | Add an entry to the `DEFAULT_AGENTS` list with `name`, `description`, `model` |
| 3 | `~/.rai/agents/<name>/AGENTS.md` | Created automatically on first run — no manual step, but verify seeding logic covers the new name |
| 4 | `AGENTS.md` (this file) | Add row to the Default Agents table |

---

### Adding a new CLI command

| # | File | What to do |
|---|------|-----------|
| 1 | `src/rai/cli/main.py` | Add `@app.command()` function, or create a new `typer.Typer()` sub-app and mount it with `app.add_typer(sub_app, name="...")` |
| 2 | `src/rai/cli/<module>.py` | If the command logic is large, put it in a new module and import lazily inside the command function to keep startup fast |
| 3 | `AGENTS.md` (this file) | Add to the CLI Commands section |

---

### Exposing something to the SDK

Every new public symbol (tool, middleware, event, client method, config class, helper) must be added to **both**:

1. `src/rai/sdk/__init__.py` — add the import and add the name to `__all__`
2. `AGENTS.md` (this file) — document in the relevant section

The SDK `__init__.py` is the single source of truth for what external users can `from rai.sdk import`. If it is not in `__all__`, it is not part of the public API.

---

## Examples

See `examples/` for 12 runnable scripts:

| File | Demonstrates |
|------|-------------|
| `01_pentest_agent.py` | Basic pentest workflow |
| `02_sast_scanner.py` | Code analysis pipeline |
| `03_red_team_parallel.py` | Multi-agent parallel coordination |
| `04_bug_bounty_hunter.py` | Bug bounty automation |
| `05_cloud_audit.py` | Cloud security assessment |
| `06_ctf_solver.py` | CTF challenge solver |
| `07_custom_middleware.py` | Extending middleware |
| `08_minimal_custom.py` | Minimal SDK integration |
| `09_sdk_cookbook.py` | Comprehensive SDK examples |
| `10_threat_modeling.py` | Threat model generation |
| `11_tui_custom_agent.py` | Custom TUI integration |
| `12_tui_multi_agent.py` | Multi-agent TUI setup |

---

## Dependencies (key)

```
deepagents==0.5.3          # Pinned — core agent framework, do NOT upgrade without regression
deepagents-cli==0.0.41     # TUI widgets (AskUserMiddleware, LocalContextMiddleware, etc.)
langchain>=1.2.15
langgraph>=1.1.6
langgraph-checkpoint-sqlite>=3.0.0
langchain-anthropic
langchain-litellm            # Multi-provider support
textual>=8.0.0               # TUI framework
fastapi>=0.115.0             # Required for rai chat / rai serve — included in the base install
uvicorn[standard]>=0.30.0   # Same — included in the base install
httpx
uuid-utils                   # UUID7 thread IDs
```

**Install options:**
- `pip install revolt-rai` — full install including `rai chat` and `rai serve`

Model provider packages are optional extras: `langchain-openai`, `langchain-google-genai`, `langchain-aws`, `langchain-groq`, `langchain-openrouter`, `langchain-ollama`.
