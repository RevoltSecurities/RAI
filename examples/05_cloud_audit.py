"""05_cloud_audit.py — Multi-cloud CIS benchmark audit with specialist subagents.

Demonstrates: _detect_providers() pattern (reads env vars), parallel cloud subagents,
              get_cloud_tools(), SARIF findings export.

Subagents:
  • aws-cis-agent  — AWS CIS Benchmark v3.0
  • azure-cis-agent — Azure CIS Benchmark v2.0
  • gcp-cis-agent  — GCP CIS Benchmark v3.0

Usage:
    python examples/05_cloud_audit.py
    python examples/05_cloud_audit.py --providers aws azure
    python examples/05_cloud_audit.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import os

from rai import DEFAULT_MODEL, AsyncSubAgent
from rai.sdk import (
    RAIAgentBuilder,
    FindingsExportTool,
    get_cloud_tools,
    init_findings_store,
)

COORDINATOR_PROMPT = """\
You are a cloud security audit coordinator. Orchestrate CIS benchmark assessments
across all detected cloud providers:

1. Identify which providers have active credentials (AWS, Azure, GCP)
2. Launch parallel specialist subagents for each detected provider
3. Aggregate all findings into a unified compliance report
4. Export a SARIF report with provider-tagged findings

For each provider benchmark sub-assessment, ensure 100% control coverage.
"""

AWS_PROMPT = """\
You are an AWS CIS Benchmark v3.0 specialist. Assess all CIS controls:
- IAM (MFA, password policy, access key rotation, root account)
- Logging (CloudTrail, Config, VPC Flow Logs, S3 bucket logging)
- Networking (security groups, default VPC, NACLs)
- Storage (S3 ACLs, encryption, versioning)
- Monitoring (CloudWatch alarms for root, unauthorized API calls)
Report each control as PASS/FAIL/MANUAL with severity and remediation.
"""

AZURE_PROMPT = """\
You are an Azure CIS Benchmark v2.0 specialist. Assess all CIS controls:
- Identity (MFA, guest accounts, privileged identity management)
- Microsoft Defender (enable all plans, security center policies)
- Storage (HTTPS only, TLS version, shared access signatures)
- SQL (auditing, TDE, Advanced Data Security, firewall rules)
- Networking (NSG flow logs, DDoS protection, bastion hosts)
Report each control as PASS/FAIL/MANUAL with severity and remediation.
"""

GCP_PROMPT = """\
You are a GCP CIS Benchmark v3.0 specialist. Assess all CIS controls:
- IAM (service account keys, admin accounts, org policies)
- Logging (audit logs, sink retention, log metric alerts)
- Networking (firewall rules, SSH/RDP access, private Google access)
- VMs (shielded VMs, disk encryption, OS login)
- Storage (uniform bucket-level access, public prevention)
Report each control as PASS/FAIL/MANUAL with severity and remediation.
"""

_PROVIDER_ENV_MARKERS = {
    "aws":   ["AWS_ACCESS_KEY_ID", "AWS_PROFILE", "AWS_DEFAULT_REGION"],
    "azure": ["AZURE_SUBSCRIPTION_ID", "AZURE_CLIENT_ID"],
    "gcp":   ["GOOGLE_APPLICATION_CREDENTIALS", "GCLOUD_PROJECT"],
}


def _detect_providers() -> list[str]:
    """Return providers that have at least one credential env var set."""
    detected = []
    for provider, envs in _PROVIDER_ENV_MARKERS.items():
        if any(os.environ.get(e) for e in envs):
            detected.append(provider)
    return detected or ["aws"]


_PROVIDER_PROMPTS = {"aws": AWS_PROMPT, "azure": AZURE_PROMPT, "gcp": GCP_PROMPT}
_PROVIDER_NAMES   = {"aws": "aws-cis-agent", "azure": "azure-cis-agent", "gcp": "gcp-cis-agent"}


async def run_cloud_audit(providers: list[str], model: str, api_key: str, base_url: str) -> None:
    findings_store = init_findings_store()
    cloud_tools = get_cloud_tools()

    subagents = [
        AsyncSubAgent(
            name=_PROVIDER_NAMES[p],
            model=model,
            system_prompt=_PROVIDER_PROMPTS[p],
            tools=cloud_tools + [FindingsExportTool(store=findings_store)],
            auto_approve=True,
        )
        for p in providers
    ]

    builder = (
        RAIAgentBuilder()
        .model(model)
        .system_prompt(COORDINATOR_PROMPT)
        .add_subagents(subagents)
        .add_tool(FindingsExportTool(store=findings_store))
        .without_hitl()
        .rate_limit("normal")
    )
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    async with await builder.build() as agent:
        await agent.run(
            f"Perform CIS benchmark audit for providers: {', '.join(providers)}. "
            "Export a SARIF report when complete."
        )
        print(f"\nCloud audit complete. Providers: {providers}. Thread: {agent.thread_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RAI Multi-Cloud CIS Audit")
    parser.add_argument("--providers", nargs="+", choices=["aws", "azure", "gcp"],
                        help="Cloud providers to audit (default: auto-detect)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()

    providers = args.providers or _detect_providers()
    asyncio.run(run_cloud_audit(providers, args.model, args.api_key, args.base_url))


if __name__ == "__main__":
    main()
