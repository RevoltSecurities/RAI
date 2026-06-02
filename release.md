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
