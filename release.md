# RAI v2.1.0 Release Notes

## What's New

### Subagents v2 — Async Background Agent System

Agents can now spawn, monitor, and coordinate parallel background agents from within a single session. The new `SubagentService` manages full lifecycle including HITL, output discipline, and completion notifications injected back into the parent agent's context.

**10 new LLM tools available to the agent:**

| Tool | Description |
|------|-------------|
| `Subagent` | Launch a single background agent; returns `task_id` immediately |
| `MultiSubagents` | Launch multiple agents in parallel |
| `Pipeline` | DAG of tasks with dependency ordering |
| `SubagentStatus` | Get current status + output of a task |
| `SubagentCancel` | Cancel a running task |
| `SubagentUpdate` | Send a follow-up message to a completed task |
| `SubagentResponse` | Block and wait for task completion |
| `SubagentProgress` | Read live checkpoint of a running task |
| `ListSubagents` | List available subagent types |
| `ListTasks` | List all tasks for the current run |

**Output discipline**: every subagent writes structured output to `/tmp/rai_{task_id}_output.md`. The parent agent receives a ≤400-char preview + file path when the task completes — subagent context never pollutes the main agent's checkpoint.

---

### Claude SDK Mode — No API Key Required

RAI now includes a full Claude Code subscription-backed mode that requires no `ANTHROPIC_API_KEY`.

```bash
rai claude                        # Claude TUI (HITL on by default)
rai claude --no-hitl              # autonomous mode
rai claude --agent pentest        # named agent
rai claude --model claude-opus-4-8
rai claude-serve                  # FastAPI SSE proxy at :8001
rai claude --url http://host:8001 # connect TUI to remote claude-serve
```

The Textual TUI (`rai claude`) streams tokens live with tool call cards, subagent progress, thinking blocks, and inline HITL approval panels.

**Python SDK:**
```python
from rai.sdk import ClaudeAgent

async with ClaudeAgent.builder().agent_name("rai").without_hitl().build() as agent:
    result = await agent.run("scan example.com for vulnerabilities")
```

---

### Remote TUI (`--url`)

Connect the Claude TUI to a remote `rai claude-serve` without spawning a local subprocess:

```bash
rai claude --url http://remote-server:8001 --api-key <key>
```

Health-checks the remote server before launching; exits with a clear error if unreachable.

---

### Docker Container Routing (`--docker`)

All `bash` and `execute` tool calls can be routed to a persistent Docker container instead of the host. Bring any image — Kali, Parrot, or a custom pentest toolkit.

```bash
rai chat --docker kalilinux/kali-rolling
rai chat --docker parrotsec/security
```

A shared workspace is mounted at `/workspace` inside the container. The container is started on server boot and stopped on exit. No default image is provided — users supply their own.

**Container capabilities:** `NET_RAW`, `NET_ADMIN`, `SYS_PTRACE`, `SETUID`, `CHOWN`, `DAC_OVERRIDE`, `NET_BIND_SERVICE`, `KILL`

---

### API Auth Hardening

- **Fixed**: `/docs`, `/openapi.json`, and `/redoc` were bypassing the `X-API-Key` guard on the HTTP harness when `--server-key` was set — an attacker on the network could enumerate all API endpoints and their full schemas without a key. Now only `/ok` (health check) is exempt.
- Both servers (`rai http serve` and `rai claude-serve`) now log a startup warning when launched without an API key.

---

### New Middleware (7 layers added)

| Middleware | Role |
|-----------|------|
| `AllowedToolsMiddleware` | Hard-enforce per-run tool whitelist via `ContextVar` |
| `ReadFileDedupMiddleware` | Stub unchanged file re-reads via mtime check |
| `SubagentRestrictMiddleware` | Block `task` tool for subagents (no recursive nesting) |
| `DockerRouterMiddleware` | Route bash/execute to Docker container when `--docker` is active |
| `RetryMiddleware` | Auto-retry 429/502/503/timeouts with exponential backoff |
| `WriteTruncateMiddleware` | Truncate file-write args in old messages; last 5 writes kept verbatim |
| `CostTrackingMiddleware` | Accumulate per-run cost in `ContextVar` |

---

### Stability Fixes (v2.1.0)

| # | File | Fix |
|---|------|-----|
| 17 | `harness/runner.py` | Remove `<system-info>cwd:</system-info>` injection — caused new-thread no-response |
| 18 | `tui/app.py` | `HistoryDivider` only shown when messages exist |
| 19 | `harness/sse.py` | `RunEventBus.subscribe()` replays all buffered events on first connection |
| 20 | `tools/security/web_search.py` | Multi-engine search via ddgs + SSL verify flag + DDG lite fallback |
| 21 | `tools/security/web_fetch.py` | Chunked reading + markdownify extraction |

---

## Upgrade from v2.0.2

No breaking changes. All existing agent configs, memories, and sessions work as-is.

**New install — Claude SDK mode (no API key needed):**
```bash
pip install revolt-rai   # claude-agent-sdk bundled since v2.0.3
rai claude               # requires `claude` CLI authenticated in PATH
```

**Docker routing:**
```bash
rai chat --docker kalilinux/kali-rolling   # pulls image on first run
```

---

## Install

```bash
pip install revolt-rai          # Python CLI + TUI + HTTP server + Claude SDK
pip install revolt-rai[bedrock] # + AWS Bedrock provider
```

---

# RAI v2.0.2 Release Notes

## What's New

### 6–8× Cost Reduction via Prompt Caching Parity with Claude Code

RAI now sends requests using the Anthropic wire format (`POST /v1/messages`) instead of the OpenAI format (`POST /chat/completions`). This single change unlocks full prompt caching — the same strategy used by Claude Code — saving 60–90% on input token costs for long sessions.

**Before v2.0.2**: $40–60 for a full 6-step VAPT session  
**After v2.0.2**: $5–7 for the same session

#### What changed under the hood

| Change | Impact |
|--------|--------|
| `ChatAnthropic` replaces `ChatLiteLLM` for Claude models | `cache_control` preserved through proxy |
| System prompt (70k chars) cached with `ephemeral` | ~28k tokens saved every turn after first |
| Tool definitions (90 tools) cached | ~35k tokens saved every turn after first |
| Last human message cached | Full history served from cache on next turn |
| Cache TTL removed (was 5 min, now 1h default) | 12× longer cache lifetime |

#### Automatic upgrade

All existing Claude model configs (`litellm:openai/bedrock-claude-*`, `anthropic:claude-*`) are automatically upgraded to `ChatAnthropic` routing at runtime. No config changes required.

To explicitly use the new routing:
```bash
rai agents config-set rai \
  --model "chatanthropic:bedrock-claude-sonnet-4.6-(US)" \
  --api-key "sk-..." \
  --base-url "https://your-litellm-proxy.example.com"
```

---

### Extended Thinking Enabled by Default

RAI now sends `thinking: {type: enabled, budget_tokens: 31999}` on every call — matching Claude Code's behavior. This improves reasoning quality for complex security assessments, reducing mistakes and re-runs.

> ⚠ **Temperature override:** Anthropic requires `temperature=1.0` when extended thinking is enabled. RAI enforces this automatically for all Claude models. Your `config.toml` temperature setting is ignored while thinking is active. Non-Claude models (OpenAI, Gemini, Ollama) are unaffected.

To disable thinking and restore your configured temperature:

```bash
RAI_THINKING=0 rai chat          # per-run
export RAI_THINKING=0            # permanent
```

| Mode | Temperature used | Notes |
|------|-----------------|-------|
| `RAI_THINKING=1` (default) | `1.0` (forced by Anthropic) | Best reasoning quality |
| `RAI_THINKING=0` | Your `config.toml` value (default `0.7`) | Standard mode, lower cost |

---

### MITM Proxy Support for Debugging

Capture every LLM request in Burp Suite or mitmproxy:

```bash
RAI_INSPECT=1 RAI_INSPECT_PROXY=http://127.0.0.1:8080 rai chat
```

Works correctly with macOS system proxies (WARP, VPN) — those are bypassed automatically.

---

## Bug Fixes

- Fixed `StaticSystemPromptCacheBreakpointMiddleware` not tagging `system[0]` due to `_should_apply_caching` returning `False` for `ChatAnthropic` in deepagents
- Fixed `AnthropicPromptCachingMiddleware` stamping `ttl: "5m"` on all cache blocks (now defaults to Anthropic's 1h)
- Fixed `RequestInspectorMiddleware` failing on macOS when WARP/VPN SOCKS proxy is active

---

## Upgrade from v2.0.1

No breaking changes. All existing configs work as-is — Claude models are automatically upgraded to the efficient routing path.

To verify caching is working:
```bash
RAI_DEBUG_LOG_CALLS=1 rai chat
# Check ~/.rai/debug/model-calls.jsonl — look for cache_read_input_tokens > 0 after turn 2
```

---

# RAI v2.0.1 Release Notes

## What's New

### Token Cost Reduction (60–80% on long sessions)

RAI now runs a 3-layer compression pipeline before every model call, keeping costs flat as sessions grow longer:

1. **History trim** — clips conversation to a token budget before the model sees it
2. **Tool result compression** — old bash/grep/file outputs are truncated; recent results stay verbatim
3. **Summarization** — only fires when the first two layers aren't enough

Combined with a configurable cheap model for summarization, a typical VAPT or SAST session now costs significantly less than before.

**Configure a cheaper summarization model:**
```bash
rai agents config-set --compact-model "litellm:openai/bedrock-claude-haiku-4.5-(US)"

# With explicit credentials if different from main
rai agents config-set \
  --compact-model "litellm:openai/bedrock-claude-haiku-4.5-(US)" \
  --compact-api-key "sk-..." \
  --compact-base-url "https://llmproxy.example.com"

# Clear it (inherit main model)
rai agents config-set --compact-model ""
```

Also configurable via `RAI_COMPACT_MODEL` env var.

---

### Loop Detection

The agent no longer gets stuck re-executing the same command. When identical tool calls are detected, RAI returns the cached result with a warning instead of executing again:

```
⚠ DUPLICATE CALL BLOCKED: 'bash' was already executed 5 times and returned:
0 matches for shell_exec|proc_open|...
This result is final. Accept it and proceed to the next step.
```

Configurable: `RAI_LOOP_WINDOW=10` (default 10 recent calls tracked).

---

### Plan Mode Improvements

- Plan completion no longer repeats the full step history in the tool result — agent reads its own notes directly
- `list_plan_steps` returns compact summaries for completed steps, full detail for pending ones
- Memory phase at plan exit now explicitly guides the agent to write target-specific methodology to `scope='target'`

---

### TUI Improvements

- Thread resume now shows the **most recent messages** instead of the oldest
- Internal control messages no longer appear as user messages in history

---

### Model Call Diagnostics

Enable detailed per-call logging to verify token consumption:

```bash
RAI_DEBUG_LOG_CALLS=1 rai chat
# Logs to ~/.rai/debug/model-calls.jsonl
```

Each entry shows: message count, total chars, estimated tokens, truncated count, message type breakdown.

---

## Bug Fixes

- `pip install revolt-rai` now works — previous wheel was empty due to packaging misconfiguration
- Session approval (`approve_for_session`) now persists correctly across tool calls
- Server shuts down cleanly when TUI exits or crashes
- Thread resume loads with the correct agent graph when multiple agents are registered
- Audit log no longer blocks the event loop on busy servers
- `content_file` in `memory_write` now restricted to safe paths only

---

## Upgrade from v2.0.0

No breaking changes. All existing agent configs, memories, and sessions work as-is.

To enable cheaper summarization after upgrading:
```bash
rai agents config-set --compact-model "litellm:openai/bedrock-claude-haiku-4.5-(US)"
```

## Install

```bash
pip install revolt-rai          # Python CLI + TUI + HTTP server
pip install revolt-rai[bedrock] # + AWS Bedrock provider
```
