"""Findings store and findings tools for RAI agents.

Shared in-process findings store plus tools: findings_add, findings_list, findings_export.
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Disk-persistent findings store
# ~/.rai/findings/<session_id>/findings.jsonl
# ---------------------------------------------------------------------------

_findings_lock = threading.Lock()
_findings_cache: list[dict[str, Any]] = []   # write-through in-memory cache
_findings_file: Path | None = None           # set by init_findings_store()


def init_findings_store(session_id: str) -> None:
    """Bind the store to a session directory and load any existing findings.

    Called once at TUI startup (on_mount) with the LangGraph thread ID so that
    resuming a session automatically restores findings from the previous run.

    Args:
        session_id: LangGraph thread ID — used as the directory name so that
                    resuming a thread reloads its findings automatically.
    """
    global _findings_file  # noqa: PLW0603
    store_dir = Path.home() / ".rai" / "findings" / session_id
    store_dir.mkdir(parents=True, exist_ok=True)
    fpath = store_dir / "findings.jsonl"
    loaded: list[dict[str, Any]] = []
    if fpath.exists():
        for raw in fpath.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                try:
                    loaded.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
    with _findings_lock:
        _findings_file = fpath
        _findings_cache.clear()
        _findings_cache.extend(loaded)


def _get_findings() -> list[dict[str, Any]]:
    with _findings_lock:
        return list(_findings_cache)


def _add_finding(finding: dict[str, Any]) -> None:
    with _findings_lock:
        _findings_cache.append(finding)
        if _findings_file is not None:
            try:
                with _findings_file.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(finding, ensure_ascii=False) + "\n")
            except OSError:
                pass  # disk failure must not crash the tool


def _clear_findings() -> None:
    with _findings_lock:
        _findings_cache.clear()
        if _findings_file is not None and _findings_file.exists():
            try:
                _findings_file.write_text("", encoding="utf-8")
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Findings tools
# ---------------------------------------------------------------------------

_EXPORT_SPILL_THRESHOLD = 10_000   # chars; larger exports spill to /tmp
_EXPORT_SPILL_PREVIEW_CHARS = 500


class FindingsAddInput(BaseModel):
    title: str = Field(description="Short title for the finding")
    severity: str = Field(description="Severity level: critical, high, medium, low, info")
    description: str = Field(description="Detailed description of the finding")
    location: str = Field(default="", description="Affected URL, file, or component")
    cve: str = Field(default="", description="CVE identifier if applicable")
    cwe: str = Field(default="", description="CWE identifier if applicable")
    evidence: str = Field(default="", description="Proof of concept, request/response, or log snippet")
    remediation: str = Field(default="", description="Suggested remediation steps")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization (e.g. ['sqli', 'auth'])")
    cvss: str = Field(default="", description="CVSS score (e.g. '8.2')")
    cvss_vector: str = Field(default="", description="CVSS vector string (e.g. 'CVSS:3.1/AV:N/AC:L/...')")
    owasp: str = Field(default="", description="OWASP category (e.g. 'OWASP A03:2021 Injection')")
    target: str = Field(default="", description="Target name or base URL under test")
    endpoint: str = Field(default="", description="Specific endpoint (e.g. 'GET /api/users/{id}')")
    parameter: str = Field(default="", description="Vulnerable parameter name")
    payload: str = Field(default="", description="Exploit payload used")
    response: str = Field(default="", description="Relevant server response snippet")
    reproduction: str = Field(default="", description="Step-by-step reproduction instructions")
    impact: str = Field(default="", description="Business or technical impact description")
    chain: str = Field(default="", description="Attack chain or kill-chain stage")
    references: str = Field(default="", description="Reference URLs (comma-separated)")
    session: str = Field(default="", description="Session date (auto-set to today if empty)")


class FindingsAddTool(BaseTool):
    """Add a security finding to the session findings store."""

    name: str = "findings_add"
    description: str = (
        "Record a security finding. Use this whenever you discover a vulnerability, "
        "misconfiguration, or security issue. Findings are tracked in the session store "
        "and can be exported as a report."
    )
    args_schema: ClassVar[type[BaseModel]] = FindingsAddInput

    def _run(
        self,
        title: str,
        severity: str,
        description: str,
        location: str = "",
        cve: str = "",
        cwe: str = "",
        evidence: str = "",
        remediation: str = "",
        tags: list[str] | None = None,
        cvss: str = "",
        cvss_vector: str = "",
        owasp: str = "",
        target: str = "",
        endpoint: str = "",
        parameter: str = "",
        payload: str = "",
        response: str = "",
        reproduction: str = "",
        impact: str = "",
        chain: str = "",
        references: str = "",
        session: str = "",
    ) -> str:
        finding = {
            "title": title,
            "severity": severity.lower(),
            "description": description,
            "location": location,
            "cve": cve,
            "cwe": cwe,
            "evidence": evidence,
            "remediation": remediation,
            "tags": tags or [],
            "cvss": cvss,
            "cvss_vector": cvss_vector,
            "owasp": owasp,
            "target": target,
            "endpoint": endpoint,
            "parameter": parameter,
            "payload": payload,
            "response": response,
            "reproduction": reproduction,
            "impact": impact,
            "chain": chain,
            "references": references,
            "session": session or datetime.now(UTC).strftime("%Y-%m-%d"),
            "source": "agent",
        }
        _add_finding(finding)
        count = len(_get_findings())
        return f"Finding recorded. Total findings in session: {count}"


class FindingsListTool(BaseTool):
    """List all findings recorded in this session."""

    name: str = "findings_list"
    description: str = "List all security findings recorded in the current session."
    args_schema: ClassVar[type[BaseModel]] = BaseModel

    def _run(self, **_kwargs: Any) -> str:
        findings = _get_findings()
        if not findings:
            return "No findings recorded yet."

        lines = [f"# Findings ({len(findings)} total)\n"]
        for i, f in enumerate(findings, 1):
            sev = f.get("severity", "?").upper()
            title = f.get("title") or f.get("name") or f.get("template") or "Untitled"
            loc = f.get("location") or f.get("url") or ""
            lines.append(f"{i}. [{sev}] {title}")
            if loc:
                lines.append(f"   Location: {loc}")
            desc = f.get("description", "")
            if desc:
                preview = desc[:200] + ("..." if len(desc) > 200 else "")
                lines.append(f"   {preview}")
        return "\n".join(lines)


class FindingsExportInput(BaseModel):
    format: str = Field(
        default="markdown",
        description="Export format: 'markdown', 'json', 'sarif', 'csv', 'hackerone', 'bugcrowd'",
    )
    output: str = Field(default="", description="File path to write to. Empty = return as string.")
    include_evidence: bool = Field(default=True, description="Include evidence blocks in markdown output.")


class FindingsExportTool(BaseTool):
    """Export all findings as a formatted report."""

    name: str = "findings_export"
    description: str = (
        "Export all findings as a markdown report or JSON. "
        "Optionally write to a file path."
    )
    args_schema: ClassVar[type[BaseModel]] = FindingsExportInput

    def _run(self, format: str = "markdown", output: str = "", include_evidence: bool = True) -> str:
        findings = _get_findings()
        if not findings:
            return "No findings to export."

        if format == "json":
            content = json.dumps(findings, indent=2)
        elif format == "sarif":
            content = _render_sarif_report(findings)
        elif format == "csv":
            content = _render_csv_report(findings)
        elif format == "hackerone":
            content = _render_hackerone_report(findings)
        elif format == "bugcrowd":
            content = _render_bugcrowd_csv(findings)
        else:
            content = _render_markdown_report(findings, include_evidence=include_evidence)

        if output:
            try:
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).write_text(content, encoding="utf-8")
                return f"Exported {len(findings)} finding(s) to {output}"
            except OSError as e:
                return f"Failed to write to {output}: {e}\n\n{content}"

        if len(content) > _EXPORT_SPILL_THRESHOLD:
            _EXT = {"json": ".json", "sarif": ".json", "csv": ".csv", "hackerone": ".md", "bugcrowd": ".csv"}
            suffix = _EXT.get(format, ".md")
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=suffix, prefix="rai_findings_", delete=False, encoding="utf-8",
            ) as tf:
                tf.write(content)
                spill_path = tf.name
            size_kb = len(content) / 1024
            preview = content[:_EXPORT_SPILL_PREVIEW_CHARS]
            return (
                f"Export too large to return inline ({len(content):,} chars, {size_kb:.1f} KB). "
                f"Written to: {spill_path}\n\n"
                f"Preview (first {_EXPORT_SPILL_PREVIEW_CHARS} chars):\n{preview}"
            )

        return content


def _render_markdown_report(findings: list[dict[str, Any]], include_evidence: bool = True) -> str:
    SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "unknown": 5}
    sorted_findings = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.get("severity", "unknown"), 5))

    lines = ["# Security Findings Report\n"]

    counts: dict[str, int] = {}
    for f in sorted_findings:
        s = f.get("severity", "unknown")
        counts[s] = counts.get(s, 0) + 1

    lines.append("## Summary\n")
    for sev in ["critical", "high", "medium", "low", "info"]:
        if sev in counts:
            lines.append(f"- **{sev.title()}**: {counts[sev]}")
    lines.append(f"\n**Total**: {len(findings)}\n")

    lines.append("## Findings\n")
    for i, f in enumerate(sorted_findings, 1):
        title = f.get("title") or f.get("name") or f.get("template") or "Untitled"
        sev = f.get("severity", "unknown").upper()
        lines.append(f"### {i}. [{sev}] {title}\n")

        for label, key in [
            ("Location", "location"), ("Location", "url"),
            ("CVE", "cve"), ("CWE", "cwe"),
            ("CVSS", "cvss"), ("CVSS Vector", "cvss_vector"),
            ("OWASP", "owasp"), ("Target", "target"),
            ("Endpoint", "endpoint"), ("Parameter", "parameter"),
            ("Impact", "impact"), ("Chain", "chain"),
            ("References", "references"), ("Session", "session"),
        ]:
            val = f.get(key, "")
            if val:
                lines.append(f"**{label}**: {val}  ")

        tags = f.get("tags", [])
        if tags:
            lines.append(f"**Tags**: {', '.join(tags)}  ")

        desc = f.get("description", "")
        if desc:
            lines.append(f"\n{desc}\n")

        evidence = f.get("evidence", "")
        if evidence and include_evidence:
            lines.append(f"\n**Evidence**:\n```\n{evidence}\n```\n")

        payload = f.get("payload", "")
        if payload and include_evidence:
            lines.append(f"\n**Payload**:\n```\n{payload}\n```\n")

        response = f.get("response", "")
        if response and include_evidence:
            lines.append(f"\n**Response**:\n```\n{response}\n```\n")

        reproduction = f.get("reproduction", "")
        if reproduction:
            lines.append(f"\n**Reproduction**:\n{reproduction}\n")

        remediation = f.get("remediation", "")
        if remediation:
            lines.append(f"\n**Remediation**: {remediation}\n")

    return "\n".join(lines)


_SARIF_SEVERITY_MAP = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}


def _render_sarif_report(findings: list[dict[str, Any]]) -> str:
    results = []
    rules: dict[str, dict[str, Any]] = {}

    for f in findings:
        rule_id = f.get("cwe") or (f.get("tags", []) or ["unknown"])[0]
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "shortDescription": {"text": rule_id},
            }

        location_uri = f.get("location") or f.get("url") or ""
        sev = f.get("severity", "unknown").lower()

        result: dict[str, Any] = {
            "ruleId": rule_id,
            "level": _SARIF_SEVERITY_MAP.get(sev, "note"),
            "message": {"text": f.get("description", f.get("title", ""))},
        }
        if location_uri:
            result["locations"] = [{
                "physicalLocation": {
                    "artifactLocation": {"uri": location_uri},
                },
            }]
        results.append(result)

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "RAI",
                    "informationUri": "https://github.com/example/rai",
                    "rules": list(rules.values()),
                },
            },
            "results": results,
        }],
    }
    return json.dumps(sarif, indent=2)


_CSV_COLUMNS = [
    "title", "severity", "description", "location", "cve", "cwe",
    "evidence", "remediation", "tags", "cvss", "cvss_vector", "owasp",
    "target", "endpoint", "parameter", "payload", "response",
    "reproduction", "impact", "chain", "references", "session", "source",
]


def _render_csv_report(findings: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for f in findings:
        row = dict(f)
        tags = row.get("tags")
        if isinstance(tags, list):
            row["tags"] = ", ".join(tags)
        writer.writerow(row)
    return buf.getvalue()


def _render_hackerone_report(findings: list[dict[str, Any]]) -> str:
    """Render findings as HackerOne platform-compliant markdown (one report per finding)."""
    SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    sorted_findings = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.get("severity", "info"), 5))

    parts: list[str] = []
    for f in sorted_findings:
        title = f.get("title") or "Untitled"
        sev = f.get("severity", "unknown").upper()
        cvss = f.get("cvss", "")
        cvss_vector = f.get("cvss_vector", "")
        description = f.get("description", "")
        impact = f.get("impact", "")
        evidence = f.get("evidence", "")
        payload = f.get("payload", "")
        response = f.get("response", "")
        reproduction = f.get("reproduction", "")
        remediation = f.get("remediation", "")
        cwe = f.get("cwe", "")
        cve = f.get("cve", "")
        references = f.get("references", "")
        location = f.get("location") or f.get("url") or ""
        endpoint = f.get("endpoint", "")

        lines = [f"# [{sev}] {title}", ""]

        meta: list[str] = [f"**Severity:** {sev}"]
        if cvss:
            meta.append(f"**CVSS Score:** {cvss}")
        if cvss_vector:
            meta.append(f"**CVSS Vector:** `{cvss_vector}`")
        if cwe:
            meta.append(f"**CWE:** {cwe}")
        if cve:
            meta.append(f"**CVE:** {cve}")
        if location:
            meta.append(f"**URL:** {location}")
        if endpoint:
            meta.append(f"**Endpoint:** `{endpoint}`")
        lines.extend(meta)
        lines.append("")

        lines.append("## Summary")
        lines.append(description or "_No description provided._")
        lines.append("")

        lines.append("## Steps to Reproduce")
        if reproduction:
            lines.append(reproduction)
        elif payload or evidence:
            lines.append("1. Send the following request:")
            if payload:
                lines.append(f"```\n{payload}\n```")
            if evidence:
                lines.append(f"```\n{evidence}\n```")
        else:
            lines.append("_Reproduction steps not provided._")
        lines.append("")

        lines.append("## Impact")
        lines.append(impact or "_Impact not specified._")
        lines.append("")

        poc_parts: list[str] = []
        if evidence:
            poc_parts.append(f"**Evidence:**\n```\n{evidence}\n```")
        if payload:
            poc_parts.append(f"**Payload:**\n```\n{payload}\n```")
        if response:
            poc_parts.append(f"**Server Response:**\n```\n{response}\n```")
        if poc_parts:
            lines.append("## Proof of Concept")
            lines.extend(poc_parts)
            lines.append("")

        lines.append("## Remediation")
        lines.append(remediation or "_Remediation not specified._")
        lines.append("")

        if references:
            lines.append("## References")
            for ref in references.split(","):
                ref = ref.strip()
                if ref:
                    lines.append(f"- {ref}")
            lines.append("")

        parts.append("\n".join(lines))

    return "\n\n---\n\n".join(parts)


_BUGCROWD_COLUMNS = [
    "title", "severity", "cwe", "url", "description",
    "recommendation", "poc", "cvss_vector", "cvss_score",
]


def _render_bugcrowd_csv(findings: list[dict[str, Any]]) -> str:
    """Render findings as Bugcrowd bulk-import CSV."""
    SEVERITY_MAP = {
        "critical": "P1", "high": "P2", "medium": "P3", "low": "P4", "info": "P5",
    }
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_BUGCROWD_COLUMNS)
    writer.writeheader()
    for f in findings:
        sev_raw = f.get("severity", "info").lower()
        poc_pieces: list[str] = []
        if f.get("payload"):
            poc_pieces.append(f"Payload: {f['payload']}")
        if f.get("evidence"):
            poc_pieces.append(f"Evidence: {f['evidence']}")
        if f.get("reproduction"):
            poc_pieces.append(f"Steps: {f['reproduction']}")
        writer.writerow({
            "title": f.get("title", ""),
            "severity": SEVERITY_MAP.get(sev_raw, sev_raw.upper()),
            "cwe": f.get("cwe", ""),
            "url": f.get("location") or f.get("url") or "",
            "description": f.get("description", ""),
            "recommendation": f.get("remediation", ""),
            "poc": " | ".join(poc_pieces),
            "cvss_vector": f.get("cvss_vector", ""),
            "cvss_score": f.get("cvss", ""),
        })
    return buf.getvalue()
