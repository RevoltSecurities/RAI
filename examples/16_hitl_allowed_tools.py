"""16_hitl_allowed_tools.py — HITL tool restriction with allowed_tools().

Demonstrates:
  - allowed_tools()   — restrict which tools need HITL approval vs run silently
  - with_hitl()       — enable interactive approval mode (default in CLI)
  - without_hitl()    — full autonomy, no approval prompts (CI / automation)
  - AllowedToolsMiddleware gate — how tool restriction wires into the harness

Scenarios covered:
  A. Restricted pentest — all tools require approval except safe read-only ones
  B. CI mode           — no HITL, only whitelisted tools, everything else blocked
  C. Stealth mode      — only passive tools allowed, no noisy scanners

Usage:
    python examples/16_hitl_allowed_tools.py --scenario A --task "audit login.example.com"
    python examples/16_hitl_allowed_tools.py --scenario B --task "SAST scan of src/"
    python examples/16_hitl_allowed_tools.py --scenario C --task "passive recon example.com"
    python examples/16_hitl_allowed_tools.py --list
"""

from __future__ import annotations

import argparse
import asyncio

from rai.sdk import (
    RAIAgent,
    RAIAgentBuilder,
    get_security_tools,
    get_builtin_tools,
    get_web_tools,
    DEFAULT_MODEL,
)


# ---------------------------------------------------------------------------
# Scenario A — Interactive pentest: all dangerous tools gated behind HITL
# ---------------------------------------------------------------------------
# Tools that require approval: everything not in the list below.
# Tools that run silently (no HITL prompt): safe read-only / passive ones.
_SAFE_TOOLS = [
    "read_file",         # static file reads
    "glob",              # file listing
    "grep",              # text search
    "http_request",      # passive HTTP recon (headers, status)
    "web_search",        # OSINT
    "web_fetch",         # page fetching
    "dns_lookup",        # passive DNS
    "whois",             # OSINT
    "findings_list",     # read findings store
    "findings_export",   # export SARIF
]


async def scenario_a(task: str) -> None:
    """A: Interactive pentest — destructive tools (bash, nuclei, nmap) need HITL approval."""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("pentest-hitl")
        # Enable interactive HITL — CLI will prompt before dangerous tool calls
        .with_hitl()
        # Tools NOT in this list will trigger a HITL approval prompt.
        # Tools IN this list run silently without interrupting the user.
        .allowed_tools(_SAFE_TOOLS)
        .add_tools(get_security_tools() + get_builtin_tools())
        .system_prompt(
            "You are a penetration tester. Perform a targeted security assessment.\n"
            "Passive recon tools run automatically. For any active tool (nmap, nuclei, "
            "exploit scripts, bash commands) the user will be prompted for approval."
        )
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)
        print(f"thread_id: {agent.thread_id}")


# ---------------------------------------------------------------------------
# Scenario B — CI mode: no HITL, only a strict whitelist of tools allowed
# ---------------------------------------------------------------------------
# In CI you want zero interrupts but also zero risk of accidental exploitation.
# Pass allowed_tools to the HTTP harness AllowedToolsMiddleware; any tool
# outside this list returns an error result to the agent without executing.
_CI_TOOLS = [
    "read_file",
    "glob",
    "grep",
    "findings_add",
    "findings_list",
    "findings_export",
]


async def scenario_b(task: str) -> None:
    """B: CI/headless mode — no HITL, only whitelisted SAST tools allowed."""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("sast-ci")
        # No approval prompts — fully autonomous
        .without_hitl()
        # Hard gate: only tools in _CI_TOOLS will execute.
        # Any call to bash, http_request, nuclei etc. returns a blocked error.
        .allowed_tools(_CI_TOOLS)
        .add_tools(get_builtin_tools())
        .without_subagents()
        .without_memory()
        .system_prompt(
            "You are a SAST scanner running in CI. Analyse source files for "
            "vulnerabilities using read_file, glob, and grep. Record each finding "
            "with findings_add and export a SARIF report with findings_export. "
            "Do NOT run any shell commands or network requests."
        )
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


# ---------------------------------------------------------------------------
# Scenario C — Stealth OSINT: only passive tools, noisy scanners blocked
# ---------------------------------------------------------------------------
_PASSIVE_TOOLS = [
    "http_request",
    "web_search",
    "web_fetch",
    "dns_lookup",
    "whois",
    "certificate_search",
    "findings_add",
    "findings_list",
    "findings_export",
]


async def scenario_c(task: str) -> None:
    """C: Passive recon only — nuclei, nmap, bash all blocked by allowed_tools."""
    async with await (
        RAIAgent.builder()
        .model(DEFAULT_MODEL)
        .agent_name("recon-passive")
        .without_hitl()
        # Only passive tools allowed — noisy / active tools return a blocked error.
        .allowed_tools(_PASSIVE_TOOLS)
        .add_tools(get_security_tools() + get_web_tools())
        .without_subagents()
        .system_prompt(
            "You are an OSINT analyst. Use ONLY passive tools: DNS, WHOIS, "
            "certificate transparency, public HTTP headers, and web searches. "
            "Never run port scans or vulnerability scanners. "
            "Compile a passive attack-surface map."
        )
        .build()
    ) as agent:
        result = await agent.run(task)
        print(result)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SCENARIOS = {
    "A": ("Interactive pentest — HITL gate on destructive tools", scenario_a,
          "Audit the login endpoint at https://login.example.com"),
    "B": ("CI SAST — no HITL, only read/grep/findings tools", scenario_b,
          "Find SQL injection and hardcoded secrets in src/"),
    "C": ("Stealth OSINT — passive tools only, no scanners", scenario_c,
          "Passive recon on example.com"),
}


def main() -> None:
    p = argparse.ArgumentParser(description="HITL + allowed_tools examples")
    p.add_argument("--scenario", default="A", choices=list(SCENARIOS),
                   help="Scenario to run (A/B/C)")
    p.add_argument("--task", default="", help="Override task string")
    p.add_argument("--list", action="store_true", help="List scenarios and exit")
    args = p.parse_args()

    if args.list:
        for key, (desc, _, default_task) in SCENARIOS.items():
            print(f"  {key}: {desc}")
            print(f"       default task: {default_task}")
        return

    desc, fn, default_task = SCENARIOS[args.scenario]
    task = args.task or default_task
    print(f"[{args.scenario}] {desc}")
    print(f"Task: {task}\n")
    asyncio.run(fn(task))


if __name__ == "__main__":
    main()
