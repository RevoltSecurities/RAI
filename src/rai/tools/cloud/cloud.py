"""Cloud security tools — AWS, GCP, Azure, Kubernetes, Terraform."""

from __future__ import annotations

import json
import subprocess
from typing import ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


def _run_cmd(cmd: list[str], timeout: int = 60) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as exc:
        return f"Error: {exc}"


# ── AWS CLI ────────────────────────────────────────────────────────────────


class AWSCLIInput(BaseModel):
    command: str = Field(description="AWS CLI subcommand, e.g. 'iam list-users' or 's3 ls'.")
    profile: str = Field(default="", description="AWS CLI profile name (--profile).")
    region: str = Field(default="", description="AWS region (--region).")
    output: str = Field(default="json", description="Output format: json, text, table.")
    timeout: int = Field(default=60, description="Timeout in seconds.")


class AWSCLITool(BaseTool):
    """Run AWS CLI commands for cloud enumeration and security testing."""

    name: str = "aws_cli"
    description: str = (
        "Run AWS CLI commands for cloud security enumeration. "
        "Examples: 'iam list-users', 's3 ls', 'ec2 describe-instances', "
        "'iam get-account-summary', 'sts get-caller-identity'. "
        "Requires AWS credentials to be configured."
    )
    args_schema: ClassVar[type[BaseModel]] = AWSCLIInput

    def _run(self, command: str, profile: str = "", region: str = "", output: str = "json", timeout: int = 60) -> str:
        import shutil
        if not shutil.which("aws"):
            return "Error: aws CLI not found. Install: brew install awscli / pip install awscli"
        cmd = ["aws"] + command.split()
        if profile:
            cmd.extend(["--profile", profile])
        if region:
            cmd.extend(["--region", region])
        cmd.extend(["--output", output])
        return _run_cmd(cmd, timeout=timeout)


# ── AWS IMDS ───────────────────────────────────────────────────────────────


class AWSIMDSInput(BaseModel):
    path: str = Field(
        default="/latest/meta-data/",
        description="IMDS path to fetch, e.g. '/latest/meta-data/iam/security-credentials/'.",
    )
    imdsv2: bool = Field(default=True, description="Use IMDSv2 (token-based). Set false for IMDSv1.")
    timeout: int = Field(default=10, description="Request timeout in seconds.")


class AWSIMDSTool(BaseTool):
    """Fetch AWS EC2 Instance Metadata Service (IMDS) endpoints."""

    name: str = "aws_imds"
    description: str = (
        "Fetch AWS EC2 Instance Metadata Service (IMDS) data. "
        "Useful for SSRF exploitation and testing metadata exposure. "
        "Common paths: /latest/meta-data/iam/security-credentials/, "
        "/latest/user-data, /latest/meta-data/hostname. "
        "IMDSv2 is used by default (sends PUT first to get token)."
    )
    args_schema: ClassVar[type[BaseModel]] = AWSIMDSInput

    def _run(self, path: str = "/latest/meta-data/", imdsv2: bool = True, timeout: int = 10) -> str:
        import httpx

        base = "http://169.254.169.254"
        url = base + path
        headers: dict[str, str] = {}

        if imdsv2:
            try:
                token_resp = httpx.put(
                    f"{base}/latest/api/token",
                    headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
                    timeout=timeout,
                )
                if token_resp.status_code == 200:
                    headers["X-aws-ec2-metadata-token"] = token_resp.text
                else:
                    return f"IMDSv2 token request failed (HTTP {token_resp.status_code}). Try imdsv2=false."
            except Exception as exc:
                return f"IMDSv2 token request error: {exc}"

        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
            return f"HTTP {resp.status_code} {url}\n\n{resp.text}"
        except Exception as exc:
            return f"IMDS request error: {exc}"


# ── kubectl ────────────────────────────────────────────────────────────────


class KubectlInput(BaseModel):
    command: str = Field(description="kubectl subcommand, e.g. 'get pods -A' or 'auth can-i --list'.")
    namespace: str = Field(default="", description="Kubernetes namespace (-n). Empty = all namespaces.")
    kubeconfig: str = Field(default="", description="Path to kubeconfig file.")
    timeout: int = Field(default=60, description="Timeout in seconds.")


class KubectlTool(BaseTool):
    """Run kubectl commands for Kubernetes security enumeration."""

    name: str = "kubectl"
    description: str = (
        "Run kubectl commands for Kubernetes security enumeration. "
        "Examples: 'get pods -A', 'get secrets -A', 'auth can-i --list', "
        "'get clusterrolebindings', 'describe pod <name>'. "
        "Requires kubectl and a valid kubeconfig."
    )
    args_schema: ClassVar[type[BaseModel]] = KubectlInput

    def _run(self, command: str, namespace: str = "", kubeconfig: str = "", timeout: int = 60) -> str:
        import shutil
        if not shutil.which("kubectl"):
            return "Error: kubectl not found. Install: brew install kubectl"
        cmd = ["kubectl"] + command.split()
        if namespace and "-n" not in cmd and "--namespace" not in cmd:
            cmd.extend(["-n", namespace])
        if kubeconfig:
            cmd.extend(["--kubeconfig", kubeconfig])
        return _run_cmd(cmd, timeout=timeout)


# ── K8s Audit ──────────────────────────────────────────────────────────────


class K8sAuditInput(BaseModel):
    kubeconfig: str = Field(default="", description="Path to kubeconfig file.")
    timeout: int = Field(default=120, description="Timeout per command in seconds.")


class K8sAuditTool(BaseTool):
    """Run an automated Kubernetes RBAC + security configuration audit."""

    name: str = "k8s_audit"
    description: str = (
        "Automated Kubernetes security audit: checks RBAC misconfigurations, "
        "privileged pods, service account token automounting, cluster-admin bindings, "
        "and namespace-level permission mismatches."
    )
    args_schema: ClassVar[type[BaseModel]] = K8sAuditInput

    def _run(self, kubeconfig: str = "", timeout: int = 120) -> str:
        import shutil
        if not shutil.which("kubectl"):
            return "Error: kubectl not found."
        extra = ["--kubeconfig", kubeconfig] if kubeconfig else []
        sections: list[str] = []

        for label, cmd_args in [
            ("Cluster-admin bindings", ["get", "clusterrolebindings", "-o", "json"]),
            ("Privileged pods (all ns)", ["get", "pods", "-A", "-o", "json"]),
            ("Service accounts with automountToken", ["get", "serviceaccounts", "-A", "-o", "json"]),
            ("Secrets (all ns)", ["get", "secrets", "-A"]),
            ("Permissions (can-i list)", ["auth", "can-i", "--list"]),
        ]:
            out = _run_cmd(["kubectl"] + cmd_args + extra, timeout=timeout)
            sections.append(f"### {label}\n{out[:2000]}")

        return "\n\n".join(sections)


# ── K8s Secrets Dump ───────────────────────────────────────────────────────


class K8sSecretsDumpInput(BaseModel):
    namespace: str = Field(default="", description="Namespace to dump secrets from. Empty = all namespaces.")
    kubeconfig: str = Field(default="", description="Path to kubeconfig file.")


class K8sSecretsDumpTool(BaseTool):
    """Enumerate and decode Kubernetes secrets across namespaces."""

    name: str = "k8s_secrets_dump"
    description: str = (
        "Enumerate and decode Kubernetes secrets. "
        "Lists all secrets and decodes base64-encoded values for review. "
        "Requires kubectl and appropriate RBAC permissions."
    )
    args_schema: ClassVar[type[BaseModel]] = K8sSecretsDumpInput

    def _run(self, namespace: str = "", kubeconfig: str = "") -> str:
        import base64
        import shutil
        if not shutil.which("kubectl"):
            return "Error: kubectl not found."
        ns_args = ["-n", namespace] if namespace else ["-A"]
        kc_args = ["--kubeconfig", kubeconfig] if kubeconfig else []
        raw = _run_cmd(["kubectl", "get", "secrets"] + ns_args + kc_args + ["-o", "json"], timeout=60)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return f"kubectl output:\n{raw}"
        items = data.get("items", [])
        lines = [f"Kubernetes secrets ({len(items)} total):\n"]
        for item in items[:50]:
            meta = item.get("metadata", {})
            ns = meta.get("namespace", "")
            name = meta.get("name", "")
            stype = item.get("type", "")
            lines.append(f"  {ns}/{name} [{stype}]")
            for k, v in (item.get("data") or {}).items():
                try:
                    decoded = base64.b64decode(v).decode("utf-8", errors="replace")[:200]
                except Exception:
                    decoded = "(binary)"
                lines.append(f"    {k}: {decoded}")
        if len(items) > 50:
            lines.append(f"  ... {len(items) - 50} more secrets")
        return "\n".join(lines)


# ── Terraform Scan ─────────────────────────────────────────────────────────


class TerraformScanInput(BaseModel):
    plan_path: str = Field(description="Path to terraform plan file, directory, or tfstate file.")
    timeout: int = Field(default=120, description="Timeout in seconds.")


class TerraformScanTool(BaseTool):
    """Scan Terraform plans/state for security misconfigurations using checkov."""

    name: str = "terraform_scan"
    description: str = (
        "Scan Terraform plan or state files for security misconfigurations. "
        "Uses checkov (preferred) or tfsec if available. "
        "Checks for: public S3 buckets, unencrypted storage, open security groups, "
        "missing MFA, overly permissive IAM roles."
    )
    args_schema: ClassVar[type[BaseModel]] = TerraformScanInput

    def _run(self, plan_path: str, timeout: int = 120) -> str:
        import shutil
        if shutil.which("checkov"):
            return _run_cmd(["checkov", "-d", plan_path, "--quiet"], timeout=timeout)
        if shutil.which("tfsec"):
            return _run_cmd(["tfsec", plan_path], timeout=timeout)
        return (
            "Error: neither checkov nor tfsec found.\n"
            "Install: pip install checkov  OR  brew install tfsec"
        )


# ── GCP CLI ────────────────────────────────────────────────────────────────


class GCPCLIInput(BaseModel):
    command: str = Field(description="gcloud subcommand, e.g. 'projects list' or 'iam service-accounts list'.")
    project: str = Field(default="", description="GCP project ID (--project).")
    timeout: int = Field(default=60, description="Timeout in seconds.")


class GCPCLITool(BaseTool):
    """Run gcloud CLI commands for GCP security enumeration."""

    name: str = "gcp_cli"
    description: str = (
        "Run gcloud CLI commands for GCP security enumeration. "
        "Examples: 'projects list', 'iam service-accounts list', "
        "'compute instances list', 'storage buckets list'. "
        "Requires gcloud to be installed and authenticated."
    )
    args_schema: ClassVar[type[BaseModel]] = GCPCLIInput

    def _run(self, command: str, project: str = "", timeout: int = 60) -> str:
        import shutil
        if not shutil.which("gcloud"):
            return "Error: gcloud not found. Install: https://cloud.google.com/sdk/docs/install"
        cmd = ["gcloud"] + command.split()
        if project:
            cmd.extend(["--project", project])
        cmd.extend(["--format", "json"])
        return _run_cmd(cmd, timeout=timeout)


# ── Azure CLI ──────────────────────────────────────────────────────────────


class AzureCLIInput(BaseModel):
    command: str = Field(description="az subcommand, e.g. 'account list' or 'role assignment list'.")
    timeout: int = Field(default=60, description="Timeout in seconds.")


class AzureCLITool(BaseTool):
    """Run Azure CLI commands for Azure security enumeration."""

    name: str = "az_cli"
    description: str = (
        "Run Azure CLI (az) commands for Azure security enumeration. "
        "Examples: 'account list', 'role assignment list', "
        "'storage account list', 'keyvault list'. "
        "Requires az CLI to be installed and logged in."
    )
    args_schema: ClassVar[type[BaseModel]] = AzureCLIInput

    def _run(self, command: str, timeout: int = 60) -> str:
        import shutil
        if not shutil.which("az"):
            return "Error: az CLI not found. Install: https://docs.microsoft.com/cli/azure/install-azure-cli"
        cmd = ["az"] + command.split() + ["--output", "json"]
        return _run_cmd(cmd, timeout=timeout)


# ── Factory ────────────────────────────────────────────────────────────────


def get_cloud_tools() -> list[BaseTool]:
    return [
        AWSCLITool(),
        AWSIMDSTool(),
        KubectlTool(),
        K8sAuditTool(),
        K8sSecretsDumpTool(),
        TerraformScanTool(),
        GCPCLITool(),
        AzureCLITool(),
    ]
