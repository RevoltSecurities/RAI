"""03_red_team_parallel.py — Red team operation with 3 parallel specialist subagents.

Demonstrates: AsyncSubAgent for parallel execution, subagent coordination,
              operator/subagent result aggregation, rate limiting.

Subagents:
  • recon-agent  — passive + active reconnaissance
  • exploit-agent — vulnerability exploitation (HITL gated)
  • report-agent  — findings aggregation and report generation

Usage:
    python examples/03_red_team_parallel.py --target example.com
    python examples/03_red_team_parallel.py --help
"""

from __future__ import annotations

import argparse
import asyncio

from rai import DEFAULT_MODEL, AsyncSubAgent
from rai.sdk import (
    RAIAgentBuilder,
    FindingsExportTool,
    get_security_tools,
    get_builtin_tools,
    init_findings_store,
)

COORDINATOR_PROMPT = """\
You are a red team coordinator. Orchestrate a three-phase engagement:

1. Delegate reconnaissance to @recon-agent — collect hostnames, open ports, services, tech stack.
2. Once recon completes, delegate exploitation to @exploit-agent with the recon findings.
3. Once exploitation completes, delegate reporting to @report-agent to produce a final PDF/markdown report.

Coordinate the phases sequentially: wait for recon before starting exploitation.
"""

RECON_PROMPT = """\
You are a reconnaissance specialist. Perform passive and active recon:
- DNS enumeration, subdomain discovery
- Port scanning and service fingerprinting
- Web technology detection
- Public exposure analysis (Shodan, certs, GitHub leaks)
Return a structured JSON summary of all discovered assets.
"""

EXPLOIT_PROMPT = """\
You are an exploitation specialist. Given recon results:
- Identify exploitable vulnerabilities (CVEs, misconfigurations, weak auth)
- Attempt proof-of-concept exploits (never destructive, always documented)
- Capture flags or access evidence where possible
Return a structured JSON findings list with severity and proof.
"""

REPORT_PROMPT = """\
You are a security report writer. Given recon + exploitation findings:
- Produce an executive summary
- Write technical findings with CVSS scores, CWE IDs, and remediation
- Format as markdown ready for a PDF renderer
"""


async def run_red_team(target: str, model: str, api_key: str, base_url: str) -> None:
    findings_store = init_findings_store()
    tools = get_security_tools() + get_builtin_tools()

    recon_subagent = AsyncSubAgent(
        name="recon-agent",
        model=model,
        system_prompt=RECON_PROMPT,
        tools=tools,
        auto_approve=True,
    )
    exploit_subagent = AsyncSubAgent(
        name="exploit-agent",
        model=model,
        system_prompt=EXPLOIT_PROMPT,
        tools=tools,
        auto_approve=False,
    )
    report_subagent = AsyncSubAgent(
        name="report-agent",
        model=model,
        system_prompt=REPORT_PROMPT,
        tools=[FindingsExportTool(store=findings_store)],
        auto_approve=True,
    )

    builder = (
        RAIAgentBuilder()
        .model(model)
        .system_prompt(COORDINATOR_PROMPT)
        .target(target)
        .add_subagents([recon_subagent, exploit_subagent, report_subagent])
        .rate_limit("normal")
        .with_hitl()
    )
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    async with await builder.build() as agent:
        await agent.run(f"Begin coordinated red team engagement against: {target}")
        print(f"\nRed team operation complete. Thread: {agent.thread_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAI Red Team — 3 Parallel Subagents")
    parser.add_argument("--target", required=True, help="Target (hostname or URL)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    asyncio.run(run_red_team(args.target, args.model, args.api_key, args.base_url))


if __name__ == "__main__":
    main()
