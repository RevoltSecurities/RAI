"""claude/10_hitl_callback.py — Programmatic HITL via can_use_tool() callback.

The Claude SDK's can_use_tool() lets you intercept every tool call with an
async callback before it executes. This is different from HTTP harness HITL:
  - HTTP harness HITL: SSE event → REST approval call
  - can_use_tool(): in-process async callback, same Python process, no HTTP round-trip

Use cases:
  - Auto-approve safe tools, deny dangerous ones
  - Scope-check: block requests to out-of-scope hosts
  - Budget gate: deny tools once cost exceeds a threshold
  - Audit: log every tool call to a database before allowing it
  - Custom logic: prompt a human in Slack/Discord before proceeding

Requires: .with_hitl() — can_use_tool is never invoked with .without_hitl()
(bypassPermissions skips the permission layer entirely).

Usage:
    python examples/claude/10_hitl_callback.py --scenario auto_approve
    python examples/claude/10_hitl_callback.py --scenario scope_check
    python examples/claude/10_hitl_callback.py --scenario budget_gate
    python examples/claude/10_hitl_callback.py --list
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any


# ---------------------------------------------------------------------------
# Scenario 1: Auto-approve safe tools, deny everything else
# ---------------------------------------------------------------------------

async def scenario_auto_approve() -> None:
    """Allow read-only tools silently; deny write/exec tools automatically."""
    from rai.sdk import ClaudeAgent

    _SAFE = {"Read", "Glob", "Grep", "WebFetch", "WebSearch", "LS"}

    async def _gate(tool_name: str, tool_input: Any, ctx: Any) -> Any:
        """Return an allow or deny PermissionResult."""
        from claude_agent_sdk import PermissionResult  # type: ignore[import]

        if tool_name in _SAFE:
            print(f"  [auto-allow] {tool_name}")
            return PermissionResult(behavior="allow")

        print(f"  [auto-deny]  {tool_name}({list(tool_input.keys()) if isinstance(tool_input, dict) else ''})")
        return PermissionResult(
            behavior="deny",
            message=f"'{tool_name}' is not permitted in this session. Use read-only tools only.",
        )

    async with (
        ClaudeAgent.builder()
        .agent_name("rai")
        .with_hitl()                  # required for can_use_tool to fire
        .can_use_tool(_gate)          # async callback wired here
        .build()
    ) as agent:
        result = await agent.run(
            "List all Python files in the current directory "
            "and show me the first 5 lines of each."
        )
        print(result.result if hasattr(result, "result") else str(result))
        print(f"cost=${result.total_cost_usd:.4f}")


# ---------------------------------------------------------------------------
# Scenario 2: Scope enforcement — block out-of-scope hostnames
# ---------------------------------------------------------------------------

async def scenario_scope_check() -> None:
    """Block HTTP requests to hosts outside the authorised scope."""
    from rai.sdk import ClaudeAgent

    _ALLOWED_SCOPE = {"example.com", "api.example.com", "staging.example.com"}

    def _extract_host(tool_input: Any) -> str | None:
        if not isinstance(tool_input, dict):
            return None
        url = tool_input.get("url", "")
        if not url:
            return None
        from urllib.parse import urlparse
        return urlparse(url).hostname or None

    async def _scope_gate(tool_name: str, tool_input: Any, ctx: Any) -> Any:
        from claude_agent_sdk import PermissionResult  # type: ignore[import]

        if tool_name in ("WebFetch", "Bash"):
            host = _extract_host(tool_input)
            if host and host not in _ALLOWED_SCOPE:
                print(f"  [scope-deny] {tool_name} → {host} (out of scope)")
                return PermissionResult(
                    behavior="deny",
                    message=(
                        f"Host '{host}' is outside the authorised scope. "
                        f"Allowed: {_ALLOWED_SCOPE}"
                    ),
                )
            print(f"  [scope-ok]   {tool_name} → {host}")

        return PermissionResult(behavior="allow")

    async with (
        ClaudeAgent.builder()
        .agent_name("rai")
        .with_hitl()
        .can_use_tool(_scope_gate)
        .build()
    ) as agent:
        result = await agent.run(
            "Fetch the headers from https://example.com and https://evil.com "
            "and compare their security headers."
        )
        print(result.result if hasattr(result, "result") else str(result))
        print(f"cost=${result.total_cost_usd:.4f}")


# ---------------------------------------------------------------------------
# Scenario 3: Budget gate — deny all tools once cost exceeds threshold
# ---------------------------------------------------------------------------

async def scenario_budget_gate() -> None:
    """Deny all tools once cumulative cost crosses $0.05."""
    from rai.sdk import ClaudeAgent

    _MAX_COST_USD = 0.05
    _spend = [0.0]    # mutable closure

    async def _budget_gate(tool_name: str, tool_input: Any, ctx: Any) -> Any:
        from claude_agent_sdk import PermissionResult  # type: ignore[import]

        # ctx.session_cost_usd is available in some SDK versions;
        # fall back to a simple counter approximation
        cost_so_far = getattr(ctx, "session_cost_usd", _spend[0])
        if cost_so_far >= _MAX_COST_USD:
            print(f"  [budget-deny] {tool_name} — ${cost_so_far:.4f} >= ${_MAX_COST_USD}")
            return PermissionResult(
                behavior="deny",
                message=(
                    f"Budget limit ${_MAX_COST_USD} reached (spent ${cost_so_far:.4f}). "
                    "Stop and summarise what you have found so far."
                ),
            )

        print(f"  [budget-ok]  {tool_name} — ${cost_so_far:.4f} so far")
        return PermissionResult(behavior="allow")

    async with (
        ClaudeAgent.builder()
        .agent_name("rai")
        .with_hitl()
        .can_use_tool(_budget_gate)
        .max_turns(10)
        .build()
    ) as agent:
        result = await agent.run(
            "Enumerate all open ports on localhost and identify what service "
            "is running on each one."
        )
        print(result.result if hasattr(result, "result") else str(result))
        print(f"cost=${result.total_cost_usd:.4f}")


# ---------------------------------------------------------------------------
# Scenario 4: Human-in-the-loop via interactive prompt
# ---------------------------------------------------------------------------

async def scenario_interactive() -> None:
    """Prompt the operator in the terminal before allowing any tool call."""
    from rai.sdk import ClaudeAgent

    async def _ask_human(tool_name: str, tool_input: Any, ctx: Any) -> Any:
        from claude_agent_sdk import PermissionResult  # type: ignore[import]

        print(f"\n[HITL] Tool: {tool_name}")
        if isinstance(tool_input, dict):
            for k, v in list(tool_input.items())[:3]:
                print(f"       {k}: {str(v)[:80]}")
        decision = input("Allow? [y/n]: ").strip().lower()

        if decision == "y":
            return PermissionResult(behavior="allow")
        reason = input("Reason for denial (optional): ").strip()
        return PermissionResult(
            behavior="deny",
            message=reason or "Operator denied this tool call.",
        )

    async with (
        ClaudeAgent.builder()
        .agent_name("rai")
        .with_hitl()
        .can_use_tool(_ask_human)
        .build()
    ) as agent:
        result = await agent.run(
            "Find any configuration files in the current directory "
            "and check for hardcoded credentials."
        )
        print(result.result if hasattr(result, "result") else str(result))
        print(f"cost=${result.total_cost_usd:.4f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SCENARIOS = {
    "auto_approve": ("Auto-approve safe tools, deny write/exec", scenario_auto_approve),
    "scope_check":  ("Scope enforcement — block out-of-scope hosts", scenario_scope_check),
    "budget_gate":  ("Budget gate — deny tools after $0.05 spent", scenario_budget_gate),
    "interactive":  ("Human-in-the-loop — terminal prompt per tool call", scenario_interactive),
}


def main() -> None:
    p = argparse.ArgumentParser(description="can_use_tool() HITL callback examples")
    p.add_argument("--scenario", default="auto_approve", choices=list(SCENARIOS))
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    if args.list:
        for key, (desc, _) in SCENARIOS.items():
            print(f"  {key}: {desc}")
        return

    desc, fn = SCENARIOS[args.scenario]
    print(f"[{args.scenario}] {desc}\n")
    asyncio.run(fn())


if __name__ == "__main__":
    main()
