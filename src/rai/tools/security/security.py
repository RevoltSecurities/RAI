"""Security-domain tools for RAI agents.

These are standard langchain BaseTool subclasses that are passed directly to
create_deep_agent(tools=[...]).  The deepagents SDK's FilesystemMiddleware
provides read_file / write_file / edit_file / execute / grep / glob already,
so this module only defines tools that go beyond that baseline:

  - http_request    — raw HTTP client for manual probing
  - nuclei_scan     — runs nuclei templates against a target
  - nmap_scan       — runs nmap against a host/range
  - findings_add    — record a finding in the session store
  - findings_list   — list tracked findings
  - findings_export — export findings as JSON / markdown
  - web_search      — DuckDuckGo search + trafilatura page extraction

Memory tools (memory_files_list, memory_read, memory_write, memory_update) live
in memory_tools.py and are created via get_memory_tools(agent_name, target) —
they require agent context at construction time so they are not part of
get_security_tools().
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Any, ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from rai.tools.core.findings import (
    _add_finding,
    _get_findings,
    FindingsAddTool,
    FindingsListTool,
    FindingsExportTool,
)


# ---------------------------------------------------------------------------
# HTTP request tool
# ---------------------------------------------------------------------------


class HttpRequestInput(BaseModel):
    url: str = Field(description="Full URL to request")
    method: str = Field(default="GET", description="HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP headers as key-value pairs")
    body: str | None = Field(default=None, description="Request body (for POST/PUT/PATCH)")
    timeout: int = Field(default=30, description="Timeout in seconds")
    follow_redirects: bool = Field(default=True, description="Whether to follow redirects")
    verify_ssl: bool = Field(default=True, description="Whether to verify SSL certificates")


class HttpRequestTool(BaseTool):
    """Make raw HTTP requests for manual probing and API testing."""

    name: str = "http_request"
    description: str = (
        "Make raw HTTP requests to probe web endpoints. "
        "Returns status code, response headers, and body. "
        "Useful for manual API testing, header inspection, and custom payload delivery."
    )
    args_schema: ClassVar[type[BaseModel]] = HttpRequestInput

    def _run(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        timeout: int = 30,
        follow_redirects: bool = True,
        verify_ssl: bool = True,
    ) -> str:
        try:
            import httpx
        except ImportError:
            return "Error: httpx not installed. Run: pip install httpx"

        try:
            with httpx.Client(verify=verify_ssl, follow_redirects=follow_redirects, timeout=timeout) as client:
                response = client.request(
                    method=method.upper(),
                    url=url,
                    headers=headers or {},
                    content=body.encode() if body else None,
                )
            headers_str = "\n".join(f"  {k}: {v}" for k, v in response.headers.items())
            body_preview = response.text[:4000]
            if len(response.text) > 4000:
                body_preview += f"\n... ({len(response.text) - 4000} more bytes truncated)"
            return (
                f"HTTP {response.status_code} {response.reason_phrase}\n"
                f"URL: {response.url}\n"
                f"Headers:\n{headers_str}\n\n"
                f"Body:\n{body_preview}"
            )
        except Exception as e:
            return f"Request failed: {e}"


# ---------------------------------------------------------------------------
# Nuclei scan tool
# ---------------------------------------------------------------------------


class NucleiScanInput(BaseModel):
    target: str = Field(description="Target URL or host to scan")
    templates: list[str] = Field(
        default_factory=list,
        description="Nuclei template paths or tags (e.g. ['cves/', 'exposures/'] or ['CVE-2021-44228']). Empty = default templates.",
    )
    severity: list[str] = Field(
        default_factory=list,
        description="Filter by severity: critical, high, medium, low, info. Empty = all.",
    )
    extra_args: list[str] = Field(
        default_factory=list,
        description="Extra nuclei CLI arguments (e.g. ['-rate-limit', '50'])",
    )
    timeout: int = Field(default=300, description="Scan timeout in seconds")


class NucleiScanTool(BaseTool):
    """Run nuclei vulnerability scanner against a target."""

    name: str = "nuclei_scan"
    description: str = (
        "Run nuclei templates against a target URL or host. "
        "Returns structured findings including template name, severity, matched URL, and extracted data. "
        "Requires nuclei to be installed (https://github.com/projectdiscovery/nuclei)."
    )
    args_schema: ClassVar[type[BaseModel]] = NucleiScanInput

    def _run(
        self,
        target: str,
        templates: list[str] | None = None,
        severity: list[str] | None = None,
        extra_args: list[str] | None = None,
        timeout: int = 300,
    ) -> str:
        import shutil

        if not shutil.which("nuclei"):
            return (
                "Error: nuclei not found on PATH. "
                "Install from: https://github.com/projectdiscovery/nuclei/releases"
            )

        cmd: list[str] = ["nuclei", "-target", target, "-json", "-silent"]

        for t in (templates or []):
            cmd.extend(["-t", t])

        if severity:
            cmd.extend(["-severity", ",".join(severity)])

        cmd.extend(extra_args or [])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Nuclei scan timed out after {timeout}s"
        except Exception as e:
            return f"Failed to run nuclei: {e}"

        findings = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                findings.append(json.loads(line))
            except json.JSONDecodeError:
                findings.append({"raw": line})

        if not findings:
            stderr = result.stderr.strip()
            return f"No findings.\n{stderr}" if stderr else "No findings."

        # Auto-add to findings store (normalized to match manual finding fields)
        for f in findings:
            info = f.get("info", {})
            classification = info.get("classification", {})
            cve_ids = classification.get("cve-id") or []
            cwe_ids = classification.get("cwe-id") or []
            refs = info.get("reference") or []
            tags_list = info.get("tags") or []

            _add_finding({
                "source": "nuclei",
                "title": info.get("name", ""),
                "severity": info.get("severity", "unknown"),
                "location": f.get("matched-at", target),
                "description": info.get("description", ""),
                "cve": ", ".join(cve_ids) if isinstance(cve_ids, list) else str(cve_ids),
                "cwe": ", ".join(cwe_ids) if isinstance(cwe_ids, list) else str(cwe_ids),
                "references": ", ".join(refs) if isinstance(refs, list) else str(refs),
                "tags": tags_list if isinstance(tags_list, list) else [str(tags_list)],
                "template": f.get("template-id", "unknown"),
                "raw": f,
            })

        lines = [f"Nuclei scan: {len(findings)} finding(s)"]
        for f in findings:
            info = f.get("info", {})
            lines.append(
                f"  [{info.get('severity', '?').upper()}] {info.get('name', f.get('template-id', '?'))} "
                f"@ {f.get('matched-at', '?')}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Nmap scan tool
# ---------------------------------------------------------------------------


class NmapScanInput(BaseModel):
    target: str = Field(description="Target host, IP, or CIDR range")
    ports: str = Field(default="", description="Port range (e.g. '80,443,8080' or '1-1000'). Empty = nmap default.")
    scan_type: str = Field(
        default="-sV",
        description="Nmap scan type flags (e.g. '-sV' for service detection, '-sS' for SYN, '-sU' for UDP)",
    )
    scripts: list[str] = Field(
        default_factory=list,
        description="NSE scripts to run (e.g. ['http-headers', 'ssl-cert'])",
    )
    extra_args: list[str] = Field(default_factory=list, description="Extra nmap CLI arguments")
    timeout: int = Field(default=120, description="Scan timeout in seconds")


class NmapScanTool(BaseTool):
    """Run nmap port and service discovery against a target."""

    name: str = "nmap_scan"
    description: str = (
        "Run nmap against a host or network range for port and service discovery. "
        "Returns open ports, services, versions, and script output. "
        "Requires nmap to be installed."
    )
    args_schema: ClassVar[type[BaseModel]] = NmapScanInput

    def _run(
        self,
        target: str,
        ports: str = "",
        scan_type: str = "-sV",
        scripts: list[str] | None = None,
        extra_args: list[str] | None = None,
        timeout: int = 120,
    ) -> str:
        import shutil

        if not shutil.which("nmap"):
            return "Error: nmap not found on PATH. Install: brew install nmap / apt install nmap"

        cmd: list[str] = ["nmap"]
        cmd.extend(scan_type.split())

        if ports:
            cmd.extend(["-p", ports])

        if scripts:
            cmd.extend(["--script", ",".join(scripts)])

        cmd.extend(extra_args or [])
        cmd.append(target)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"nmap scan timed out after {timeout}s"
        except Exception as e:
            return f"Failed to run nmap: {e}"

        output = result.stdout.strip()
        if result.returncode != 0 and not output:
            return f"nmap error: {result.stderr.strip()}"
        return output or f"nmap returned no output.\nStderr: {result.stderr.strip()}"


# ---------------------------------------------------------------------------
# Create subagent tool
# ---------------------------------------------------------------------------


class CreateSubagentInput(BaseModel):
    name: str = Field(description="Subagent identifier in kebab-case (e.g. 'jwt-auditor'). Spaces are converted to hyphens.")
    description: str = Field(description="One-line description shown in /agents listing")
    system_prompt: str = Field(default="", description="Full system prompt string. Use this OR system_prompt_path — not both.")
    system_prompt_path: str = Field(default="", description="Path to a file containing the system prompt. Preferred for long prompts — the tool reads the file automatically.")
    parent_agent: str = Field(default="rai", description="Parent agent name — the AGENTS.md to append to (e.g. 'rai')")
    model: str = Field(default="inherit", description="Model string or 'inherit' to use parent's model (e.g. 'openai/gpt-4o', 'anthropic:claude-sonnet-4-6')")
    api_key: str = Field(default="", description="API key, or empty/inherit to use parent's key")
    base_url: str = Field(default="", description="Custom base URL for a proxy or local model, or empty to inherit")


class CreateSubagentTool(BaseTool):
    """Create a new RAI subagent by appending its definition to an AGENTS.md file."""

    name: str = "create_subagent"
    description: str = (
        "Create a new specialized subagent for RAI by writing its definition to the parent agent's AGENTS.md. "
        "The subagent will be available after restarting RAI or using /reload-agents. "
        "For long prompts (agent-creator output), pass system_prompt_path pointing to the written file "
        "instead of inlining the full prompt string in system_prompt."
    )
    args_schema: ClassVar[type[BaseModel]] = CreateSubagentInput

    def _run(
        self,
        name: str,
        description: str,
        system_prompt: str = "",
        system_prompt_path: str = "",
        parent_agent: str = "rai",
        model: str = "inherit",
        api_key: str = "",
        base_url: str = "",
        **_kwargs: Any,
    ) -> str:
        from rai.config.settings import settings

        # Normalise name
        name = name.strip().lower().replace(" ", "-").replace("_", "-")
        if not name:
            return "Error: subagent name cannot be empty."

        # Resolve system prompt — prefer path over inline string for long prompts
        if system_prompt_path.strip():
            try:
                system_prompt = pathlib.Path(system_prompt_path.strip()).read_text(encoding="utf-8")
            except OSError as exc:
                return f"Error: could not read system_prompt_path '{system_prompt_path}': {exc}"
        if not system_prompt.strip():
            return "Error: provide either system_prompt (string) or system_prompt_path (file path)."

        individual_md_path = settings.agent_md_path(name)

        # Duplicate check — individual agent dir is the source of truth
        if individual_md_path.exists():
            return (
                f"Error: subagent '{name}' already exists at "
                f"{individual_md_path}. Choose a different name or edit the file directly."
            )

        # Resolve inherit values
        effective_model = model.strip() or "inherit"
        effective_api_key = api_key.strip() if api_key.strip() not in ("", "inherit") else "inherit"
        effective_base_url = base_url.strip() if base_url.strip() not in ("", "inherit") else "inherit"

        # Write individual agent dir + memory + AGENTS.md
        try:
            settings.ensure_memory_files(name)
            from rai.defaults.agents import _write_individual_agent_md
            _write_individual_agent_md(
                agent_name=name,
                description=description.strip(),
                model=effective_model,
                api_key=effective_api_key,
                base_url=effective_base_url,
                system_prompt=system_prompt.strip(),
            )
        except OSError as exc:
            return f"Error creating subagent '{name}': {exc}"

        return (
            f"✓ Subagent '{name}' created at {individual_md_path}\n"
            f"  Model: {effective_model}\n"
            f"  Invoke with: @{name} <task>  or  /reload-agents to activate without restart"
        )

    async def _arun(self, *args: Any, **kwargs: Any) -> str:  # type: ignore[override]
        return self._run(*args, **kwargs)


# ---------------------------------------------------------------------------
# Web search tool
# ---------------------------------------------------------------------------

_WS_MAX_RESULTS = int(os.environ.get("RAI_SEARCH_MAX_RESULTS", "10"))
_WS_FETCH_TOP_N = int(os.environ.get("RAI_SEARCH_FETCH_TOP_N", "5"))
_WS_FETCH_TIMEOUT = int(os.environ.get("RAI_FETCH_TIMEOUT", "20"))
_WS_MAX_CONTENT = 40_000   # chars returned per page to the agent
_WS_MIN_CONTENT = 150      # skip pages with less content than this

_WS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RAI/1.0; +https://example.com/rai)",
}


def _ws_search_ddg(query: str, max_results: int) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except ImportError:
        raise RuntimeError("duckduckgo-search not installed: pip install duckduckgo-search")
    except Exception as e:
        raise RuntimeError(f"DDG search failed: {e}") from e


async def _ws_get_results(query: str, max_results: int) -> list[dict]:
    import asyncio
    return await asyncio.to_thread(_ws_search_ddg, query, max_results)


async def _ws_fetch_page(url: str) -> str | None:
    import httpx
    try:
        async with httpx.AsyncClient(
            timeout=_WS_FETCH_TIMEOUT,
            follow_redirects=True,
            headers=_WS_HEADERS,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        return None

    try:
        import trafilatura
        content = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=False,
            include_tables=True,
            include_images=False,
            no_fallback=False,
        )
    except Exception:
        content = None

    if not content or len(content) < _WS_MIN_CONTENT:
        # Readability fallback (optional dep)
        try:
            from readability import Document
            import re as _re
            doc = Document(html)
            content = _re.sub(r"<[^>]+>", " ", doc.summary(html_partial=True)).strip()
        except Exception:
            return None

    if not content or len(content) < _WS_MIN_CONTENT:
        return None
    return content[:_WS_MAX_CONTENT]


class WebSearchInput(BaseModel):
    query: str = Field(description="Search query.")
    max_results: int = Field(default=5, description="Max search results to retrieve.")
    fetch_top_n: int = Field(default=3, description="How many top URLs to fetch and extract content from.")
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Only include results from these domains (e.g. ['nvd.nist.gov', 'github.com']).",
    )
    blocked_domains: list[str] = Field(
        default_factory=list,
        description="Exclude results from these domains.",
    )


class WebSearchTool(BaseTool):
    """Search the web and extract full page content from top results via DuckDuckGo."""

    name: str = "web_search"
    description: str = (
        "Search the web and return full page content from top results. "
        "Use for CVE lookups, exploit PoC research, security advisories, "
        "package vulnerabilities, documentation, and any real-time information. "
        "Returns snippets for all results and extracted Markdown for the top fetched pages."
    )
    args_schema: ClassVar[type[BaseModel]] = WebSearchInput

    def _run(
        self,
        query: str,
        max_results: int = _WS_MAX_RESULTS,
        fetch_top_n: int = _WS_FETCH_TOP_N,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        **_kwargs: Any,
    ) -> str:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    return ex.submit(
                        asyncio.run,
                        self._arun(
                            query=query,
                            max_results=max_results,
                            fetch_top_n=fetch_top_n,
                            allowed_domains=allowed_domains or [],
                            blocked_domains=blocked_domains or [],
                        ),
                    ).result()
            return loop.run_until_complete(
                self._arun(
                    query=query,
                    max_results=max_results,
                    fetch_top_n=fetch_top_n,
                    allowed_domains=allowed_domains or [],
                    blocked_domains=blocked_domains or [],
                )
            )
        except Exception as exc:
            return f"[web_search error] {exc}"

    async def _arun(  # type: ignore[override]
        self,
        query: str,
        max_results: int = _WS_MAX_RESULTS,
        fetch_top_n: int = _WS_FETCH_TOP_N,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        **_kwargs: Any,
    ) -> str:
        import asyncio

        # 1. Search
        try:
            raw = await _ws_get_results(query, max_results)
        except Exception as exc:
            return f"[web_search error] {exc}"

        if not raw:
            return f"[web_search] No results for: {query}"

        # 2. Filter domains
        filtered = []
        for item in raw:
            url = item.get("href") or item.get("url", "")
            if not url:
                continue
            if allowed_domains and not any(d in url for d in allowed_domains):
                continue
            if blocked_domains and any(d in url for d in (blocked_domains or [])):
                continue
            filtered.append((url, item.get("title", ""), item.get("body", "")))

        if not filtered:
            return f"[web_search] All results filtered out for: {query}"

        # 3. Fetch top N pages concurrently
        fetch_targets = filtered[:fetch_top_n]
        fetched: dict[str, str | None] = {}

        async def _do_fetch(url: str) -> None:
            fetched[url] = await _ws_fetch_page(url)

        await asyncio.gather(*[_do_fetch(url) for url, _, _ in fetch_targets])

        # 4. Format output
        lines = [f"## Web search: {query}\n"]
        for i, (url, title, snippet) in enumerate(filtered, 1):
            lines.append(f"### [{i}] {title}")
            lines.append(f"URL: {url}")
            content = fetched.get(url)
            if content:
                lines.append(f"\n{content}\n")
            else:
                lines.append(f"Snippet: {snippet}\n")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Web fetch tool
# ---------------------------------------------------------------------------

_WF_MAX_CONTENT = 80_000   # larger than search — agent asked for this page specifically


async def _wf_fetch(url: str) -> tuple[str | None, str | None]:
    """Fetch *url* and extract Markdown. Returns (content, error); one will be None."""
    import re as _re
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=_WS_FETCH_TIMEOUT,
            follow_redirects=False,       # report redirects to the agent, don't auto-follow
            headers=_WS_HEADERS,
        ) as client:
            resp = await client.get(url)

            if resp.is_redirect:
                location = resp.headers.get("location", "")
                return None, (
                    f"REDIRECT {resp.status_code} → {location}. "
                    "Call web_fetch again with the redirect URL to continue."
                )
            resp.raise_for_status()
            html = resp.text
    except httpx.HTTPStatusError as exc:
        return None, f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
    except Exception as exc:
        return None, str(exc)

    # Extract Markdown (links preserved — agent may need to follow them)
    try:
        import trafilatura
        content: str | None = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            include_images=False,
            no_fallback=False,
        )
    except Exception:
        content = None

    if not content or len(content) < _WS_MIN_CONTENT:
        try:
            from readability import Document
            doc = Document(html)
            content = _re.sub(r"<[^>]+>", " ", doc.summary(html_partial=True)).strip()
        except Exception:
            pass

    if not content or len(content) < _WS_MIN_CONTENT:
        return None, "Could not extract readable content from page."

    return content[:_WF_MAX_CONTENT], None


class WebFetchInput(BaseModel):
    url: str = Field(description="Full URL to fetch (https://...). Use URLs from web_search results or user-provided links.")
    prompt: str = Field(
        default="",
        description=(
            "What to look for on this page — guides your reading. "
            "Examples: 'What is the CVSS score?', 'Find the PoC exploit code', 'Summarize the scope section'."
        ),
    )


class WebFetchTool(BaseTool):
    """Fetch a URL and return its extracted Markdown content.

    Use when you already have a specific URL to read (e.g. from web_search
    results, a CVE advisory link, or a user-provided URL). Unlike web_search,
    this tool fetches one page at full depth rather than scanning many results.

    Cross-host redirects are reported back rather than silently followed —
    the agent can decide whether to follow them.
    """

    name: str = "web_fetch"
    description: str = (
        "Fetch a specific URL and return its full page content as Markdown. "
        "Use after web_search when you need to read the full content of a result, "
        "or when the user provides a direct link. "
        "Good for: CVE detail pages, exploit writeups, API docs, bug bounty scopes, "
        "security blog posts, GitHub issues, NVD entries."
    )
    args_schema: ClassVar[type[BaseModel]] = WebFetchInput

    def _run(
        self,
        url: str,
        prompt: str = "",
        **_kwargs: Any,
    ) -> str:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    return ex.submit(asyncio.run, self._arun(url=url, prompt=prompt)).result()
            return loop.run_until_complete(self._arun(url=url, prompt=prompt))
        except Exception as exc:
            return f"[web_fetch error] {exc}"

    async def _arun(  # type: ignore[override]
        self,
        url: str,
        prompt: str = "",
        **_kwargs: Any,
    ) -> str:
        content, error = await _wf_fetch(url)

        if error:
            return f"[web_fetch] {url}\nError: {error}"

        lines = [f"## {url}"]
        if prompt:
            lines.append(f"*Prompt: {prompt}*\n")
        lines.append(content)  # type: ignore[arg-type]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Default security tool registry
# ---------------------------------------------------------------------------


def get_security_tools() -> list[BaseTool]:
    """Return the default set of security-domain tools for RAI agents."""
    return [
        WebSearchTool(),
        WebFetchTool(),
        HttpRequestTool(),
        NucleiScanTool(),
        NmapScanTool(),
        FindingsAddTool(),
        FindingsListTool(),
        FindingsExportTool(),
    ]
