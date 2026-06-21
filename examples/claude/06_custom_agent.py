"""06_custom_agent.py — Build fully custom core agents with their own
system prompts, MCP tools, subagents, hooks, and registry.

This is the primary pattern for building a product on top of the RAI Claude SDK.
Each "core agent" is a ClaudeAgentBuilder spec stored in a ClaudeAgentRegistry.
Claude Code's native tools (Bash, Read, Write, WebFetch, etc.) are always available.
Extra capabilities come from MCP servers you attach.

Demonstrates:
    system_prompt()       — full custom persona / instructions
    with_mcp()            — attach stdio / SSE MCP tool servers
    add_agent()           — define subagents inline (AgentDefinition)
    add_hook()            — custom PreToolUse / PostToolUse hooks
    plan_mode()           — planning pass before execution
    ClaudeAgentRegistry   — register multiple custom core agents
    registry.register()   — store builder specs under a name
    registry.build(name)  — instantiate fresh agent from spec

Usage:
    python examples/claude/06_custom_agent.py
    python examples/claude/06_custom_agent.py --agent webapp-auditor --target https://example.com
    python examples/claude/06_custom_agent.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rai.sdk import (
    AgentDefinition,
    ClaudeAgent,
    ClaudeAgentBuilder,
    ClaudeAgentRegistry,
    HookMatcher,
    ResultMessage,
    iter_tokens,
    iter_tool_calls,
    TaskStartedMessage,
    TaskNotificationMessage,
    TASK_COMPLETED,
    TASK_FAILED,
)

# ---------------------------------------------------------------------------
# 1. Custom system prompts
# ---------------------------------------------------------------------------

_WEBAPP_AUDITOR_PROMPT = """\
You are a specialist web application security auditor.

Your capabilities:
- HTTP request inspection and manipulation (use Bash with curl/httpie)
- JavaScript and source analysis (Read, Bash with grep)
- Authentication flow testing (cookies, JWTs, OAuth)
- OWASP Top 10 methodology

Workflow:
1. Spider the target to map endpoints
2. Test authentication and access control
3. Probe for injection points (SQLi, XSS, SSTI)
4. Verify findings with a PoC before reporting

Always report: severity, CVSS score, affected endpoint, PoC, remediation.
"""

_CLOUD_AUDITOR_PROMPT = """\
You are a cloud infrastructure security specialist.

Your capabilities:
- AWS / GCP / Azure CLI auditing (Bash with cloud CLIs)
- IAM policy analysis
- S3/GCS/Blob misconfiguration detection
- Kubernetes security review

Always check: public exposure, over-privileged roles, unencrypted data,
missing logging, and stale credentials.
Report findings with resource ARN/path, severity, and remediation.
"""

_REPORT_WRITER_PROMPT = """\
You are a professional security report writer.

Given a set of findings in any format (JSON, raw text, tool output),
produce a polished, structured security report:

Structure:
1. Executive Summary
2. Scope and Methodology
3. Findings (Critical → Low, each with: title, severity, description, PoC, remediation)
4. Risk Matrix
5. Recommendations

Write in clear, professional English suitable for a CISO audience.
"""

# ---------------------------------------------------------------------------
# 2. Custom hooks
# ---------------------------------------------------------------------------

async def _log_hook(input_data, tool_use_id, context):
    """PostToolUse: print a one-line log of every tool call."""
    tool = input_data.get("tool_name", "")
    inp = input_data.get("tool_input", {})
    brief = str(inp)[:60].replace("\n", " ")
    print(f"\n  [audit] {tool}({brief})", end="", flush=True)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse"}}


async def _block_destructive_hook(input_data, tool_use_id, context):
    """PreToolUse: block any rm -rf or dd commands."""
    tool = input_data.get("tool_name", "")
    inp  = input_data.get("tool_input", {})
    cmd  = inp.get("command", "") if isinstance(inp, dict) else ""
    if tool == "Bash" and any(p in cmd for p in ("rm -rf", "dd if=", "> /dev/")):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Destructive command blocked by safety hook.",
            }
        }
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}


# ---------------------------------------------------------------------------
# 3. Inline subagent definitions
# ---------------------------------------------------------------------------

_RECON_SUBAGENT = AgentDefinition(
    description="Passive reconnaissance — DNS, WHOIS, certificate transparency",
    prompt=(
        "You are a passive recon specialist. You NEVER send traffic directly to the target. "
        "Use only: DNS lookups (dig, nslookup), WHOIS, crt.sh, Shodan API if configured. "
        "Return a structured JSON with: subdomains, IPs, open_ports, technologies."
    ),
    model=None,          # inherit from parent
    maxTurns=15,
    permissionMode="bypassPermissions",
)

_EXPLOIT_SUBAGENT = AgentDefinition(
    description="PoC exploit generator for confirmed vulnerabilities",
    prompt=(
        "You are a proof-of-concept exploit writer. "
        "Given a confirmed vulnerability (endpoint, parameter, type), write a minimal "
        "curl / Python PoC that demonstrates the issue WITHOUT causing damage. "
        "Never exfiltrate real data. Return: exploit_code, expected_output, risk_note."
    ),
    model=None,
    maxTurns=10,
    permissionMode="bypassPermissions",
)

# ---------------------------------------------------------------------------
# 4. Build the registry
# ---------------------------------------------------------------------------

def build_registry() -> ClaudeAgentRegistry:
    """Construct a fully custom ClaudeAgentRegistry with three core agents."""
    registry = ClaudeAgentRegistry()

    # --- Web application auditor ---
    webapp = (
        ClaudeAgentBuilder()
        .agent_name("webapp-auditor")         # maps to ~/.rai/agents/webapp-auditor/ if it exists
        .system_prompt(_WEBAPP_AUDITOR_PROMPT)  # override with our custom prompt
        .without_hitl()
        .with_partial_messages()
        .add_agent("recon", _RECON_SUBAGENT)
        .add_agent("exploit-gen", _EXPLOIT_SUBAGENT)
        .add_hook("PostToolUse", HookMatcher(hooks=[_log_hook]))
        .add_hook("PreToolUse",  HookMatcher(hooks=[_block_destructive_hook]))
        # Attach an external MCP tool server (uncomment when you have one running):
        # .with_mcp({
        #     "burp-mcp": {"type": "sse", "url": "http://127.0.0.1:8081/sse"},
        # })
    )
    registry.register("webapp-auditor", webapp)

    # --- Cloud infrastructure auditor ---
    cloud = (
        ClaudeAgentBuilder()
        .agent_name("cloud-auditor")
        .system_prompt(_CLOUD_AUDITOR_PROMPT)
        .without_hitl()
        .with_partial_messages()
        .add_hook("PostToolUse", HookMatcher(hooks=[_log_hook]))
        .add_hook("PreToolUse",  HookMatcher(hooks=[_block_destructive_hook]))
        # Attach AWS MCP server if configured:
        # .with_mcp({
        #     "aws-mcp": {
        #         "command": "uvx", "args": ["awslabs.aws-mcp-server@latest"],
        #         "env": {"AWS_REGION": "us-east-1"},
        #     }
        # })
    )
    registry.register("cloud-auditor", cloud)

    # --- Report writer (plan mode first, then write) ---
    reporter = (
        ClaudeAgentBuilder()
        .agent_name("report-writer")
        .system_prompt(_REPORT_WRITER_PROMPT)
        .without_hitl()
        .with_partial_messages()
        .max_turns(20)
    )
    registry.register("report-writer", reporter)

    return registry


# ---------------------------------------------------------------------------
# 5. Runner
# ---------------------------------------------------------------------------

async def stream_response(agent, prompt: str) -> str:
    chunks: list[str] = []
    async for msg in agent.stream(prompt):
        for token in iter_tokens(msg):
            sys.stdout.write(token)
            sys.stdout.flush()
            chunks.append(token)
        for call in iter_tool_calls(msg):
            print(f"\n  [tool] {call['name']}: {str(call['input'])[:60]}")
        if isinstance(msg, TaskStartedMessage):
            print(f"\n  [subagent:start] {msg.description}")
        if isinstance(msg, TaskNotificationMessage):
            status_label = "✓" if msg.status == TASK_COMPLETED else "✗"
            print(f"\n  [subagent:{status_label}] {msg.summary[:80]}")
        if isinstance(msg, ResultMessage):
            break
    print()
    return "".join(chunks)


async def run_audit_pipeline(target: str) -> None:
    """Full pipeline: webapp audit → report generation."""
    registry = build_registry()
    print(f"Agents: {registry.list()}\n")

    # Step 1: Web audit
    print("=" * 60)
    print(f"[webapp-auditor] target={target}")
    print("=" * 60)
    async with registry.build("webapp-auditor") as auditor:
        findings = await stream_response(
            auditor,
            f"Perform a comprehensive web application security assessment of {target}. "
            "Use your recon subagent first, then test all discovered endpoints for "
            "OWASP Top 10 vulnerabilities. Return findings as JSON."
        )
    print(f"Audit cost: ${auditor.last_result.total_cost_usd:.4f}\n")

    # Step 2: Report
    print("=" * 60)
    print("[report-writer] generating report")
    print("=" * 60)
    async with registry.build("report-writer") as reporter:
        await stream_response(
            reporter,
            f"Write a professional penetration test report for target {target} "
            f"based on these findings:\n\n{findings[:3000]}"
        )
    print(f"Report cost: ${reporter.last_result.total_cost_usd:.4f}")


async def run_single(agent_name: str, target: str) -> None:
    registry = build_registry()
    if agent_name not in registry.list():
        print(f"Unknown agent '{agent_name}'. Available: {registry.list()}")
        return
    print(f"[{agent_name}] target={target}\n")
    async with registry.build(agent_name) as agent:
        await stream_response(agent, f"Assess: {target}")
    print(f"\ncost=${agent.last_result.total_cost_usd:.4f}  turns={agent.last_result.num_turns}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Custom core agent registry")
    parser.add_argument("--target", default="http://localhost:8080")
    parser.add_argument("--agent", default=None, help="Run a single agent by name")
    parser.add_argument("--list", action="store_true", help="List registered agents")
    args = parser.parse_args()

    if args.list:
        reg = build_registry()
        print("Custom registered agents:")
        for name in reg.list():
            print(f"  {name}")
    elif args.agent:
        asyncio.run(run_single(args.agent, args.target))
    else:
        asyncio.run(run_audit_pipeline(args.target))
