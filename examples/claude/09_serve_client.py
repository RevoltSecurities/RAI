"""claude/09_serve_client.py — ClaudeServeClient: Python client for rai claude-serve.

Demonstrates every namespace of ClaudeServeClient:
  c.chat      — stream tokens, submit messages, plan mode
  c.sessions  — permission mode, model switch, MCP info, interrupt, delete
  c.history   — list sessions, messages, subagent transcripts
  c.health    — server health check

Start the server in one terminal:
    rai claude-serve --api-key secret123

Then run this file:
    python examples/claude/09_serve_client.py
    python examples/claude/09_serve_client.py --scenario basic
    python examples/claude/09_serve_client.py --scenario hitl
    python examples/claude/09_serve_client.py --scenario plan
    python examples/claude/09_serve_client.py --scenario session_mgmt
    python examples/claude/09_serve_client.py --scenario history
    python examples/claude/09_serve_client.py --list

If you don't have rai claude-serve running, pass --demo to use a stub server.
"""

from __future__ import annotations

import argparse
import asyncio


# ---------------------------------------------------------------------------
# Scenario 1: Basic streaming — token by token
# ---------------------------------------------------------------------------

async def scenario_basic(base_url: str, api_key: str) -> None:
    """Stream tokens from rai claude-serve and print them as they arrive."""
    from rai.sdk import (
        ClaudeServeClient,
        ClaudeTokenEvent,
        ClaudeRunEndEvent,
        ClaudeThinkingEvent,
        ClaudeToolStartEvent,
        ClaudeToolResultEvent,
    )

    async with ClaudeServeClient(base_url, api_key=api_key) as c:
        # Health check
        ok = await c.health.ok()
        print(f"Server healthy: {ok}\n")

        session_id = "demo-basic-01"
        print(f"Streaming response (session={session_id}):\n")

        async for ev in c.chat.stream(
            "List three common web application vulnerabilities and their CVSS scores.",
            session_id=session_id,
        ):
            if isinstance(ev, ClaudeTokenEvent):
                print(ev.text, end="", flush=True)

            elif isinstance(ev, ClaudeThinkingEvent):
                print(f"\n[thinking] {ev.thinking[:80]}…")

            elif isinstance(ev, ClaudeToolStartEvent):
                print(f"\n[tool] {ev.tool_name}({ev.tool_input})")

            elif isinstance(ev, ClaudeToolResultEvent):
                preview = str(ev.result)[:120]
                print(f"       → {preview}")

            elif isinstance(ev, ClaudeRunEndEvent):
                print(f"\n\ncost=${ev.cost:.4f}  turns={ev.turns}")


# ---------------------------------------------------------------------------
# Scenario 2: HITL — approve / deny tool calls programmatically
# ---------------------------------------------------------------------------

async def scenario_hitl(base_url: str, api_key: str) -> None:
    """Handle HITL permission requests: approve safe tools, deny dangerous ones."""
    from rai.sdk import (
        ClaudeServeClient,
        ClaudeTokenEvent,
        ClaudeRunEndEvent,
        ClaudeHITLRequestEvent,
        ClaudeHITLResponseEvent,
    )

    # Tools we consider safe to auto-approve
    _AUTO_APPROVE = {"Read", "Bash", "Glob", "Grep", "WebFetch", "WebSearch"}
    # Tools we never allow without explicit human input
    _NEVER_AUTO = {"Edit", "Write", "NotebookEdit"}

    async with ClaudeServeClient(base_url, api_key=api_key) as c:
        session_id = "demo-hitl-01"

        async for ev in c.chat.stream(
            "Find all Python files in the current directory and summarise what each does.",
            session_id=session_id,
        ):
            if isinstance(ev, ClaudeTokenEvent):
                print(ev.text, end="", flush=True)

            elif isinstance(ev, ClaudeHITLRequestEvent):
                tool = ev.tool_name
                print(f"\n[HITL] Tool requested: {tool}")

                if tool in _NEVER_AUTO:
                    # Deny — agent will receive the denial and adjust
                    await c.sessions.resolve_permission(
                        ev.session_id, ev.tool_use_id, "deny",
                        message=f"{tool} is not permitted in this session.",
                    )
                    print(f"       → DENIED")
                elif tool in _AUTO_APPROVE:
                    await c.sessions.resolve_permission(ev.session_id, ev.tool_use_id, "allow")
                    print(f"       → allowed")
                else:
                    # Unknown tool — prompt the user
                    decision = input(f"Allow {tool}? [y/n]: ").strip().lower()
                    action = "allow" if decision == "y" else "deny"
                    await c.sessions.resolve_permission(ev.session_id, ev.tool_use_id, action)

            elif isinstance(ev, ClaudeHITLResponseEvent):
                print(f"\n[HITL resolved] {ev.tool_name} → {ev.decision}")

            elif isinstance(ev, ClaudeRunEndEvent):
                print(f"\ncost=${ev.cost:.4f}  turns={ev.turns}")


# ---------------------------------------------------------------------------
# Scenario 3: Plan mode — send in plan_mode, then switch and re-send
# ---------------------------------------------------------------------------

async def scenario_plan(base_url: str, api_key: str) -> None:
    """Use plan_mode to get a plan first, then switch to execute it."""
    from rai.sdk import (
        ClaudeServeClient,
        ClaudeTokenEvent,
        ClaudeRunEndEvent,
        ClaudePlanBlockedEvent,
    )

    async with ClaudeServeClient(base_url, api_key=api_key) as c:
        session_id = "demo-plan-01"
        task = "Audit the src/ directory for SQL injection vulnerabilities."

        print("Phase 1: Getting plan (plan_mode=True — tools blocked)…\n")
        plan_text = []

        async for ev in c.chat.stream(task, session_id=session_id, plan_mode=True):
            if isinstance(ev, ClaudeTokenEvent):
                print(ev.text, end="", flush=True)
                plan_text.append(ev.text)
            elif isinstance(ev, ClaudePlanBlockedEvent):
                print(f"\n[plan_blocked] tool={ev.tool_name} — as expected in plan mode")
            elif isinstance(ev, ClaudeRunEndEvent):
                print(f"\ncost=${ev.cost:.4f}\n")

        print("\n--- Plan produced. Switching to execute mode. ---\n")

        # Switch permission mode from 'plan' to 'default' (or 'bypassPermissions')
        await c.sessions.set_permission_mode(session_id, "bypassPermissions")

        print("Phase 2: Executing plan (all tools unlocked)…\n")

        async for ev in c.chat.stream(
            "Now execute the plan you just produced.",
            session_id=session_id,
        ):
            if isinstance(ev, ClaudeTokenEvent):
                print(ev.text, end="", flush=True)
            elif isinstance(ev, ClaudeRunEndEvent):
                print(f"\ncost=${ev.cost:.4f}  turns={ev.turns}")


# ---------------------------------------------------------------------------
# Scenario 4: Session management
# ---------------------------------------------------------------------------

async def scenario_session_mgmt(base_url: str, api_key: str) -> None:
    """Switch models, inspect MCP servers, interrupt, and delete sessions."""
    from rai.sdk import ClaudeServeClient, ClaudeTokenEvent, ClaudeRunEndEvent

    async with ClaudeServeClient(base_url, api_key=api_key) as c:
        session_id = "demo-mgmt-01"

        # Start a short run
        async for ev in c.chat.stream("Say hello in one sentence.", session_id=session_id):
            if isinstance(ev, ClaudeTokenEvent):
                print(ev.text, end="", flush=True)
            elif isinstance(ev, ClaudeRunEndEvent):
                print()

        # Switch the model for the next turn in the same session
        await c.sessions.set_model(session_id, "claude-opus-4-8")
        print(f"\nSwitched session {session_id} to claude-opus-4-8")

        # Inspect MCP servers registered for this session
        mcp = await c.sessions.get_mcp(session_id)
        print(f"MCP servers: {list(mcp.get('servers', {}).keys()) or 'none'}")

        # Inspect context (system prompt, memory, etc.)
        ctx = await c.sessions.get_context(session_id)
        prompt_preview = str(ctx.get("system_prompt", ""))[:120]
        print(f"System prompt preview: {prompt_preview}…")

        # Interrupt a running session (safe to call even if not currently running)
        await c.sessions.interrupt(session_id)
        print(f"Session {session_id} interrupted (no-op if idle)")

        # Delete session when done
        await c.sessions.delete(session_id)
        print(f"Session {session_id} deleted")


# ---------------------------------------------------------------------------
# Scenario 5: History — list sessions and read message transcripts
# ---------------------------------------------------------------------------

async def scenario_history(base_url: str, api_key: str) -> None:
    """List past sessions and read their full message history."""
    from rai.sdk import ClaudeServeClient

    async with ClaudeServeClient(base_url, api_key=api_key) as c:

        # List recent sessions (reads from ~/.claude/)
        sessions = await c.history.list(limit=10)
        print(f"Found {len(sessions)} recent sessions\n")

        if not sessions:
            print("No sessions yet — run another scenario first.")
            return

        # Pick the most recent session
        latest = sessions[0]
        sid = latest.get("session_id", "")
        summary = latest.get("summary", "no summary")[:80]
        print(f"Latest session: {sid}")
        print(f"Summary: {summary}\n")

        # Read messages
        msgs = await c.history.messages(sid, limit=20)
        print(f"Messages in session: {len(msgs)}")
        for msg in msgs[:5]:
            role = msg.get("type", "?")
            content = msg.get("message", {})
            # Extract text preview
            text = ""
            if isinstance(content, dict):
                for block in content.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")[:100]
                        break
            print(f"  [{role}] {text}")

        # List subagents that ran under this session
        subagents = await c.history.subagents(sid)
        agent_ids = subagents.get("subagents", [])
        print(f"\nSubagents used: {len(agent_ids)}")
        for agent_id in agent_ids[:3]:
            sub_msgs = await c.history.subagent_messages(sid, agent_id)
            print(f"  {agent_id[:16]}…  ({len(sub_msgs)} messages)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SCENARIOS = {
    "basic":        ("Basic token streaming", scenario_basic),
    "hitl":         ("HITL auto-approve / deny callbacks", scenario_hitl),
    "plan":         ("Plan mode → switch to execute", scenario_plan),
    "session_mgmt": ("Session management — model switch / interrupt / delete", scenario_session_mgmt),
    "history":      ("History — list sessions, read messages, subagent transcripts", scenario_history),
}


def main() -> None:
    p = argparse.ArgumentParser(description="ClaudeServeClient examples")
    p.add_argument("--scenario", default="basic", choices=list(SCENARIOS))
    p.add_argument("--base-url", default="http://127.0.0.1:8001")
    p.add_argument("--api-key", default="")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    if args.list:
        for key, (desc, _) in SCENARIOS.items():
            print(f"  {key}: {desc}")
        print("\nStart server: rai claude-serve --api-key <key>")
        return

    desc, fn = SCENARIOS[args.scenario]
    print(f"[{args.scenario}] {desc}")
    print(f"Server: {args.base_url}\n")
    asyncio.run(fn(args.base_url, args.api_key))


if __name__ == "__main__":
    main()
