"""02_sast_scanner.py — Static application security testing agent.

Demonstrates: FilesystemBackend, without_shell(), SAST security tools,
              FindingsAddTool for inline tracking, SARIF export.

Usage:
    python examples/02_sast_scanner.py --path /path/to/codebase
    python examples/02_sast_scanner.py --path . --model anthropic:claude-sonnet-4-6
    python examples/02_sast_scanner.py --help
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from rai import DEFAULT_MODEL
from rai.sdk import (
    RAIAgentBuilder,
    FilesystemBackend,
    FindingsAddTool,
    FindingsListTool,
    get_security_tools,
    init_findings_store,
)

SYSTEM_PROMPT = """\
You are an expert application security engineer performing a static code analysis.

Scan the codebase for:
- Injection flaws (SQL, command, LDAP, XPath)
- Authentication and session management weaknesses
- Insecure direct object references
- Sensitive data exposure (hardcoded secrets, weak crypto)
- Broken access controls
- Security misconfigurations
- Vulnerable dependencies

For each finding: severity (Critical/High/Medium/Low), CWE ID, file:line, and remediation advice.
Export a SARIF report when done.
"""


async def run_sast(path: str, model: str, api_key: str, base_url: str) -> None:
    cwd = Path(path).resolve()
    findings_store = init_findings_store()

    builder = (
        RAIAgentBuilder()
        .model(model)
        .system_prompt(SYSTEM_PROMPT)
        .cwd(cwd)
        .backend(FilesystemBackend(root=cwd))
        .without_shell()
        .add_tools(get_security_tools() + [
            FindingsAddTool(store=findings_store),
            FindingsListTool(store=findings_store),
        ])
        .without_hitl()
    )
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    async with await builder.build() as agent:
        await agent.run(f"Perform a comprehensive SAST scan of the codebase at {cwd}. Export SARIF when done.")
        print(f"\nSAST scan complete. Thread: {agent.thread_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAI SAST Scanner")
    parser.add_argument("--path", default=".", help="Path to codebase root")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    asyncio.run(run_sast(args.path, args.model, args.api_key, args.base_url))


if __name__ == "__main__":
    main()
