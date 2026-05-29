"""Reference-data tools — offline payload search, kill-chain, H1 corpus, CVE intel."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


# ── PayloadSearch ──────────────────────────────────────────────────────────


class PayloadSearchInput(BaseModel):
    vuln_class: str = Field(
        default="",
        description="Vulnerability class to filter by (e.g. 'ssrf', 'sqli', 'xss', 'jwt'). Empty = all classes.",
    )
    keyword: str = Field(
        default="",
        description="Keyword substring to match against title, payload, or notes. Empty = no keyword filter.",
    )


class PayloadSearchTool(BaseTool):
    """Search the bundled offline payload library by vuln class and/or keyword."""

    name: str = "payload_search"
    description: str = (
        "Search the bundled offline payload library. "
        "Filter by vuln_class (e.g. 'ssrf', 'sqli', 'xss', 'jwt', 'graphql', 'rce') "
        "and/or a keyword substring. Returns matching payloads with titles and usage notes. "
        "Works without network access."
    )
    args_schema: ClassVar[type[BaseModel]] = PayloadSearchInput

    def _run(self, vuln_class: str = "", keyword: str = "") -> str:
        from rai.data.references.payloads import search_payloads

        results = search_payloads(
            vuln_class=vuln_class or None,
            keyword=keyword or None,
        )
        if not results:
            return f"No payloads found for vuln_class={vuln_class!r} keyword={keyword!r}"
        lines = [f"Found {len(results)} payload(s):\n"]
        for p in results:
            lines.append(f"[{p.vuln_class}] {p.title}")
            lines.append(f"  Payload : {p.payload}")
            if p.notes:
                lines.append(f"  Notes   : {p.notes}")
            lines.append(f"  Source  : {p.source}")
            lines.append("")
        return "\n".join(lines)


# ── KillChainLookup ────────────────────────────────────────────────────────


class KillChainLookupInput(BaseModel):
    phase: str = Field(
        description=(
            "Kill-chain phase to look up tools for. "
            "Examples: recon, exploitation, persistence, privilege-escalation, "
            "lateral-movement, credential-access, exfiltration, command-and-control"
        )
    )
    limit: int = Field(default=15, description="Maximum number of tools to return.")


class KillChainLookupTool(BaseTool):
    """Return offensive tools for a kill-chain phase from the bundled YAML + RedTeam-Tools corpus."""

    name: str = "killchain_lookup"
    description: str = (
        "Return offensive tools for a given kill-chain phase (e.g. recon, exploitation, "
        "privilege-escalation, lateral-movement, credential-access). "
        "Uses the bundled YAML fallback plus the cached RedTeam-Tools README when available."
    )
    args_schema: ClassVar[type[BaseModel]] = KillChainLookupInput

    def _run(self, phase: str, limit: int = 15) -> str:
        from rai.data.references.killchain import lookup

        results = lookup(phase, limit=limit)
        if not results:
            return f"No tools found for phase '{phase}'. Try: recon, exploitation, persistence, privilege-escalation."
        lines = [f"Tools for phase '{phase}' ({len(results)}):\n"]
        for t in results:
            line = f"• {t.name}"
            if t.description:
                line += f" — {t.description[:120]}"
            if t.url:
                line += f"\n  {t.url}"
            lines.append(line)
        return "\n".join(lines)


# ── KillChainSuggest ───────────────────────────────────────────────────────


class KillChainSuggestInput(BaseModel):
    objective: str = Field(
        description="Describe what you want to accomplish (e.g. 'dump password hashes', 'enumerate shares')."
    )
    limit: int = Field(default=10, description="Maximum number of suggestions to return.")


class KillChainSuggestTool(BaseTool):
    """Suggest offensive tools by matching a free-text objective against the kill-chain corpus."""

    name: str = "killchain_suggest"
    description: str = (
        "Suggest offensive tools by keyword-matching a free-text objective description "
        "against the kill-chain corpus. "
        "Example: 'dump NTLM hashes from Windows' → impacket, mimikatz suggestions."
    )
    args_schema: ClassVar[type[BaseModel]] = KillChainSuggestInput

    def _run(self, objective: str, limit: int = 10) -> str:
        from rai.data.references.killchain import suggest

        results = suggest(objective, limit=limit)
        if not results:
            return f"No tools matched objective: '{objective}'"
        lines = [f"Suggested tools for '{objective}' ({len(results)}):\n"]
        for t in results:
            line = f"• {t.name} [{t.phase}]"
            if t.description:
                line += f" — {t.description[:120]}"
            lines.append(line)
        return "\n".join(lines)


# ── MethodologyFetch ───────────────────────────────────────────────────────


class MethodologyFetchInput(BaseModel):
    vuln_class: str = Field(
        description="Vulnerability class to look up (e.g. 'ssrf', 'idor', 'xss', 'oauth'). Matches chapter titles."
    )
    excerpt_chars: int = Field(default=2000, description="Max characters to return per chapter.")


class MethodologyFetchTool(BaseTool):
    """Fetch bug bounty methodology chapters from the cached AllAboutBugBounty corpus."""

    name: str = "methodology_fetch"
    description: str = (
        "Fetch bug bounty methodology text for a vuln class from the cached AllAboutBugBounty repo. "
        "Returns chapter content with testing steps. Requires 'rai refs install' to be run first."
    )
    args_schema: ClassVar[type[BaseModel]] = MethodologyFetchInput

    def _run(self, vuln_class: str, excerpt_chars: int = 2000) -> str:
        from rai.data.references.methodology import lookup

        results = lookup(vuln_class, excerpt_chars=excerpt_chars)
        if not results:
            return (
                f"No methodology found for '{vuln_class}'. "
                "Run 'rai refs install' to cache AllAboutBugBounty, or try a different class name."
            )
        lines = []
        for ch in results:
            lines.append(f"### {ch['title']} ({ch['vuln_class']})")
            lines.append(ch["excerpt"])
            lines.append("")
        return "\n".join(lines)


# ── OnelinerSearch ─────────────────────────────────────────────────────────


class OnelinerSearchInput(BaseModel):
    topic: str = Field(
        description="Topic or tool name to search for (e.g. 'tcpdump', 'ssh tunnel', 'nmap scripts')."
    )
    limit: int = Field(default=10, description="Maximum number of recipes to return.")


class OnelinerSearchTool(BaseTool):
    """Search the cached Book of Secret Knowledge for shell one-liners and recipes."""

    name: str = "oneliner_search"
    description: str = (
        "Search the cached book-of-secret-knowledge repo for shell one-liners, network recipes, "
        "and CLI commands. Topics: tcpdump, nmap, ssh, curl, awk, jq, tunneling, etc. "
        "Requires 'rai refs install' to be run first."
    )
    args_schema: ClassVar[type[BaseModel]] = OnelinerSearchInput

    def _run(self, topic: str, limit: int = 10) -> str:
        from rai.data.references.oneliners import search

        results = search(topic, limit=limit)
        if not results:
            return (
                f"No one-liners found for '{topic}'. "
                "Run 'rai refs install' to cache the book-of-secret-knowledge."
            )
        lines = [f"One-liners for '{topic}' ({len(results)}):\n"]
        for r in results:
            lines.append(f"### {' > '.join(r.headings)}")
            if r.description:
                lines.append(r.description[:200])
            lines.append(f"```\n{r.command}\n```")
            lines.append("")
        return "\n".join(lines)


# ── H1Search ──────────────────────────────────────────────────────────────


class H1SearchInput(BaseModel):
    keyword: str = Field(default="", description="Keyword to match in title or extras.")
    cwe: str = Field(default="", description="CWE filter, e.g. 'CWE-79' or '79'.")
    program: str = Field(default="", description="Program name substring filter.")
    min_bounty: float = Field(default=0.0, description="Minimum bounty amount in USD.")
    severity: str = Field(
        default="",
        description="Severity filter: critical, high, medium, low, informational.",
    )
    limit: int = Field(default=15, description="Maximum number of reports to return.")


class H1SearchTool(BaseTool):
    """Search disclosed HackerOne reports from the cached hackerone-reports corpus."""

    name: str = "h1_search"
    description: str = (
        "Search the cached HackerOne disclosed reports corpus. "
        "Filter by keyword, CWE, program name, minimum bounty, and/or severity. "
        "Great for prior-art research and severity calibration. "
        "Requires 'rai refs install' to be run first."
    )
    args_schema: ClassVar[type[BaseModel]] = H1SearchInput

    def _run(
        self,
        keyword: str = "",
        cwe: str = "",
        program: str = "",
        min_bounty: float = 0.0,
        severity: str = "",
        limit: int = 15,
    ) -> str:
        from rai.data.references.h1_corpus import search

        results = search(
            keyword=keyword or None,
            cwe=cwe or None,
            program=program or None,
            min_bounty=min_bounty,
            severity=severity or None,
            limit=limit,
        )
        if not results:
            return (
                "No H1 reports matched your filters. "
                "Run 'rai refs install' to cache the hackerone-reports corpus."
            )
        lines = [f"HackerOne reports ({len(results)}):\n"]
        for r in results:
            line = f"• {r.title}"
            meta: list[str] = []
            if r.severity:
                meta.append(r.severity)
            if r.cwe:
                meta.append(r.cwe)
            if r.bounty:
                meta.append(f"${r.bounty:,.0f}")
            if r.program:
                meta.append(r.program)
            if meta:
                line += f" [{', '.join(meta)}]"
            lines.append(line)
            if r.url:
                lines.append(f"  {r.url}")
        return "\n".join(lines)


# ── CVEPoCLookup ───────────────────────────────────────────────────────────


class CVEPoCLookupInput(BaseModel):
    cve_id: str = Field(description="CVE identifier, e.g. 'CVE-2021-44228'.")


class CVEPoCLookupTool(BaseTool):
    """Look up public PoC URLs for a CVE ID from the cached trickest/cve index."""

    name: str = "cve_poc_lookup"
    description: str = (
        "Look up public proof-of-concept URLs for a CVE ID (e.g. CVE-2021-44228). "
        "Uses the locally cached trickest/cve and Penetration_Testing_POC repos. "
        "Requires 'rai refs install' to be run first."
    )
    args_schema: ClassVar[type[BaseModel]] = CVEPoCLookupInput

    def _run(self, cve_id: str) -> str:
        from rai.data.references.cve_poc_index import lookup_poc

        urls = lookup_poc(cve_id)
        if not urls:
            return (
                f"No PoC URLs found for {cve_id}. "
                "Run 'rai refs install' to cache the PoC index, or check NVD for CVE details."
            )
        lines = [f"PoC URLs for {cve_id.upper()} ({len(urls)}):\n"]
        for url in urls[:25]:
            lines.append(f"• {url}")
        if len(urls) > 25:
            lines.append(f"  … and {len(urls) - 25} more")
        return "\n".join(lines)


# ── CVEIntel ───────────────────────────────────────────────────────────────


class CVEIntelInput(BaseModel):
    cve_id: str = Field(description="CVE identifier, e.g. 'CVE-2021-44228'.")


class CVEIntelTool(BaseTool):
    """Fetch live CVE intel from NVD + EPSS API."""

    name: str = "cve_intel"
    description: str = (
        "Fetch CVE details from the NVD API (CVSS score, description, affected products) "
        "and the FIRST EPSS API (exploitation probability score). "
        "Requires network access."
    )
    args_schema: ClassVar[type[BaseModel]] = CVEIntelInput

    def _run(self, cve_id: str) -> str:
        import httpx

        cve_upper = cve_id.strip().upper()
        output: dict[str, Any] = {"cve_id": cve_upper}
        errors: list[str] = []

        try:
            r = httpx.get(
                "https://services.nvd.nist.gov/rest/json/cves/2.0",
                params={"cveId": cve_upper},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                vulns = data.get("vulnerabilities", [])
                if vulns:
                    cve_data = vulns[0].get("cve", {})
                    desc_list = cve_data.get("descriptions", [])
                    desc = next(
                        (d["value"] for d in desc_list if d.get("lang") == "en"),
                        "",
                    )
                    output["description"] = desc
                    metrics = cve_data.get("metrics", {})
                    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                        if key in metrics and metrics[key]:
                            m = metrics[key][0].get("cvssData", {})
                            output["cvss_version"] = key
                            output["cvss_score"] = m.get("baseScore")
                            output["cvss_severity"] = m.get("baseSeverity")
                            output["cvss_vector"] = m.get("vectorString")
                            break
                    configs = cve_data.get("configurations", [])
                    if configs:
                        output["affected_config_count"] = len(configs)
                else:
                    errors.append("CVE not found in NVD")
            else:
                errors.append(f"NVD returned HTTP {r.status_code}")
        except Exception as exc:
            errors.append(f"NVD error: {exc}")

        try:
            r2 = httpx.get(
                "https://api.first.org/data/v1/epss",
                params={"cve": cve_upper},
                timeout=10,
            )
            if r2.status_code == 200:
                d2 = r2.json()
                items = d2.get("data", [])
                if items:
                    output["epss_score"] = items[0].get("epss")
                    output["epss_percentile"] = items[0].get("percentile")
        except Exception as exc:
            errors.append(f"EPSS error: {exc}")

        if errors:
            output["errors"] = errors

        lines = [f"CVE Intel: {cve_upper}\n"]
        if "description" in output:
            lines.append(f"Description: {output['description'][:400]}")
        if "cvss_score" in output:
            lines.append(
                f"CVSS: {output.get('cvss_score')} ({output.get('cvss_severity')}) "
                f"| {output.get('cvss_vector', '')}"
            )
        if "epss_score" in output:
            score = float(output["epss_score"]) * 100
            pct = float(output.get("epss_percentile", 0)) * 100
            lines.append(f"EPSS: {score:.2f}% exploitation probability ({pct:.0f}th percentile)")
        for err in output.get("errors", []):
            lines.append(f"Warning: {err}")
        return "\n".join(lines)


# ── Factory ────────────────────────────────────────────────────────────────


def get_reference_tools() -> list[BaseTool]:
    return [
        PayloadSearchTool(),
        KillChainLookupTool(),
        KillChainSuggestTool(),
        MethodologyFetchTool(),
        OnelinerSearchTool(),
        H1SearchTool(),
        CVEPoCLookupTool(),
        CVEIntelTool(),
    ]
