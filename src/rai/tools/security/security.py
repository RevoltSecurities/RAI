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
# RAI_FETCH_SSL_VERIFY=0 disables SSL verification (e.g. corporate MITM proxy)
_SSL_VERIFY: bool = os.environ.get("RAI_FETCH_SSL_VERIFY", "1") != "0"

# RAI_SEARCH_BACKEND: comma-separated backend(s) or "auto" (default).
# Available: auto, brave, duckduckgo, google, startpage, yahoo, yandex, mojeek
# Examples: "google", "brave,google", "auto"
_WS_BACKEND = os.environ.get("RAI_SEARCH_BACKEND", "auto")

_WS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RAI/1.0; +https://example.com/rai)",
}


def _ws_search_ddgs(query: str, max_results: int, backend: str) -> list[dict]:
    """Search via ddgs library (uses primp/HTTP2 internally)."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore[no-redef]
        except ImportError:
            raise RuntimeError("ddgs not installed: pip install ddgs")
    with DDGS(verify=_SSL_VERIFY) as d:
        return list(d.text(query, max_results=max_results, backend=backend))


def _ws_search_ddg_lite(query: str, max_results: int) -> list[dict]:
    """Fallback: parse DDG Lite HTML via httpx (HTTP/1.1, proxy-friendly).

    Used automatically when RAI_FETCH_SSL_VERIFY=0 and ddgs fails due to
    HTTP/2 HPACK errors from MITM proxies that impersonation triggers.
    """
    import re
    import urllib.parse
    import httpx

    url = "https://lite.duckduckgo.com/lite/"
    params = {"q": query}
    try:
        with httpx.Client(http2=False, verify=_SSL_VERIFY, timeout=_WS_FETCH_TIMEOUT) as client:
            resp = client.get(
                url, params=params,
                headers={"User-Agent": "Mozilla/5.0 (compatible; RAI/1.0)"},
            )
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"DDG lite fallback failed: {e}") from e

    html = resp.text
    results: list[dict] = []

    # DDG lite attr order: href='...' class='result-link'>Title</a>
    hrefs = re.findall(r"href=['\"]([^'\"]+)['\"][^>]*class=['\"]result-link['\"]", html)
    titles = re.findall(r"class=['\"]result-link['\"]>(.*?)</a>", html, re.S)
    snippets = re.findall(r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>", html, re.S)

    for i, (href, title) in enumerate(zip(hrefs, titles)):
        m = re.search(r"uddg=([^&'\"]+)", href)
        real_url = urllib.parse.unquote(m.group(1)) if m else href
        title = re.sub(r"<[^>]+>", "", title).strip()
        snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
        if real_url and title:
            results.append({"title": title, "href": real_url, "body": snippet})
        if len(results) >= max_results:
            break

    return results


def _ws_search(query: str, max_results: int, backend: str = _WS_BACKEND) -> list[dict]:
    """Search with automatic fallback: ddgs → DDG lite (when SSL verify disabled)."""
    try:
        return _ws_search_ddgs(query, max_results, backend)
    except Exception as e:
        err = str(e)
        # HTTP/2 HPACK errors from MITM proxies — fall back to plain HTTP/1.1 DDG lite
        if not _SSL_VERIFY and ("HPACK" in err or "LocalProtocol" in err or "ConnectError" in err):
            return _ws_search_ddg_lite(query, max_results)
        raise RuntimeError(f"Search failed [{backend}]: {e}") from e




async def _ws_fetch_page(url: str) -> str | None:
    """Fetch a single page for web_search result enrichment. Uses shared _html_to_markdown."""
    import httpx
    try:
        async with httpx.AsyncClient(
            timeout=_WS_FETCH_TIMEOUT,
            follow_redirects=True,
            headers=_WS_HEADERS,
            verify=_SSL_VERIFY,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        return None

    content = _html_to_markdown(html)
    if not content:
        return None
    return content[:_WS_MAX_CONTENT]


class WebSearchInput(BaseModel):
    query: str = Field(description="Search query.")
    max_results: int = Field(default=5, description="Max search results to retrieve.")
    fetch_top_n: int = Field(default=3, description="How many top URLs to fetch and extract content from.")
    backend: str = Field(
        default="auto",
        description=(
            "Search backend(s) to use. 'auto' tries all available engines. "
            "Options: auto, brave, duckduckgo, google, startpage, yahoo, yandex, mojeek. "
            "Comma-separate multiple: 'brave,google'. "
            "Override default via RAI_SEARCH_BACKEND env var."
        ),
    )
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
        backend: str = _WS_BACKEND,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        **_kwargs: Any,
    ) -> str:
        import asyncio
        import concurrent.futures
        coro = self._arun(
            query=query,
            max_results=max_results,
            fetch_top_n=fetch_top_n,
            backend=backend,
            allowed_domains=allowed_domains or [],
            blocked_domains=blocked_domains or [],
        )
        try:
            asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, coro).result()
        except RuntimeError:
            return asyncio.run(coro)

    async def _arun(  # type: ignore[override]
        self,
        query: str,
        max_results: int = _WS_MAX_RESULTS,
        fetch_top_n: int = _WS_FETCH_TOP_N,
        backend: str = _WS_BACKEND,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        **_kwargs: Any,
    ) -> str:
        import asyncio

        # 1. Search
        try:
            raw = await asyncio.to_thread(_ws_search, query, max_results, backend)
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

# Chunk size the agent receives per call — reads the page in windows like bash `head`/`tail`.
# RAI_FETCH_CHUNK_SIZE controls this; default 5000 chars (~1250 tokens).
_WF_CHUNK_SIZE   = int(os.environ.get("RAI_FETCH_CHUNK_SIZE", "5000"))
_WF_MAX_TOTAL    = int(os.environ.get("RAI_FETCH_MAX_TOTAL",  "200000"))  # hard cap on full page
_WF_TIMEOUT      = int(os.environ.get("RAI_FETCH_TIMEOUT",    str(_WS_FETCH_TIMEOUT)))

_WF_USER_AGENT = (
    "ModelContextProtocol/1.0 (Autonomous; +https://github.com/modelcontextprotocol/servers)"
)


def _html_to_markdown(html: str) -> str:
    """Convert HTML to clean Markdown. Primary: markdownify. Fallback: trafilatura."""
    # Primary: markdownify — clean, link-preserving, heading-aware
    try:
        import markdownify as _md
        content = _md.markdownify(html, heading_style=_md.ATX, strip=["script", "style"])
        # collapse excessive blank lines
        import re as _re
        content = _re.sub(r"\n{3,}", "\n\n", content).strip()
        if content and len(content) >= _WS_MIN_CONTENT:
            return content
    except Exception:
        pass

    # Fallback: trafilatura
    try:
        import trafilatura
        content = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            include_images=False,
            no_fallback=False,
        )
        if content and len(content) >= _WS_MIN_CONTENT:
            return content
    except Exception:
        pass

    return ""


async def _wf_fetch_raw(url: str) -> tuple[str, str, str]:
    """Fetch URL. Returns (html_or_raw, content_type, error). On error html_or_raw is ""."""
    import httpx
    try:
        async with httpx.AsyncClient(
            timeout=_WF_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _WF_USER_AGENT},
            verify=_SSL_VERIFY,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text, resp.headers.get("content-type", ""), ""
    except httpx.HTTPStatusError as exc:
        return "", "", f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
    except Exception as exc:
        return "", "", str(exc)


class WebFetchInput(BaseModel):
    url: str = Field(
        description="Full URL to fetch (https://...). Use URLs from web_search results or user-provided links.",
    )
    prompt: str = Field(
        default="",
        description=(
            "What to look for on this page — guides your reading. "
            "E.g. 'What is the CVSS score?', 'Find the PoC exploit code'."
        ),
    )
    start_index: int = Field(
        default=0,
        ge=0,
        description=(
            "Character offset to start reading from. "
            "Use the next_start value from a previous truncated response to read the next chunk."
        ),
    )
    raw: bool = Field(
        default=False,
        description="Return raw HTML/text instead of extracted Markdown. Use when Markdown conversion loses important content.",
    )


class WebFetchTool(BaseTool):
    """Fetch a URL and return its content as Markdown, with chunk-by-chunk reading.

    Reads pages in configurable chunks (default 5000 chars, ~1250 tokens).
    When the page is longer, the response includes a next_start index — call
    web_fetch again with that start_index to read the next window.

    Supports robots.txt-respecting fetch, markdownify HTML extraction,
    trafilatura fallback, and raw mode for structured data / APIs.
    """

    name: str = "web_fetch"
    description: str = (
        "Fetch a specific URL and return its content as Markdown. "
        "Reads in chunks — if truncated, use the returned next_start index to continue. "
        "Use after web_search when you need the full content of a result, "
        "or for direct links: CVE pages, exploit writeups, API docs, bug bounty scopes, "
        "GitHub issues, NVD entries, security advisories."
    )
    args_schema: ClassVar[type[BaseModel]] = WebFetchInput

    def _run(
        self,
        url: str,
        prompt: str = "",
        start_index: int = 0,
        raw: bool = False,
        **_kwargs: Any,
    ) -> str:
        import asyncio
        import concurrent.futures
        coro = self._arun(url=url, prompt=prompt, start_index=start_index, raw=raw)
        try:
            asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, coro).result()
        except RuntimeError:
            return asyncio.run(coro)

    async def _arun(  # type: ignore[override]
        self,
        url: str,
        prompt: str = "",
        start_index: int = 0,
        raw: bool = False,
        **_kwargs: Any,
    ) -> str:
        html, content_type, error = await _wf_fetch_raw(url)

        if error:
            return f"[web_fetch] {url}\nError: {error}"

        # Decide whether to extract Markdown or return raw
        is_html = (
            "<html" in html[:200].lower()
            or "text/html" in content_type
            or not content_type
        )

        if is_html and not raw:
            content = _html_to_markdown(html)
            if not content:
                return (
                    f"[web_fetch] {url}\n"
                    "Could not extract readable content. "
                    "Retry with raw=true to get the raw HTML."
                )
        else:
            content = html
            if not raw:
                # Non-HTML (JSON, XML, plain text) — return as-is with note
                prefix = f"[Content-Type: {content_type}]\n"
                content = prefix + content

        # Hard cap before chunking
        content = content[:_WF_MAX_TOTAL]
        total_len = len(content)

        # Validate start_index
        if start_index >= total_len:
            return (
                f"[web_fetch] {url}\n"
                f"<error>start_index {start_index} is past end of content "
                f"({total_len} chars total). No more content available.</error>"
            )

        chunk = content[start_index : start_index + _WF_CHUNK_SIZE]
        chunk_end = start_index + len(chunk)
        remaining = total_len - chunk_end

        lines = [f"## {url}"]
        if prompt:
            lines.append(f"*Prompt: {prompt}*\n")
        lines.append(f"*Showing chars {start_index}–{chunk_end} of {total_len} total*\n")
        lines.append(chunk)

        if remaining > 0:
            lines.append(
                f"\n<truncated> {remaining} chars remaining. "
                f"Call web_fetch with start_index={chunk_end} to read the next chunk.</truncated>"
            )
        else:
            lines.append("\n*[end of page]*")

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
