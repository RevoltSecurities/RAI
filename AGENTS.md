# RAI — Developer & Agent Guide

> Canonical reference for all AI agents and developers working on this project.
> Claude Code: `CLAUDE.md` routes here via `@AGENTS.md`.

---

## Project Overview

**revolt-rai** (`rai`) is an open-source AI security operator — autonomous, adaptive, full-spectrum cybersecurity: threat modeling, SAST, pentesting, red team, bug bounty, VAPT, SOC.

- **Python**: 3.11+ | **Current version**: `2.0.1` (see `pyproject.toml`)
- **Core framework**: `deepagents==0.5.3` (pinned — do not upgrade without full regression)
- **Agent graph**: LangGraph `CompiledStateGraph` + SQLite checkpointer (`~/.rai/sessions.db`)
- **Model default**: `anthropic:claude-sonnet-4-6`
- **JS SDK**: `@revolt-rai/js` v1.0.0 at `packages/rai-js/`

---

## Repository Layout

```
rai/
├── src/rai/                    # Python core package
│   ├── __init__.py             # version = 2.0.1, top-level exports
│   ├── cli/                    # CLI (main.py, http_server.py, serve.py, explorer.py, refs.py)
│   ├── sdk/                    # Public Python SDK surface
│   ├── engine/                 # Agent factory (factory.py) + model builder (model.py)
│   ├── harness/                # FastAPI HTTP server + SSE + HITL + plan routes
│   │   ├── app.py              # AgentPool + FastAPI app wiring
│   │   ├── runner.py           # execute_run() coroutine, _RUN_REGISTRY, _HITL_FUTURES
│   │   ├── sse.py              # RunEventBus + ThreadNotifBus
│   │   ├── models.py           # Pydantic request/response models
│   │   ├── selflearn.py        # Self-learning memory phase after runs
│   │   ├── plan/               # Plan mode tools (write_plan, enter_step, etc.)
│   │   └── routes/             # FastAPI routers (agents, runs, threads, hitl, tasks, pipelines)
│   ├── tui/                    # Textual TUI
│   │   ├── app.py              # RaiHttpTUIApp (2000 lines)
│   │   ├── history.py          # Persistent prompt history (~/.rai/history.jsonl)
│   │   ├── themes.py           # 4 themes: rai, github-dark, glass, claude
│   │   ├── screens/            # Modal screens (runs, threads, theme, model, mcp)
│   │   └── widgets/            # 15 custom widgets
│   ├── middleware/             # 19 middleware modules
│   ├── tools/                  # 8 tool domains (core, security, web, cloud, ad, android, container, reversing)
│   ├── agents/                 # AGENTS.md parser + subagent loader + background runner
│   ├── sessions/               # Thread persistence (store.py — SQLite helpers)
│   ├── config/                 # RAISettings singleton + per-agent config.toml
│   └── data/                   # Bundled prompts, skills, OPPLAN templates
│
├── packages/
│   └── rai-js/                 # TypeScript SDK (@revolt-rai/js v1.0.0)
│       ├── src/
│       │   ├── client.ts       # RAIClient — REST + SSE, getHeaders, dynamic auth
│       │   ├── events.ts       # 40+ typed SSE event interfaces
│       │   ├── subagents.ts    # SubagentManager class
│       │   ├── useRAIStream.ts # React hook (useReducer-based, no stale closures)
│       │   └── index.ts        # Public exports
│       ├── examples/
│       │   ├── chatbox/        # Full React chat app (Vite + Tailwind)
│       │   └── patterns/       # 10 pattern files comparing RAI vs LangGraph SDK
│       ├── package.json        # @revolt-rai/js, version 1.0.0
│       └── README.md           # 848-line production docs
│
├── static/                     # Static assets (rai-logo.png) — copied into Docker
├── docker/
│   └── Dockerfile              # Multi-stage build, non-root rai user, port 8000
├── tests/
│   └── test_agentic_loop.py
├── examples/                   # 12 Python SDK examples
├── .github/workflows/
│   ├── publish-pypi.yml        # PyPI publish on GitHub Release
│   ├── publish-npm.yml         # npm publish on GitHub Release / v* tag
│   └── publish-docker-image.yml# GHCR publish on Release / tag / main push
├── pyproject.toml              # revolt-rai 2.0.1, hatchling build
├── AGENTS.md                   # This file
├── CLAUDE.md                   # Routes to @AGENTS.md
└── release.md                  # v2.0.0 release notes
```

---

## Critical Bug Fixes Applied (v2.0.1)

All fixes are committed and verified. **Do not revert.**

### Original 9 critical fixes

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

**Build fix**: `pyproject.toml` — `src/rai/**` added to sdist `include`. The previous PyPI wheel was empty (just metadata). `pip install revolt-rai` now works.

### Post-v2.0.1 token optimisation fixes

| # | File | Fix | Impact |
|---|------|-----|--------|
| 10 | `middleware/compression.py` | `_char_estimate()` now counts tool_call args — was only counting content, letting 120k chars bypass the 75k budget | Critical (caused 390-msg sessions at 119k chars) |
| 11 | `engine/factory.py` | `TOOL_RESULT_TOKEN_LIMIT` 8000→4000 tokens — reduces LangGraph checkpoint bloat | High |
| 12 | `engine/factory.py` | Summarization uses `cfg.compact_model` (cheap model) instead of main model | High ($2-5 saved per session) |
| 13 | `harness/plan/tools.py` | `exit_plan_mode` result stripped full step digest (~5,700 chars saved per plan session) | Medium |
| 14 | `harness/plan/tools.py` | `list_plan_steps` compact for done steps — strips description/how_to, keeps notes[:150] | Medium |
| 15 | `tui/app.py` + `client/threads.py` | `history_tail()` — TUI now loads last 50 msgs not first 50 | UX |
| 16 | `tui/app.py` | Filter `<system-reminder>` human messages from history display | UX |

---

## Middleware Stack (outermost → innermost, 22 layers)

| # | Class | File | Role |
|---|-------|------|------|
| 1 | `ConfigurableModelMiddleware` | deepagents-cli | Per-request model override via runtime context |
| 2 | `AuditLogMiddleware` | `middleware/audit.py` | Async tool call logging to `~/.rai/audit.log` |
| 3 | `HooksMiddleware` | `middleware/hooks.py` | Claude Code PreToolUse/PostToolUse hooks |
| 3.5 | `LoopDetectionMiddleware` | `middleware/loop_detection.py` | **NEW** Blocks duplicate tool calls — returns cached result with ⚠ warning when agent re-issues identical bash/grep/read. Stops degenerate 50× loops under context pressure |
| 4 | `RTKToolMiddleware` | `middleware/rtk.py` | Rewrites bash commands via `rtk rewrite` |
| 5 | `ExecuteInterceptorMiddleware` | `middleware/execute.py` | Routes execute → RAI BashTool |
| 6 | `RateLimitMiddleware` | `middleware/ratelimit.py` | Per-tool delays (profile: aggressive/normal/stealth) |
| 7 | `AskUserMiddleware` | deepagents-cli | Injects ask_user tool |
| 8 | `RAIMemoryMiddleware` | `middleware/memory.py` | Inlines ≤4k files, indexes large ones |
| 9 | `SkillsMiddleware` | deepagents-cli | Loads skills from 6 dirs |
| 10 | `LocalContextMiddleware` | deepagents-cli | Security-aware env detection |
| 11 | `StaticSystemPromptCacheBreakpointMiddleware` | `middleware/cache_split.py` | Splits static/dynamic for Anthropic prompt caching |
| 12 | `FindingsEnrichmentMiddleware` | `middleware/findings.py` | Findings count in system prompt |
| 13 | `OPPLANMiddleware` OR `PlanModeMiddleware` | `middleware/opplan.py` / `plan_mode.py` | OPPLAN (CLI) or HTTP plan mode |
| 14 | `ModelOverrideMiddleware` | `middleware/model_override.py` | Per-call model switching via env var |
| 15 | `MessageCompressionMiddleware` | `middleware/compression.py` | **Layer 1 compression** — trim all history to ≤30k tokens. Zero LLM cost |
| 15.7 | `ToolResultCompressionMiddleware` | `middleware/tool_compaction.py` | **Layer 2 compression** — truncate old bash/file/http results (>500 chars → 1500 max) + old bash args (>600 chars). Never touches: HumanMessage, plan tools, findings, memory, ask_user, last 20 messages. Saves 60-80% tokens on long sessions |
| 16 | `SummarizationToolMiddleware` | `middleware/summarization.py` | **Layer 3 compression** — LLM summarization at 100k tokens or 40 messages. Exposes `/compact` command |
| 17 | `RAIPromptCachingMiddleware` | `middleware/prompt_cache.py` | Anthropic prompt caching (also works via LiteLLM proxy) |
| 18 | `ModelCallLoggerMiddleware` | `middleware/model_logger.py` | Debug logging when `RAI_DEBUG_LOG_CALLS=1` — logs effective_count, total_chars, estimated_tokens, truncated_count |
| 19 | `EmptyContentSanitizerMiddleware` | `middleware/sanitizer.py` | Strips empty text blocks (Bedrock compatibility) |

---

## Tools (8 domains)

| Domain | Getter | Key tools |
|--------|--------|-----------|
| Core | `get_core_tools()` | `bash`, `findings`, `memory`, `opplan`, `references` |
| Security | `get_security_tools()` | `http_request`, `nuclei_scan`, `nmap_scan`, `web_search`, `create_subagent` |
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
GET  /agents                                List registered agents

POST /agents/{name}/runs                    Create run (returns run_id)
GET  /agents/{name}/runs/{id}/stream        SSE stream of run events
POST /agents/{name}/runs/{id}/cancel        Cancel run
POST /agents/{name}/runs/{id}/plan/approve  Approve plan
POST /agents/{name}/runs/{id}/plan/reject   Reject plan

GET  /threads                               List threads
GET  /threads/{id}/history                  Paginated message history (offset+limit)
DELETE /threads/{id}                        Delete thread
POST /threads/{id}/interrupt                Submit HITL decision
POST /threads/{id}/ask_user                 Submit ask_user answers

GET  /tasks                                 Background tasks
POST /subagents/{id}/interrupt              Subagent HITL
```

### SSE events (40+ total, key ones)

`run_start` → `token` → `thinking` → `tool_start` → `tool_end` → `interrupt` →
`session_approved` → `ask_user_request` → `plan_mode_entered` → `plan_ready` →
`step_start` → `step_complete` → `plan_completed` → `subagent_started` →
`subagent_token` → `subagent_completed` → `run_end`

---

## Config Paths

```
~/.rai/
├── agents/{name}/
│   ├── config.toml          # model, api_key, base_url, temperature, max_tokens
│   ├── AGENTS.md            # Subagent definitions (YAML frontmatter + system prompt)
│   ├── prompt.md            # System prompt override (highest priority)
│   └── memory/              # user.md, feedback.md, engagement.md, findings.md
├── .mcp.json                # Global MCP servers
├── audit.log                # All tool calls (JSON lines)
├── history.jsonl            # TUI prompt history (1000 entries max)
└── sessions.db              # LangGraph SQLite checkpointer
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

## JS SDK — @revolt-rai/js

Located at `packages/rai-js/`. Published separately as `@revolt-rai/js v1.0.0`.

### Architecture

- `useRAIStream` — `useReducer`-based React hook (no `useSyncExternalStore`, no stale closures)
- `RAIClient` — fetch-based REST + SSE client with `getHeaders` async resolver
- `SubagentManager` — stable class instance, signals React via `SUBAGENT_TICK` action
- `dispatchRef` / `processEventRef` / `stateRef` — refs ensure async SSE loop always has latest functions

### Key methods

```ts
stream.submit(text, opts?)           // start run
stream.stop()                        // abort SSE + cancel server run
stream.disconnect()                  // abort SSE only — server keeps running
stream.joinStream(runId, lastId?)    // reconnect to existing run
stream.switchThread(id | null)       // switch thread (auto-loads history)
stream.approveInterrupt()            // HITL approve
stream.approveInterruptForSession()  // HITL approve for session
stream.rejectInterrupt(msg?)
stream.editInterrupt({ name, args })
stream.respondToInterrupt(msg)
stream.approvePlan()
stream.rejectPlan(feedback?)
stream.answerAskUser(answers)
```

### Dynamic auth (getHeaders)

```ts
useRAIStream({
  getHeaders: async () => ({
    Authorization: `Bearer ${await auth.getToken()}`,
    "X-MFA-Token": sessionStorage.getItem("mfa"),
    "X-Org-Id": store.org.id,
  }),
});
```

Called before EVERY request including SSE reconnects, HITL decisions, plan approvals.

### submit() options

```ts
stream.submit(text, {
  agent: "recon",
  model: "anthropic:claude-opus-4-8",
  planMode: true,
  allowedTools: ["bash", "read_file"],
  maxTurns: 30,
  metadata: { user_id, org_id },
  config: { target_scope: "*.example.com" },
});
```

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
4. `packages/rai-js/src/events.ts` — TypeScript interface + add to RAIEvent union
5. `packages/rai-js/src/useRAIStream.ts` — handle in `processEventRef.current` switch
6. `src/rai/sdk/__init__.py` — export Python class
7. `packages/rai-js/src/index.ts` — export TS type
8. `AGENTS.md` — add to SSE events list

### New JS SDK feature

1. `packages/rai-js/src/` — implement
2. `packages/rai-js/src/index.ts` — export
3. `packages/rai-js/README.md` — document
4. `packages/rai-js/examples/patterns/` — add example file
5. `AGENTS.md` — update JS SDK section

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
- **JS SDK**: all state in `useReducer` — never `useSyncExternalStore` with external mutable store
- **JS async loops**: use `dispatchRef.current()` and `processEventRef.current()` — never capture functions directly in SSE loop closures

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RAI_DEBUG_LOG_CALLS` | `0` | Set to `1` to enable `ModelCallLoggerMiddleware` → `~/.rai/debug/model-calls.jsonl` |
| `RAI_DEBUG_LOG_FILE` | `~/.rai/debug/model-calls.jsonl` | Override debug log path |
| `RAI_COMPACT_MSG_TRIGGER` | `40` | Message count that fires `SummarizationMiddleware` |
| `RAI_COMPACT_TOKEN_TRIGGER` | `100000` | Token count that fires `SummarizationMiddleware` |
| `RAI_COMPACT_KEEP` | `20` | Messages kept after summarization compaction |
| `RAI_COMPACT_TRUNCATE_AT` | `30` | Depth at which old tool call args are truncated by summarizer |
| `RAI_COMPACT_TRUNCATE_MAX` | `2000` | Max chars per tool arg after summarizer truncation |
| `RAI_COMPACT_RESULT_KEEP` | `20` | `ToolResultCompressionMiddleware`: last N messages never touched |
| `RAI_COMPACT_RESULT_MAX` | `1500` | `ToolResultCompressionMiddleware`: max chars for old tool results |
| `RAI_COMPACT_CMD_MAX` | `600` | `ToolResultCompressionMiddleware`: max chars for old bash command args |
| `RAI_COMPACT_FINDINGS_ARG_MAX` | `250` | `ToolResultCompressionMiddleware`: max chars for old findings_add description arg |
| `RAI_LOOP_WINDOW` | `10` | `LoopDetectionMiddleware`: number of recent tool calls to track for dedup |
| `RAI_LOOP_DISABLED` | `0` | Set to `1` to disable loop detection (debugging only) |
| `RAI_COMPACT_MODEL` | — | Cheaper model for summarization (e.g. `litellm:openai/bedrock-claude-haiku-4.5-(US)`). Also set via `rai agents config-set --compact-model` |
| `RAI_INSPECT` | `0` | Set to `1` to enable `RequestInspectorMiddleware` — logs full request to `~/.rai/debug/requests.jsonl` |
| `RAI_INSPECT_PROXY` | — | MITM proxy for request inspection (e.g. `http://127.0.0.1:8080` for Burp/mitmproxy). Requires `RAI_INSPECT=1` |
| `RAI_INSPECT_LOG_FILE` | `~/.rai/debug/requests.jsonl` | Override request inspector log path |
| `RAI_MODEL_OVERRIDE` | — | Override model for all calls (`provider:model` format) |
| `RAI_RATE_LIMIT_PROFILE` | `normal` | `aggressive` / `normal` / `stealth` |
| `LITELLM_API_KEY` | — | API key for LiteLLM proxy (set by `factory.py` from `config.toml`) |
| `LITELLM_BASE_URL` | — | Base URL for LiteLLM proxy |
| `OPENAI_API_KEY` | — | OpenAI / LiteLLM proxy key (set by `factory.py`) |
| `OPENAI_BASE_URL` | — | OpenAI / LiteLLM proxy URL (set by `factory.py`) |

---

## Release

```
# Python (revolt-rai on PyPI)
git tag v2.0.1
git push origin main && git push origin v2.0.1
# → publish-pypi.yml triggers on GitHub Release

# JS (@revolt-rai/js on npm)
# Same tag triggers publish-npm.yml
# Requires NPM_TOKEN secret

# Docker (GHCR)
# publish-docker-image.yml triggers on release / v* tag / main push
```

---

## Install

```bash
pip install revolt-rai                    # Python CLI + TUI + HTTP server
pip install revolt-rai[bedrock]           # + AWS Bedrock provider
npm install @revolt-rai/js                # JS/TS SDK
```

> No `[http]` extra — `fastapi` and `uvicorn` are core deps since v2.0.0.
