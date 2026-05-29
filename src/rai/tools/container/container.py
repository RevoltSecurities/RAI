"""Container security tools — Docker audit, escape checks, image scanning, K8s pod escape."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from rai.tools.core.findings import _add_finding


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


# ── Docker Audit ───────────────────────────────────────────────────────────


class DockerAuditInput(BaseModel):
    container_id: str = Field(
        default="",
        description="Container ID or name to inspect. Empty = list all running containers.",
    )


class DockerAuditTool(BaseTool):
    """Audit Docker container configuration for security misconfigurations."""

    name: str = "docker_audit"
    description: str = (
        "Audit Docker container security configuration: privileged mode, capabilities, "
        "volume mounts, network mode, user context, and security options. "
        "Empty container_id lists all running containers."
    )
    args_schema: ClassVar[type[BaseModel]] = DockerAuditInput

    def _run(self, container_id: str = "") -> str:
        import shutil
        if not shutil.which("docker"):
            return "Error: docker not found."
        if not container_id:
            return _run_cmd(["docker", "ps", "--format", "table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}"])

        raw = _run_cmd(["docker", "inspect", container_id])
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return f"docker inspect output:\n{raw}"

        if not data:
            return f"Container not found: {container_id}"

        cfg = data[0]
        host_cfg = cfg.get("HostConfig", {})
        findings: list[str] = []

        if host_cfg.get("Privileged"):
            findings.append("[CRITICAL] --privileged mode — full host access")
            _add_finding(
                title=f"Docker privileged container: {container_id}",
                description="Container running with --privileged flag — complete host escape possible",
                severity="critical",
                tool="docker_audit",
            )

        caps = host_cfg.get("CapAdd") or []
        dangerous_caps = {"CAP_SYS_ADMIN", "CAP_NET_ADMIN", "CAP_SYS_PTRACE", "CAP_DAC_OVERRIDE", "ALL"}
        for cap in caps:
            if cap in dangerous_caps:
                findings.append(f"[HIGH] Dangerous capability: {cap}")

        mounts = cfg.get("Mounts") or []
        for mount in mounts:
            src = mount.get("Source", "")
            sensitive = ["/", "/etc", "/var/run/docker.sock", "/proc", "/sys"]
            for s in sensitive:
                if src == s or src.startswith(s + "/"):
                    findings.append(f"[HIGH] Sensitive mount: {src} → {mount.get('Destination', '')}")
                    if "docker.sock" in src:
                        _add_finding(
                            title=f"Docker socket mounted in container: {container_id}",
                            description="docker.sock mounted — full Docker daemon access = host escape",
                            severity="critical",
                            tool="docker_audit",
                        )

        network_mode = host_cfg.get("NetworkMode", "")
        if network_mode == "host":
            findings.append("[HIGH] --network=host — shares host network namespace")

        pid_mode = host_cfg.get("PidMode", "")
        if pid_mode == "host":
            findings.append("[HIGH] --pid=host — shares host PID namespace")

        user = cfg.get("Config", {}).get("User", "")
        if not user or user == "root" or user == "0":
            findings.append("[MEDIUM] Running as root (no user specified or user=root)")

        lines = [f"Docker audit: {container_id}\n"]
        if findings:
            lines.extend(findings)
        else:
            lines.append("No critical misconfigurations found.")
        return "\n".join(lines)


# ── Docker Escape Check ────────────────────────────────────────────────────


class DockerEscapeCheckInput(BaseModel):
    pass


class DockerEscapeCheckTool(BaseTool):
    """Check the current container environment for known escape vectors."""

    name: str = "docker_escape_check"
    description: str = (
        "Check the current container environment for escape vectors: "
        "privileged mode, docker.sock mount, hostPID/hostNetwork, "
        "cgroup release_agent (CVE-2022-0492), sensitive host path mounts. "
        "Run from inside a container to assess escape risk."
    )
    args_schema: ClassVar[type[BaseModel]] = DockerEscapeCheckInput

    def _run(self) -> str:
        findings: list[str] = []

        # Check for privileged mode via /proc/self/status capabilities
        try:
            status = Path("/proc/self/status").read_text()
            cap_eff_line = next((l for l in status.splitlines() if l.startswith("CapEff:")), "")
            if cap_eff_line:
                cap_eff = int(cap_eff_line.split()[-1], 16)
                if cap_eff == 0xffffffffffffffff or cap_eff > 0x3ffffffffff:
                    findings.append("[CRITICAL] Full capabilities (likely --privileged)")
                    _add_finding(
                        title="Container escape: privileged mode detected",
                        description="Container has all capabilities — privileged escape likely possible",
                        severity="critical",
                        tool="docker_escape_check",
                    )
        except Exception:
            pass

        # Check for docker.sock mount
        try:
            mounts = Path("/proc/mounts").read_text()
            if "docker.sock" in mounts:
                findings.append("[CRITICAL] /var/run/docker.sock mounted — docker daemon access")
                _add_finding(
                    title="Container escape: docker.sock mounted",
                    description="Docker socket is mounted inside container — escape via docker run --privileged",
                    severity="critical",
                    tool="docker_escape_check",
                )
        except Exception:
            pass

        # Check for cgroup escape (CVE-2022-0492)
        try:
            release_agent = Path("/sys/fs/cgroup/release_agent")
            if release_agent.is_file():
                findings.append("[HIGH] /sys/fs/cgroup/release_agent writable — CVE-2022-0492 candidate")
        except Exception:
            pass

        # Check for hostPID/hostNetwork via /proc namespace
        try:
            init_pid_ns = Path("/proc/1/ns/pid").resolve()
            self_pid_ns = Path("/proc/self/ns/pid").resolve()
            if init_pid_ns == self_pid_ns:
                findings.append("[HIGH] Sharing host PID namespace (hostPID=true)")
        except Exception:
            pass

        # Check sensitive mounts
        try:
            mounts_text = Path("/proc/mounts").read_text()
            for sensitive in ["/etc ", "/proc ", "/sys ", " / "]:
                if sensitive in mounts_text:
                    findings.append(f"[MEDIUM] Potentially sensitive host path mounted: {sensitive.strip()}")
        except Exception:
            pass

        if not findings:
            return "No obvious container escape vectors detected."
        return "Container escape check findings:\n\n" + "\n".join(findings)


# ── Docker Image Scan ──────────────────────────────────────────────────────


class DockerImageScanInput(BaseModel):
    image: str = Field(description="Docker image name/tag to scan, e.g. 'nginx:latest'.")
    severity: str = Field(
        default="HIGH,CRITICAL",
        description="Minimum severity to report: CRITICAL, HIGH, MEDIUM, LOW, UNKNOWN.",
    )
    timeout: int = Field(default=300, description="Timeout in seconds.")


class DockerImageScanTool(BaseTool):
    """Scan a Docker image for CVE vulnerabilities using trivy."""

    name: str = "docker_image_scan"
    description: str = (
        "Scan a Docker image for known CVE vulnerabilities using trivy. "
        "Reports affected packages, CVE IDs, and severity. "
        "Requires trivy: brew install trivy  OR  apt install trivy"
    )
    args_schema: ClassVar[type[BaseModel]] = DockerImageScanInput

    def _run(self, image: str, severity: str = "HIGH,CRITICAL", timeout: int = 300) -> str:
        import shutil
        if not shutil.which("trivy"):
            return "Error: trivy not found. Install: brew install trivy  OR  https://aquasecurity.github.io/trivy"
        cmd = ["trivy", "image", "--severity", severity, "--quiet", image]
        return _run_cmd(cmd, timeout=timeout)


# ── K8s Pod Escape ─────────────────────────────────────────────────────────


class K8sPodEscapeInput(BaseModel):
    namespace: str = Field(default="default", description="Kubernetes namespace to audit.")
    kubeconfig: str = Field(default="", description="Path to kubeconfig file.")
    timeout: int = Field(default=60, description="Timeout in seconds.")


class K8sPodEscapeTool(BaseTool):
    """Find Kubernetes pods with container escape vectors."""

    name: str = "k8s_pod_escape"
    description: str = (
        "Find Kubernetes pods with container escape capabilities: "
        "hostPID, hostNetwork, hostIPC, privileged containers, "
        "automounted service account tokens, and dangerous capabilities."
    )
    args_schema: ClassVar[type[BaseModel]] = K8sPodEscapeInput

    def _run(self, namespace: str = "default", kubeconfig: str = "", timeout: int = 60) -> str:
        import shutil
        if not shutil.which("kubectl"):
            return "Error: kubectl not found."
        ns_args = ["-n", namespace] if namespace != "all" else ["-A"]
        kc_args = ["--kubeconfig", kubeconfig] if kubeconfig else []
        raw = _run_cmd(["kubectl", "get", "pods"] + ns_args + kc_args + ["-o", "json"], timeout=timeout)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return f"kubectl output:\n{raw}"

        findings: list[str] = []
        for item in data.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            name = meta.get("name", "?")
            ns = meta.get("namespace", namespace)
            label = f"{ns}/{name}"
            if spec.get("hostPID"):
                findings.append(f"[HIGH] {label}: hostPID=true")
            if spec.get("hostNetwork"):
                findings.append(f"[HIGH] {label}: hostNetwork=true")
            if spec.get("hostIPC"):
                findings.append(f"[MEDIUM] {label}: hostIPC=true")
            for c in spec.get("containers", []):
                sec = c.get("securityContext", {})
                if sec.get("privileged"):
                    findings.append(f"[CRITICAL] {label}/{c['name']}: privileged=true")
                    _add_finding(
                        title=f"K8s privileged pod: {label}",
                        description=f"Pod {label} container {c['name']} runs privileged",
                        severity="critical",
                        tool="k8s_pod_escape",
                    )
                caps = (sec.get("capabilities") or {}).get("add", [])
                for cap in caps:
                    if cap in ("SYS_ADMIN", "NET_ADMIN", "ALL"):
                        findings.append(f"[HIGH] {label}/{c['name']}: capability {cap}")
            if spec.get("automountServiceAccountToken", True):
                findings.append(f"[LOW] {label}: automountServiceAccountToken=true (default)")

        if not findings:
            return f"No pod escape vectors found in namespace '{namespace}'."
        return f"K8s pod escape check ({namespace}):\n\n" + "\n".join(findings[:50])


# ── Container Runtime Audit ────────────────────────────────────────────────


class ContainerRuntimeAuditInput(BaseModel):
    pass


class ContainerRuntimeAuditTool(BaseTool):
    """Audit the Docker/container runtime security configuration."""

    name: str = "container_runtime_audit"
    description: str = (
        "Audit Docker daemon / container runtime security: "
        "runtime version, seccomp/AppArmor/SELinux status, rootless mode, "
        "logging driver, and network configuration."
    )
    args_schema: ClassVar[type[BaseModel]] = ContainerRuntimeAuditInput

    def _run(self) -> str:
        import shutil
        sections: list[str] = []
        if shutil.which("docker"):
            sections.append(f"[docker info]\n{_run_cmd(['docker', 'info'])[:3000]}")
        if shutil.which("crictl"):
            sections.append(f"[crictl info]\n{_run_cmd(['crictl', 'info'])[:2000]}")
        if not sections:
            return "Error: docker or crictl not found."
        return "\n\n".join(sections)


# ── Docker History ─────────────────────────────────────────────────────────


class DockerHistoryInput(BaseModel):
    image: str = Field(description="Docker image name/tag to inspect, e.g. 'myapp:latest'.")


class DockerHistoryTool(BaseTool):
    """Inspect Docker image layer history for secrets, credentials, or sensitive data."""

    name: str = "docker_history"
    description: str = (
        "Show Docker image layer history to find secrets or credentials "
        "accidentally baked into image layers via ENV, RUN, or COPY commands. "
        "Common issues: hardcoded passwords in ENV, API keys in RUN commands."
    )
    args_schema: ClassVar[type[BaseModel]] = DockerHistoryInput

    def _run(self, image: str) -> str:
        import shutil
        if not shutil.which("docker"):
            return "Error: docker not found."
        raw = _run_cmd(["docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", image])
        lines = raw.splitlines()
        findings: list[str] = []
        sensitive_patterns = [
            r"password\s*=",
            r"passwd\s*=",
            r"secret\s*=",
            r"api_key\s*=",
            r"apikey\s*=",
            r"access_key\s*=",
            r"token\s*=",
            r"auth\s*=",
            r"AWS_SECRET",
            r"PRIVATE_KEY",
        ]
        import re
        for i, line in enumerate(lines):
            for pattern in sensitive_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(f"Layer {i}: potential secret — {line[:200]}")
                    _add_finding(
                        title=f"Docker image layer contains potential secret: {image}",
                        description=f"Layer {i}: {line[:300]}",
                        severity="high",
                        tool="docker_history",
                    )
                    break

        result = f"Docker layer history for {image} ({len(lines)} layers):\n\n"
        result += "\n".join(f"  [{i}] {l[:200]}" for i, l in enumerate(lines[:30]))
        if findings:
            result += "\n\n### Potential Secrets Found\n" + "\n".join(findings)
        return result


# ── Factory ────────────────────────────────────────────────────────────────


def get_container_tools() -> list[BaseTool]:
    return [
        DockerAuditTool(),
        DockerEscapeCheckTool(),
        DockerImageScanTool(),
        K8sPodEscapeTool(),
        ContainerRuntimeAuditTool(),
        DockerHistoryTool(),
    ]
