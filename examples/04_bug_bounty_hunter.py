"""04_bug_bounty_hunter.py — Autonomous bug bounty hunting agent.

Demonstrates: get_web_tools(), without_hitl() for full autonomy,
              rate_limit("stealth") to avoid detection, NullBackend pattern.

Usage:
    python examples/04_bug_bounty_hunter.py --scope https://bugcrowd.com/program
    python examples/04_bug_bounty_hunter.py --help
"""

from __future__ import annotations

import argparse
import asyncio

from rai import DEFAULT_MODEL
from rai.sdk import (
    RAIAgentBuilder,
    CompositeBackend,
    FindingsAddTool,
    FindingsListTool,
    FindingsExportTool,
    get_builtin_tools,
    get_security_tools,
    get_web_tools,
    init_findings_store,
)

SYSTEM_PROMPT = """\
You are an expert bug bounty hunter operating within a defined program scope.

Focus areas (OWASP Top 10 + HackerOne priorities):
1. Authentication bypass and session management flaws
2. Authorization issues (IDOR, privilege escalation)
3. Injection (SQLi, XSS, SSTI, SSRF, XXE)
4. Business logic vulnerabilities
5. Information disclosure and sensitive data exposure

Rules:
- Only test endpoints within the defined scope
- No DoS, no data deletion, no lateral movement outside scope
- Document every finding with request/response proof
- Rate limit all requests — be a stealthy hunter
"""


async def run_bug_bounty(scope: str, model: str, api_key: str, base_url: str) -> None:
    findings_store = init_findings_store()
    tools = (
        get_security_tools()
        + get_web_tools()
        + get_builtin_tools()
        + [
            FindingsAddTool(store=findings_store),
            FindingsListTool(store=findings_store),
            FindingsExportTool(store=findings_store),
        ]
    )

    backend = CompositeBackend(backends=[])

    builder = (
        RAIAgentBuilder()
        .model(model)
        .system_prompt(SYSTEM_PROMPT)
        .backend(backend)
        .add_tools(tools)
        .without_hitl()
        .rate_limit("stealth")
    )
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    async with await builder.build() as agent:
        await agent.run(
            f"Hunt for vulnerabilities within this bug bounty scope: {scope}. "
            "Export a findings report when done."
        )
        print(f"\nBug bounty hunt complete. Thread: {agent.thread_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAI Autonomous Bug Bounty Hunter")
    parser.add_argument("--scope", required=True, help="Program scope URL or domain")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    asyncio.run(run_bug_bounty(args.scope, args.model, args.api_key, args.base_url))


if __name__ == "__main__":
    main()
