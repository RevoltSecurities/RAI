"""claude/08_thinking_effort.py — Extended thinking and effort levels.

Demonstrates every thinking and effort knob in the Claude SDK builder:

  with_thinking()     — adaptive extended thinking (Opus 4.6+)
  without_thinking()  — disable thinking (default for non-Opus models)
  effort(level)       — low / medium / high / xhigh / max
  max_turns(n)        — cap the number of agentic turns
  session_id(sid)     — pin to a specific session ID (vs resume() for resuming)

When to use each effort level:
  low    — quick triage, simple yes/no questions, token-budget runs
  medium — standard analysis, most everyday tasks (default)
  high   — deep review, multi-file audit, complex reasoning required
  xhigh  — exhaustive analysis, long-horizon planning, CTF solvers
  max    — maximum reasoning depth, large attack surface, research-grade

Usage:
    python examples/claude/08_thinking_effort.py --scenario thinking
    python examples/claude/08_thinking_effort.py --scenario effort
    python examples/claude/08_thinking_effort.py --scenario max_turns
    python examples/claude/08_thinking_effort.py --list
"""

from __future__ import annotations

import argparse
import asyncio

from rai.sdk import ClaudeAgent


_TASK_THINKING = (
    "Analyse this authentication bypass technique and explain the root cause: "
    "A JWT token uses HS256. The server accepts tokens where alg='none'. "
    "Provide a step-by-step explanation and CVSS score."
)

_TASK_EFFORT = (
    "What are the top three most commonly exploited OWASP Top 10 "
    "vulnerabilities in REST APIs, and why?"
)

_TASK_MAX_TURNS = (
    "Search the current working directory for any Python files, "
    "count how many there are, and tell me the total."
)


# ---------------------------------------------------------------------------
# Scenario 1: with_thinking vs without_thinking
# ---------------------------------------------------------------------------

async def scenario_thinking() -> None:
    """Compare answers with extended thinking enabled vs disabled."""

    print("=== WITHOUT thinking ===")
    async with ClaudeAgent.builder().agent_name("rai").without_hitl().without_thinking().build() as agent:
        result = await agent.run(_TASK_THINKING)
        print(result.result if hasattr(result, "result") else str(result))
        print(f"turns={result.num_turns}  cost=${result.total_cost_usd:.4f}\n")

    print("=== WITH adaptive thinking (Opus 4.6+) ===")
    # Note: with_thinking() requires a model that supports extended thinking.
    # If your default model doesn't support it, override with:
    #   .model("claude-opus-4-8")
    async with ClaudeAgent.builder().agent_name("rai").without_hitl().with_thinking().build() as agent:
        result = await agent.run(_TASK_THINKING)
        print(result.result if hasattr(result, "result") else str(result))
        print(f"turns={result.num_turns}  cost=${result.total_cost_usd:.4f}")


# ---------------------------------------------------------------------------
# Scenario 2: effort() levels
# ---------------------------------------------------------------------------

async def scenario_effort() -> None:
    """Run the same task at three effort levels and compare depth of response."""

    for level in ("low", "medium", "high"):
        print(f"\n=== effort='{level}' ===")
        async with (
            ClaudeAgent.builder()
            .agent_name("rai")
            .without_hitl()
            .effort(level)
            .build()
        ) as agent:
            result = await agent.run(_TASK_EFFORT)
            text = result.result if hasattr(result, "result") else str(result)
            # Print a preview — full response can be very long at high effort
            preview = text[:800] + ("…" if len(text) > 800 else "")
            print(preview)
            print(f"turns={result.num_turns}  cost=${result.total_cost_usd:.4f}")

    # xhigh and max — only use when you need exhaustive depth (cost is significant)
    print("\n=== effort='xhigh' (exhaustive) ===")
    async with ClaudeAgent.builder().agent_name("rai").without_hitl().effort("xhigh").build() as agent:
        result = await agent.run(_TASK_EFFORT)
        text = result.result if hasattr(result, "result") else str(result)
        print(text[:800] + ("…" if len(text) > 800 else ""))
        print(f"turns={result.num_turns}  cost=${result.total_cost_usd:.4f}")


# ---------------------------------------------------------------------------
# Scenario 3: max_turns() + session_id()
# ---------------------------------------------------------------------------

async def scenario_max_turns() -> None:
    """Cap agentic turns and pin to an explicit session ID."""
    import uuid

    # Pin a session_id so you can track this run in ~/.claude/
    # Use .resume(sid) instead to continue a prior session's message history.
    sid = f"rai-demo-{uuid.uuid4().hex[:8]}"
    print(f"session_id: {sid}")

    async with (
        ClaudeAgent.builder()
        .agent_name("rai")
        .without_hitl()
        # Hard cap: agent stops after N turns regardless of task completion.
        # Useful for cost control in autonomous loops.
        .max_turns(3)
        # Pin session — appears in `claude sessions list` with this ID.
        # Does NOT resume a prior session; use .resume() for that.
        .session_id(sid)
        .build()
    ) as agent:
        result = await agent.run(_TASK_MAX_TURNS)
        print(result.result if hasattr(result, "result") else str(result))
        print(f"turns used={result.num_turns}  (max=3)  cost=${result.total_cost_usd:.4f}")
        print(f"session_id: {agent.session_id}")

    # Now resume that same session to continue the conversation
    print("\n--- Resuming session to ask a follow-up ---")
    async with (
        ClaudeAgent.builder()
        .agent_name("rai")
        .without_hitl()
        .max_turns(2)
        .resume(sid)          # resume() restores full prior message history
        .build()
    ) as agent:
        result = await agent.run("Now list only the test files from those results.")
        print(result.result if hasattr(result, "result") else str(result))
        print(f"turns used={result.num_turns}  cost=${result.total_cost_usd:.4f}")


# ---------------------------------------------------------------------------
# Scenario 4: combined — thinking + max effort + max_turns for deep audit
# ---------------------------------------------------------------------------

async def scenario_deep_audit() -> None:
    """Production pattern: Opus + thinking + max effort for critical security reviews."""
    async with (
        ClaudeAgent.builder()
        .agent_name("rai")
        .model("claude-opus-4-8")
        .without_hitl()
        .with_thinking()       # extended reasoning enabled
        .effort("high")        # deep analysis
        .max_turns(20)         # enough turns for a real audit
        .build()
    ) as agent:
        result = await agent.run(
            "Review the authentication flow in src/ for JWT vulnerabilities, "
            "session fixation, and privilege escalation paths. "
            "Report with CWE IDs, severity, and proof-of-concept impact."
        )
        print(result.result if hasattr(result, "result") else str(result))
        print(f"turns={result.num_turns}  cost=${result.total_cost_usd:.4f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SCENARIOS = {
    "thinking":   ("with_thinking() vs without_thinking() comparison", scenario_thinking),
    "effort":     ("effort() level comparison — low / medium / high / xhigh", scenario_effort),
    "max_turns":  ("max_turns() + session_id() vs resume()", scenario_max_turns),
    "deep_audit": ("Combined: Opus + thinking + high effort (expensive)", scenario_deep_audit),
}


def main() -> None:
    p = argparse.ArgumentParser(description="Thinking and effort level examples")
    p.add_argument("--scenario", default="effort", choices=list(SCENARIOS))
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
