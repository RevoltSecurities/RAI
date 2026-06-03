"""RAI agent factory.

create_rai_agent() builds a general-purpose agentic assistant using the
deepagents SDK as the foundation, with the following layers:

  - Per-agent memory at ~/.rai/agents/<name>/memories/ (3 files)
  - Skills from ~/.rai/skills/ + ~/.rai/agents/<name>/skills/
  - LocalShellBackend for full shell + filesystem access
  - Configurable model middleware (per-request model overrides via runtime context)
  - Audit log middleware (logs every tool call)
  - Hooks middleware (Claude Code–compatible PreToolUse/PostToolUse hooks)
  - Ask-user middleware (injects ask_user tool for mid-task clarification)
  - Findings enrichment middleware (keeps findings count in system prompt)
  - MCP tools loaded from ~/.rai/.mcp.json + project .mcp.json
  - Built-in tools (bash, http, nuclei, nmap, findings)
  - Human-in-the-loop interrupts for destructive operations
  - Custom subagents loaded from ~/.rai/agents/<name>/subagents/
"""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import AsyncSubAgent, CompiledSubAgent, create_deep_agent
from deepagents.backends import CompositeBackend, LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware import SkillsMiddleware
from rai.middleware.memory import RAIMemoryMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware, SummarizationToolMiddleware

from rai.config.agent import AgentConfig, load_agent_config
from rai.agents.loader import load_subagents_for
from rai.tools.core.bash import get_builtin_tools
from rai.tools.core.memory import get_memory_tools
from rai.config.settings import settings
from rai.defaults.agents import ensure_default_agents
from rai.defaults.skills import ensure_default_skills
from rai.agents.background import LocalAsyncAgentMiddleware
from rai.middleware.audit import AuditLogMiddleware
from rai.middleware.sanitizer import EmptyContentSanitizerMiddleware
from rai.middleware.execute import ExecuteInterceptorMiddleware
from rai.middleware.compression import MessageCompressionMiddleware
from rai.middleware.findings import FindingsEnrichmentMiddleware
from rai.middleware.hooks import HooksMiddleware
from rai.middleware.rtk import RTKToolMiddleware
from rai.tools.security.security import get_security_tools
from rai.engine.model import DEFAULT_MODEL, DEFAULT_AGENT_NAME, _is_litellm_format, _build_llm

if TYPE_CHECKING:
    from deepagents.middleware.subagents import SubAgent
    from langchain.agents.middleware.types import InterruptOnConfig
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.pregel import Pregel

    from rai.mcp.session import MCPServerInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Context engineering: lower tool result storage limit and calibrate token counter
# ---------------------------------------------------------------------------
# deepagents defaults TOOL_RESULT_TOKEN_LIMIT to 20000 (×4 chars = 80k chars).
# Lower to 8000 tokens (32k chars) to reduce how much a single bash/fetch result
# bloats the LangGraph checkpoint and accumulated message history.
try:
    import deepagents.backends.utils as _dbu
    _dbu.TOOL_RESULT_TOKEN_LIMIT = 4000
except Exception:
    pass


# ---------------------------------------------------------------------------
# Subagent type helpers
# ---------------------------------------------------------------------------

# Union type for all subagent specs accepted by create_rai_agent.
# SubAgent       — dict with system_prompt; built by SDK at runtime
# AsyncSubAgent  — dict with graph_id; delegates to remote Agent Protocol server
# CompiledSubAgent — dict with runnable; pre-compiled Pregel graph
AnySubAgent = Any  # TypeAlias kept as Any for runtime flexibility


def _is_spec_subagent(s: dict) -> bool:
    """True when s is a plain SubAgent spec (has system_prompt, not pre-compiled/remote)."""
    return "system_prompt" in s and "runnable" not in s and "graph_id" not in s


# ---------------------------------------------------------------------------
# System prompt construction
# ---------------------------------------------------------------------------


def get_system_prompt() -> str:
    """Return the RAI system prompt from the bundled template."""
    return (Path(__file__).parent.parent / "data" / "prompts" / "rai" / "prompt.md").read_text(encoding="utf-8")


def get_subagent_system_prompt() -> str:
    """Return the slim subagent system prompt (~500 tokens vs 14,500 for the full prompt)."""
    return (Path(__file__).parent.parent / "data" / "prompts" / "subagent" / "prompt.md").read_text(encoding="utf-8")


def get_agent_prompt(agent_name: str, *, is_subagent: bool = False) -> str:
    """Return the system prompt for an agent.

    Priority:
      1. ~/.rai/agents/<agent_name>/prompt.md       — explicit user file override
      2. data/prompts/<agent_name>/prompt.md         — prebuilt bundled prompt
         Agents with a bundled prompt (e.g. "rai") always use it; their AGENTS.md
         defines subagents, not their own persona, so it is never used here.
      3. ~/.rai/agents/<agent_name>/AGENTS.md body   — custom subagent system prompt
         Only reached by agents with no bundled prompt (user-defined subagents).
      4. Slim subagent default (~500 tokens)         — if is_subagent=True
      5. Full RAI prompt (14,500 tokens)             — parent/main agents
    """
    from rai.config.settings import settings
    agent_dir = settings.agent_dir(agent_name)

    # 1. Explicit user prompt.md — highest priority
    custom = agent_dir / "prompt.md"
    if custom.exists():
        return custom.read_text(encoding="utf-8")

    # 2. Prebuilt bundled prompt — agents with a bundled prompt (e.g. "rai") always
    #    use it and never fall through to AGENTS.md, which defines their subagents.
    bundled = Path(__file__).parent.parent / "data" / "prompts" / agent_name / "prompt.md"
    if bundled.exists():
        return bundled.read_text(encoding="utf-8")

    # 3. AGENTS.md body — only for custom subagents that have no bundled prompt
    agents_md = agent_dir / "AGENTS.md"
    if agents_md.exists():
        try:
            from rai.agents.parser import parse_agents_md
            entries = parse_agents_md(agents_md)
            if entries:
                # Use entries[0] fallback only for single-entry files (hand-edited
                # with no name field). Multi-entry = subagent definitions, not own prompt.
                named = next((e for e in entries if e.name == agent_name), None)
                entry = named if named is not None else (entries[0] if len(entries) == 1 else None)
                if entry and entry.system_prompt:
                    _WARN_THRESHOLD = 20_000
                    if len(entry.system_prompt) > _WARN_THRESHOLD:
                        logger.warning(
                            "AGENTS.md body for agent '%s' is %d chars (~%d tokens) — "
                            "this is injected as the system prompt on EVERY LLM call. "
                            "Move detailed methodology to a skills file or memory file to reduce per-call cost. "
                            "Path: %s",
                            agent_name,
                            len(entry.system_prompt),
                            len(entry.system_prompt) // 4,
                            agents_md,
                        )
                    return entry.system_prompt
        except Exception:
            pass  # fall through to defaults

    # 4/5. No custom prompt — slim default for subagents, full RAI prompt for parent
    return get_subagent_system_prompt() if is_subagent else get_system_prompt()


# ---------------------------------------------------------------------------
# Subagent loading from TOML files
# ---------------------------------------------------------------------------


def load_custom_subagents(agent_name: str) -> list[SubAgent]:
    """Load custom subagent definitions from ~/.rai/agents/<name>/subagents/*.toml.

    Each TOML file must have at minimum:
        name = "subagent-name"
        description = "What this subagent does"
        system_prompt = "You are a ..."

    Optional fields: model, skills (list of paths)
    """
    subagents_dir = settings.agent_dir(agent_name) / "subagents"
    if not subagents_dir.exists():
        return []

    result: list[SubAgent] = []
    for toml_file in sorted(subagents_dir.glob("*.toml")):
        try:
            with toml_file.open("rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            logger.warning("Could not load subagent from %s: %s", toml_file, e)
            continue

        required = {"name", "description", "system_prompt"}
        if missing := required - data.keys():
            logger.warning("Skipping subagent %s: missing fields %s", toml_file.name, missing)
            continue

        subagent: SubAgent = {
            "name": data["name"],
            "description": data["description"],
            "system_prompt": data["system_prompt"],
        }
        if "model" in data:
            subagent["model"] = data["model"]
        if "skills" in data and isinstance(data["skills"], list):
            subagent["skills"] = data["skills"]
        result.append(subagent)

    return result


# ---------------------------------------------------------------------------
# HITL interrupt configuration
# ---------------------------------------------------------------------------


def _memory_write_desc(tool_call: dict, state: object, runtime: object) -> str:  # noqa: ARG001
    args = tool_call.get("args", {})
    scope = args.get("scope", "agent")
    file_arg = args.get("file", "?")
    mode = args.get("mode", "append")
    content_file: str = args.get("content_file", "")
    content: str = args.get("content", "")

    if content_file:
        # Large-content path — show file path + size instead of content preview
        from pathlib import Path as _Path
        try:
            size = _Path(content_file).stat().st_size
            size_str = f"{size:,} bytes"
        except OSError:
            size_str = "size unknown"
        return (
            f"memory_write → {scope}/{file_arg} ({mode})\n"
            f"Reading from file: {content_file} ({size_str})"
        )

    preview = content[:300] + f"\n...({len(content) - 300} more chars)" if len(content) > 300 else content
    return (
        f"memory_write → {scope}/{file_arg} ({mode})\n"
        f"{len(content)} chars\n\nPreview:\n{preview}"
    )


def _memory_update_desc(tool_call: dict, state: object, runtime: object) -> str:  # noqa: ARG001
    args = tool_call.get("args", {})
    old: str = args.get("old_text", "")
    new: str = args.get("new_text", "")
    return (
        f"memory_update → {args.get('scope', 'agent')}/{args.get('file', '?')}\n"
        f"Replace ({len(old)} chars): {old[:150]}{'...' if len(old) > 150 else ''}\n"
        f"With ({len(new)} chars): {new[:150]}{'...' if len(new) > 150 else ''}"
    )


def _build_interrupt_on() -> dict[str, InterruptOnConfig]:
    """Gate ALL tools behind user approval when running in UI (interactive) mode.

    rw = approve/edit/reject  — tools that execute, mutate, or send requests.
    ro = approve/reject only  — read/analysis tools; no edit textarea so large
         content doesn't cause the HITL widget to cancel.
    """
    rw: InterruptOnConfig = {"allowed_decisions": ["approve", "edit", "reject"]}
    ro: InterruptOnConfig = {"allowed_decisions": ["approve", "reject"]}

    return {
        # ---- shell / file ops (SDK built-ins) ----
        "execute":    {**rw, "description": "Execute a shell command on the host"},
        "bash":       {**rw, "description": "Run a shell command on the host"},
        "write_file": {**rw, "description": "Create or overwrite a file"},
        "edit_file":  {**rw, "description": "Modify an existing file"},
        "read_file":  {**ro, "description": "Read a file from disk"},
        # ---- subagents ----
        "task":            {**rw, "description": "Spawn a subagent with tool access"},
        "create_subagent": {**rw, "description": "Create and dispatch a subagent"},
        # ---- web / network ----
        "web_search":   {**ro, "description": "Search the web"},
        "web_fetch":    {**ro, "description": "Fetch a URL"},
        "http_request": {**rw, "description": "Send HTTP request to target"},
        # ---- security scanners ----
        "nuclei_scan": {**rw, "description": "Run nuclei scanner against target"},
        "nmap_scan":   {**rw, "description": "Run nmap against host/range"},
        # ---- web security ----
        "jwt_decode":        {**ro, "description": "Decode a JWT token"},
        "jwt_forge":         {**rw, "description": "Forge a JWT token"},
        "jwt_crack":         {**rw, "description": "Crack JWT signing secret"},
        "oauth_audit":       {**rw, "description": "Audit OAuth flow"},
        "graphql_introspect": {**ro, "description": "Introspect GraphQL schema"},
        # ---- cloud ----
        "aws_cli":         {**rw, "description": "Run AWS CLI command"},
        "aws_imds":        {**ro, "description": "Query AWS instance metadata"},
        "gcp_cli":         {**rw, "description": "Run GCP CLI command"},
        "az_cli":          {**rw, "description": "Run Azure CLI command"},
        "kubectl":         {**rw, "description": "Run kubectl command"},
        "k8s_audit":       {**ro, "description": "Audit Kubernetes cluster"},
        "k8s_secrets_dump":{**rw, "description": "Dump Kubernetes secrets"},
        "terraform_scan":  {**ro, "description": "Scan Terraform config for issues"},
        # ---- containers ----
        "docker_audit":          {**ro, "description": "Audit Docker daemon/config"},
        "docker_escape_check":   {**rw, "description": "Check for Docker escape vectors"},
        "docker_image_scan":     {**ro, "description": "Scan Docker image for vulns"},
        "docker_history":        {**ro, "description": "Show Docker image layer history"},
        "k8s_pod_escape":        {**rw, "description": "Attempt Kubernetes pod escape"},
        "container_runtime_audit": {**ro, "description": "Audit container runtime security"},
        # ---- Active Directory ----
        "bloodhound_collect": {**rw, "description": "Collect AD data via BloodHound"},
        "kerberoast":         {**rw, "description": "Run Kerberoasting attack"},
        "asreproast":         {**rw, "description": "Run AS-REP roasting attack"},
        "dcsync":             {**rw, "description": "DCSync credential dump"},
        "adcs_audit":         {**rw, "description": "Audit AD Certificate Services"},
        "ldap_enum":          {**rw, "description": "Enumerate LDAP directory"},
        # ---- Android ----
        "apk_info":              {**ro, "description": "Extract APK metadata"},
        "apk_decompile":         {**ro, "description": "Decompile APK to smali"},
        "apk_decompile_java":    {**ro, "description": "Decompile APK to Java"},
        "android_manifest_audit":{**ro, "description": "Audit Android manifest"},
        "adb_shell":             {**rw, "description": "Run command via ADB shell"},
        "frida_inject":          {**rw, "description": "Inject Frida script into process"},
        # ---- Reversing ----
        "binary_info":     {**ro, "description": "Extract binary metadata"},
        "strings_extract": {**ro, "description": "Extract strings from binary"},
        "symbols_extract": {**ro, "description": "Extract symbols from binary"},
        "packer_detect":   {**ro, "description": "Detect binary packer/protector"},
        "rop_gadgets":     {**rw, "description": "Find ROP gadgets in binary"},
        "disassemble":     {**rw, "description": "Disassemble binary section"},
        # ---- References / lookups ----
        "payload_search":    {**ro, "description": "Search payload library"},
        "killchain_lookup":  {**ro, "description": "Look up kill-chain technique"},
        "killchain_suggest": {**ro, "description": "Suggest next kill-chain step"},
        "methodology_fetch": {**ro, "description": "Fetch methodology steps"},
        "oneliner_search":   {**ro, "description": "Search one-liner commands"},
        "h1_search":         {**ro, "description": "Search HackerOne corpus"},
        "cve_poc_lookup":    {**ro, "description": "Look up CVE PoC"},
        "cve_intel":         {**ro, "description": "Fetch CVE intelligence"},
        # ---- Findings ----
        "findings_add":    {**rw, "description": "Add a finding to the report"},
        "findings_list":   {**ro, "description": "List current findings"},
        "findings_export": {**rw, "description": "Export findings report"},
        # ---- Memory ----
        # memory_write / memory_update use ro (no edit textarea) to avoid the HITL
        # widget trying to render thousands of chars and cancelling.
        "memory_files_list": {**ro, "description": "List agent/target memory files"},
        "memory_read":       {**ro, "description": "Read a named memory file"},
        "memory_write":      {**ro, "description": _memory_write_desc},
        "memory_update":     {**ro, "description": _memory_update_desc},
        "memory_path":       {**rw, "description": "Resolve absolute path of a memory file"},
        # ---- OPPLAN ----
        "opplan_init":     {**rw, "description": "Initialise operation plan"},
        "opplan_add":      {**rw, "description": "Add objective to op-plan"},
        "opplan_get":      {**ro, "description": "Get op-plan objective"},
        "opplan_list":     {**ro, "description": "List op-plan objectives"},
        "opplan_update":   {**rw, "description": "Update op-plan objective"},
        "opplan_expand":   {**rw, "description": "Expand op-plan objective"},
        "opplan_collapse": {**rw, "description": "Collapse op-plan objective"},
        "opplan_save":     {**rw, "description": "Save op-plan to disk"},
        "opplan_load":     {**ro, "description": "Load op-plan from disk"},
    }


# ---------------------------------------------------------------------------
# Subagent postprocessing
# ---------------------------------------------------------------------------


def _postprocess_subagents(
    subagents: list[dict],
    *,
    parent_api_key: str = "",
    parent_base_url: str = "",
) -> list[dict]:
    """Prepare subagent dicts before passing to create_deep_agent.

    Three things are handled here that the SDK cannot do automatically:

    1. Per-subagent config.toml — load ``~/.rai/agents/<name>/config.toml``
       so each subagent can have its own model, api_key, base_url, temperature,
       and max_tokens.  AGENTS.md explicit values take precedence; config.toml
       fills in what AGENTS.md leaves empty.

    2. LiteLLM model resolution — ``graph.py`` calls ``resolve_model(str)``
       which delegates to LangChain's ``init_chat_model``.  That function does
       NOT support LiteLLM's ``provider/model`` (slash) format.  We pre-resolve
       those strings to ``ChatLiteLLM`` instances so the SDK sees a
       ``BaseChatModel`` and returns it unchanged.

    3. Empty content sanitization — Bedrock rejects AIMessages with
       ``content=""`` (blank text block).  The parent's
       ``EmptyContentSanitizerMiddleware`` only wraps the parent agent loop.
       Each subagent runs its own ``create_agent`` loop built inside
       ``graph.py`` with a fresh middleware stack.  We inject the sanitizer as
       the innermost middleware on every subagent so it strips blank content
       blocks just before each model call, regardless of whether the subagent
       uses an inherited or custom model.
    """
    processed: list[dict] = []
    for raw in subagents:
        sa = dict(raw)  # shallow copy — don't mutate the original

        # AsyncSubAgent (graph_id) and CompiledSubAgent (runnable) are opaque to RAI —
        # pass them through unchanged; model resolution and middleware injection only
        # apply to plain SubAgent specs that the SDK will build from scratch.
        if not _is_spec_subagent(sa):
            processed.append(sa)
            continue

        name = sa.get("name", "")

        # Extract RAI-specific fields that aren't in the SubAgent TypedDict.
        # These come from AGENTS.md resolution (explicit or parent-inherited).
        agents_md_api_key = sa.pop("api_key", "") or ""
        agents_md_base_url = sa.pop("base_url", "") or ""

        # Load per-subagent config.toml (silently absent is fine)
        sub_cfg = load_agent_config(name) if name else None

        # Resolve effective credentials:
        # AGENTS.md explicit > config.toml > parent credentials > env vars (via _build_llm)
        effective_api_key = (
            agents_md_api_key
            or (sub_cfg.api_key if sub_cfg else "")
            or parent_api_key
        )
        effective_base_url = (
            agents_md_base_url
            or (sub_cfg.base_url if sub_cfg else "")
            or parent_base_url
        )
        effective_temperature = sub_cfg.temperature if sub_cfg else 0.7
        effective_max_tokens = sub_cfg.max_tokens if sub_cfg else 8192

        # Model resolution: AGENTS.md > config.toml > absent (inherits parent)
        model_val = sa.get("model")
        if model_val is None and sub_cfg and sub_cfg.model:
            model_val = sub_cfg.model
            sa["model"] = model_val

        # Pre-resolve ALL string models to BaseChatModel with credentials baked in.
        # LiteLLM format (provider/model) was already handled here; non-LiteLLM strings
        # (e.g. "anthropic:claude-sonnet-4-6") were passed through as raw strings and
        # resolved later by deepagents' create_agent() via init_chat_model() — WITHOUT
        # the api_key/base_url, causing auth failures.  _build_llm handles every format.
        if isinstance(model_val, str):
            try:
                sa["model"] = _build_llm(
                    model_val,
                    api_key=effective_api_key,
                    base_url=effective_base_url,
                    temperature=effective_temperature,
                    max_tokens=effective_max_tokens,
                )
            except Exception as exc:
                logger.warning(
                    "Could not resolve model '%s' for subagent '%s': %s — "
                    "falling back to parent model",
                    model_val, name, exc,
                )
                sa.pop("model", None)  # fall back to parent's resolved_model

        # Inject EmptyContentSanitizerMiddleware as the innermost middleware.
        # graph.py builds: [Todo, Filesystem, Summarization, PatchToolCalls]
        # + spec["middleware"] + [AnthropicPromptCaching]
        # Appending here places it between PatchToolCalls and AnthropicPromptCaching,
        # which is close enough to the LLM call to strip empty blocks before Bedrock
        # sees them.
        existing_mw: list = list(sa.get("middleware", []))
        sa["middleware"] = existing_mw + [EmptyContentSanitizerMiddleware()]

        processed.append(sa)
    return processed


# ---------------------------------------------------------------------------
# Main factory
# ---------------------------------------------------------------------------


def create_rai_agent(
    model: str | BaseChatModel,
    agent_name: str = DEFAULT_AGENT_NAME,
    *,
    # Feature flags
    extra_tools: list[BaseTool] | None = None,
    system_prompt: str | None = None,
    system_prompt_extra: str | None = None,
    interactive: bool = True,
    auto_approve: bool = False,
    enable_memory: bool = True,
    enable_skills: bool = True,
    enable_shell: bool = True,
    enable_audit_log: bool = True,
    disable_native_tools: bool = False,
    disable_subagents: bool = False,
    custom_subagents: list[AsyncSubAgent | CompiledSubAgent | dict] | None = None,
    hooks_config_path: str | None = None,
    extra_middleware: list[Any] | None = None,
    custom_backend: Any = None,
    # Per-agent config (loaded from AGENTS.md + config.toml if None)
    agent_config: AgentConfig | None = None,
    # CLI overrides — take highest priority over AGENTS.md and config.toml
    api_key: str = "",
    base_url: str = "",
    # Per-target memory scope (optional)
    target: str = "",
    # Rate limiting profile: 'aggressive', 'normal', 'stealth'
    rate_limit_profile: str = "",
    # Per-subagent extra tools (e.g. from ~/.rai/agents/<subagent_name>/mcp.json)
    subagent_tools_map: dict[str, list[BaseTool]] | None = None,
    # Infrastructure
    checkpointer: BaseCheckpointSaver | None = None,
    mcp_server_info: list[MCPServerInfo] | None = None,
    cwd: Path | None = None,
    # HTTP harness mode — skip LocalAsyncAgentMiddleware so its tool names
    # don't collide with the renamed HTTP subagent tools
    suppress_local_async: bool = False,
    # HTTP harness mode — skip opplan tools + middleware (replaced by plan mode tools)
    disable_opplan: bool = False,
    # Subagent flag — skip OPPLAN middleware (subagents don't manage engagement objectives)
    is_subagent: bool = False,
    # RTK — disable `rtk rewrite` command rewriting middleware
    disable_rtk: bool = False,
) -> tuple[Pregel, CompositeBackend]:
    """Create a RAI agent using the deepagents SDK.

    Args:
        model: LangChain model or model string.
               Supported formats:
                 - 'anthropic:claude-sonnet-4-6'  (LangChain provider, default)
                 - 'openai/gpt-4o'                (LiteLLM native)
                 - 'litellm:anthropic/claude-3-5-sonnet-20241022'  (explicit prefix)
        agent_name: Agent identifier — used for memory, skills, and subagent paths
        extra_tools: Additional BaseTool instances to include
        system_prompt: Fully replace the default RAI system prompt.
        system_prompt_extra: Append additional instructions after the resolved system
            prompt (default or overridden).  Use this when you want to keep the full
            RAI persona and just inject extra context (e.g. engagement scope, custom
            rules, product-specific knowledge).  Not exposed via the CLI.
        interactive: Tailors system prompt for interactive vs headless mode
        disable_native_tools: When True, skip all RAI built-in tool loading (security,
            bash, memory, opplan, web, cloud, AD, reversing, android, container).
            The agent receives only tools from extra_tools.  Use this when embedding
            RAI with a fully custom toolset.  Not exposed via the CLI.
        disable_subagents: When True, skip all subagent loading (TOML files, AGENTS.md,
            and LocalAsyncAgentMiddleware).  The main agent gets all tools and memory but
            no delegation capability.  Not exposed via the CLI.
        hooks_config_path: Path to an extra hooks.json file whose entries are merged
            ON TOP of the default ~/.rai/hooks.json and ~/.claude/settings.json configs.
            Each RAI agent in a multi-agent deployment can have its own hook rules.
            Not exposed via the CLI.
        custom_subagents: Explicit list of subagent dicts to inject.  Each dict must
            have at minimum {"name": str, "description": str, "system_prompt": str}.
            When disable_subagents=False (default) these are merged with AGENTS.md
            subagents (custom_subagents take precedence on name collision).
            When disable_subagents=True only these subagents are used — AGENTS.md and
            TOML files are skipped entirely.  Not exposed via the CLI.
        auto_approve: Disable all HITL interrupts (autonomous mode)
        enable_memory: Load per-agent memory from ~/.rai/agents/<name>/memories/
        enable_skills: Load skills from ~/.rai/skills/ and agent skills dir
        enable_shell: Provide shell execution via LocalShellBackend
        enable_audit_log: Write all tool calls to ~/.rai/audit.log
        checkpointer: LangGraph checkpointer for session persistence
        mcp_server_info: MCP server metadata (for system prompt display)
        cwd: Working directory override

    Returns:
        (agent_graph, composite_backend)
    """
    effective_cwd = cwd or Path.cwd()

    # HTTP mode (suppress_local_async=True) never uses OPPLAN — plan mode replaces it.
    # Enforce automatically so no HTTP code path can accidentally enable OPPLAN.
    if suppress_local_async:
        disable_opplan = True

    # Install default subagents (researcher, coder, agent-creator) on first run.
    # Idempotent — skipped if AGENTS.md already exists.
    ensure_default_agents(agent_name)

    # Seed bundled skills (skill-creator) to ~/.rai/skills/ on first run.
    # Idempotent — existing files are never overwritten.
    ensure_default_skills()

    # ---- Per-agent config ----
    cfg = agent_config if agent_config is not None else load_agent_config(agent_name)

    # CLI --api-key / --base-url override everything (highest priority)
    if api_key:
        cfg.api_key = api_key
    if base_url:
        cfg.base_url = base_url

    # Bridge RAI config credentials into os.environ so ConfigurableModelMiddleware
    # (deepagents-cli) can find them. That middleware calls deepagents-cli's
    # create_model() on every runtime model-context swap, which reads ONLY os.environ —
    # it has no access to RAI's config.toml. Without this, any runtime model switch
    # (e.g. /model command, TUI model picker, HTTP context injection) rebuilds the
    # LLM using the wrong or missing credentials, hitting an expired/wrong key.
    # Force-assign: config.toml api_key always wins unless an explicit non-empty
    # env var was set BEFORE the process started (CLI env overrides).
    # setdefault would silently skip empty-string env vars that block the real key.
    if cfg.api_key:
        if not os.environ.get("LITELLM_API_KEY"):
            os.environ["LITELLM_API_KEY"] = cfg.api_key
        if not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = cfg.api_key
    if cfg.base_url:
        if not os.environ.get("LITELLM_BASE_URL"):
            os.environ["LITELLM_BASE_URL"] = cfg.base_url
        if not os.environ.get("OPENAI_BASE_URL"):
            os.environ["OPENAI_BASE_URL"] = cfg.base_url

    # Per-agent config model overrides CLI model only when CLI still has the bare default
    # and config has an explicit model set.  CLI --model always wins over config.
    effective_model: str | BaseChatModel
    if isinstance(model, str) and model == DEFAULT_MODEL and cfg.model:
        effective_model = cfg.model
    else:
        effective_model = model

    # Resolve to a BaseChatModel (handles LiteLLM and passthrough for langchain strings)
    resolved_model = _build_llm(
        effective_model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
    )

    # Ensure directories exist
    settings.ensure_agent_dir(agent_name)
    settings.ensure_hooks_config()

    # ---- Backend setup ----
    if custom_backend is not None:
        # Caller supplied a fully-built CompositeBackend — use it directly.
        # For LocalContextMiddleware we need the raw (non-composite) backend.
        composite_backend = custom_backend
        backend: Any = getattr(custom_backend, "default", custom_backend)
    else:
        if enable_shell:
            shell_env = os.environ.copy()
            backend = LocalShellBackend(
                root_dir=effective_cwd,
                inherit_env=True,
                env=shell_env,
            )
        else:
            backend = FilesystemBackend(root_dir=effective_cwd)

        # Route large results to temp dirs (same pattern as deepagents-cli)
        large_results_backend = FilesystemBackend(
            root_dir=tempfile.mkdtemp(prefix="rai_large_results_"),
            virtual_mode=True,
        )
        conversation_history_backend = FilesystemBackend(
            root_dir=tempfile.mkdtemp(prefix="rai_conversation_history_"),
            virtual_mode=True,
        )
        composite_backend = CompositeBackend(
            default=backend,
            routes={
                "/large_tool_results/": large_results_backend,
                "/conversation_history/": conversation_history_backend,
            },
        )

    # ---- Middleware stack ----
    # Order matters: first = outermost wrapper, last = innermost (closest to LLM).
    agent_middleware: list[Any] = []

    # Patch AnthropicPromptCachingMiddleware._cache_control to emit no ttl.
    # Anthropic defaults to 1h when ttl is absent — Claude Code never sends ttl.
    # deepagents unconditionally appends its own AnthropicPromptCachingMiddleware
    # so we patch the class property at factory init time to affect all instances.
    try:
        from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware as _APCM
        _APCM._cache_control = property(lambda self: {"type": self.type})  # type: ignore[method-assign]
    except Exception:
        pass

    # 0. Configurable model — enables per-request model overrides via LangGraph runtime
    #    context (no-op when no CLIContext is present on the runtime).
    try:
        from deepagents_cli.configurable_model import ConfigurableModelMiddleware  # type: ignore[import-not-found]
        agent_middleware.append(ConfigurableModelMiddleware())
    except ImportError:
        logger.debug("deepagents-cli not installed; skipping ConfigurableModelMiddleware")

    # 2. Audit logging
    if enable_audit_log:
        agent_middleware.append(AuditLogMiddleware(log_path=settings.ensure_audit_log()))

    # 3. Hooks — Claude Code–compatible PreToolUse/PostToolUse/model lifecycle hooks.
    #    Sits after audit log (audit records all calls) but before execute interceptor
    #    (hooks see the original tool name, e.g. "execute" or "bash").
    agent_middleware.append(HooksMiddleware(extra_config_path=hooks_config_path))

    # 3b. RTK — rewrites bash commands via `rtk rewrite` before execution.
    #     Equivalent to the `rtk hook claude` PreToolUse hook in Claude Code.
    #     No-ops silently when rtk is not installed or the command has no equivalent.
    if not disable_rtk:
        agent_middleware.append(RTKToolMiddleware())

    # 3.5. Loop detection — prevents degenerate re-execution of identical tool calls.
    #      When the agent re-derives the same bash/grep/read_file call under context
    #      pressure, returns the cached result immediately with a ⚠ warning instead
    #      of executing again. Stops 50× same-command loops like the grep degenerate.
    from rai.middleware.loop_detection import LoopDetectionMiddleware
    agent_middleware.append(LoopDetectionMiddleware(
        window=int(os.environ.get("RAI_LOOP_WINDOW", 10)),
    ))

    # 4. Execute interceptor — routes deepagents' built-in execute tool through
    #    RAI BashTool (env isolation, stderr labelling, /tmp spill, allowlist).
    agent_middleware.append(ExecuteInterceptorMiddleware())

    # 4.5. Rate limiting — per-tool minimum delay (CLI > agent config > env > default)
    effective_rate = rate_limit_profile or cfg.rate_limit_profile or settings.rate_limit_profile
    if effective_rate != "aggressive":
        from rai.middleware.ratelimit import RateLimitMiddleware
        agent_middleware.append(RateLimitMiddleware(profile=effective_rate))

    # 5. Ask user — injects ask_user tool so the agent can pause and ask the user
    #    mid-task; uses langgraph interrupt() and the TUI AskUserMenu to collect answers.
    try:
        from deepagents_cli.ask_user import AskUserMiddleware  # type: ignore[import-not-found]
        agent_middleware.append(AskUserMiddleware())
    except ImportError:
        logger.debug("deepagents-cli not installed; skipping AskUserMiddleware")

    # 6. Memory
    if enable_memory:
        # Global profile loads first — every agent sees who the user is before
        # reading its own memory.  Agent-specific memory loads second so it can
        # supplement or override the global context.  Target memory loads last.
        global_paths = settings.ensure_global_user_profile()
        memory_paths = settings.ensure_memory_files(agent_name)
        # When a target is active, append target-scoped memory files.
        # Agent level: user, feedback, engagement, target_overview, findings, methodology (general).
        # Target level: engagement, recon, findings, notes, methodology (target-specific).
        # Both methodology files load — general first, then target-specific on top.
        if target:
            target_paths = settings.ensure_target_memory(target)
            memory_paths = memory_paths + target_paths
        project_md_paths = settings.get_project_agent_md_paths(cwd=effective_cwd)
        all_memory_sources = (
            [str(p) for p in global_paths]
            + [str(p) for p in memory_paths]
            + [str(p) for p in project_md_paths]
        )
        # Self-learning loop: load lessons.md if written by write_lesson() / selflearn harness
        _lessons_path = Path.home() / ".rai" / "agents" / agent_name / "memory" / "lessons.md"
        if _lessons_path.exists():
            all_memory_sources.append(str(_lessons_path))

        # RAIMemoryMiddleware: inlines small files (≤4k chars) directly into the system
        # prompt; lists large files as index entries with their path and size so the agent
        # knows they exist and can read_file them on demand. This prevents the
        # deepagents MemoryMiddleware from injecting multi-MB engagement files on every call.
        agent_middleware.append(RAIMemoryMiddleware(sources=all_memory_sources))

    # 7. Skills
    if enable_skills:
        sources: list[str] = []
        user_skills, agent_skills = settings.ensure_skills_dirs(agent_name)
        sources.extend([str(user_skills), str(agent_skills)])
        project_skills = settings.get_project_skills_dir(cwd=effective_cwd)
        if project_skills:
            sources.append(str(project_skills))
        claude_user_skills = settings.get_user_claude_skills_dir()
        if claude_user_skills.exists():
            sources.append(str(claude_user_skills))
        claude_project_skills = settings.get_project_claude_skills_dir(cwd=effective_cwd)
        if claude_project_skills:
            sources.append(str(claude_project_skills))
        agent_middleware.append(SkillsMiddleware(backend=FilesystemBackend(), sources=sources))

    # 8. Local context (security-aware env detection)
    try:
        from deepagents_cli.local_context import LocalContextMiddleware  # type: ignore[import-not-found]
        from deepagents_cli.mcp_tools import MCPServerInfo as CLIMCPServerInfo  # type: ignore[import-not-found]

        agent_middleware.append(
            LocalContextMiddleware(
                backend=backend,
                mcp_server_info=[
                    CLIMCPServerInfo(name=s.name, transport=s.transport)
                    for s in (mcp_server_info or [])
                ],
            )
        )
    except ImportError:
        logger.debug("deepagents-cli not installed; skipping LocalContextMiddleware")

    # 8.5. Static-prefix cache breakpoint — stamps cache_control on the current last
    #      system-message block so the ~16,700-token stable prefix can be cached
    #      independently of the volatile tail (Findings + OPPLAN).  Without this,
    #      every OPPLAN status update or new finding busts the entire system-prompt
    #      cache and re-charges all ~17,000 tokens.  With it, only the ~700-token
    #      dynamic tail misses; the static prefix still hits (~95% cost reduction on
    #      volatile turns).  No-ops silently for non-Anthropic/non-Claude models.
    try:
        from rai.middleware.cache_split import StaticSystemPromptCacheBreakpointMiddleware
        # ttl="1h" — Anthropic's default when omitted; matches Claude Code (no ttl sent).
        agent_middleware.append(
            StaticSystemPromptCacheBreakpointMiddleware(unsupported_model_behavior="ignore", ttl="1h")
        )
    except ImportError:
        logger.debug("cache_split not available; skipping StaticSystemPromptCacheBreakpointMiddleware")

    # 9. Findings enrichment (keep findings count visible to LLM)
    agent_middleware.append(FindingsEnrichmentMiddleware())

    # 10. OPPLAN — engagement objective tracker for all security disciplines
    #     Skipped for subagents (is_subagent=True) — they don't manage engagement objectives.
    #     Also skipped for HTTP harness parent agent (disable_opplan=True) — replaced by plan mode.
    if not disable_opplan and not is_subagent:
        from rai.middleware.opplan import OPPLANMiddleware  # lazy — avoids circular import
        agent_middleware.append(OPPLANMiddleware(agent_name=agent_name))

    # 10b. Plan mode interceptor — redirects write_todos to write_plan() in plan-mode HTTP runs.
    #      Only active when disable_opplan=True (HTTP harness parent agent, not subagents).
    if disable_opplan and not is_subagent:
        from rai.middleware.plan_mode import PlanModeMiddleware  # lazy
        agent_middleware.append(PlanModeMiddleware())

    # 10c. Model override — per-call model switching via RAI_MODEL_OVERRIDE env var
    from rai.middleware.model_override import ModelOverrideMiddleware  # lazy
    agent_middleware.append(ModelOverrideMiddleware())


    # 10.5. Message compression — trim history to ~30k tokens before model call.
    #        Zero LLM cost. Pre-filter: reduces token cost on every call and gives
    #        SummarizationMiddleware (layer 11) a smaller window to work with.
    #        Order is correct: compress first → then summarize if still too large.
    #        The compression.py _char_estimate() fix (includes tool_call args) ensures
    #        this now accurately measures the 75k char budget.
    agent_middleware.append(MessageCompressionMiddleware())

    # 10.7. Tool result compaction — truncate old bash/file/http results and bash
    #        command args. Zero LLM cost. Never touches: human messages (carry
    #        <system-reminder> plan enforcement + subagent notifications), plan tools,
    #        findings, memory, ask_user. Saves 60-80% context on long security sessions.
    from rai.middleware.tool_compaction import ToolResultCompressionMiddleware
    agent_middleware.append(ToolResultCompressionMiddleware(
        keep_recent=int(os.environ.get("RAI_COMPACT_RESULT_KEEP", 20)),
        max_result_chars=int(os.environ.get("RAI_COMPACT_RESULT_MAX", 1500)),
        max_cmd_chars=int(os.environ.get("RAI_COMPACT_CMD_MAX", 600)),
        max_findings_arg_chars=int(os.environ.get("RAI_COMPACT_FINDINGS_ARG_MAX", 250)),
    ))

    # 11. Summarization tool + early-fire safety valve.
    #
    # create_deep_agent adds its own SummarizationMiddleware internally (auto-compact at 85%
    # of the model's context window). That middleware uses the default 4 chars/token counter
    # which severely under-counts code-heavy security workloads (actual ~2.5 chars/token),
    # causing it to fire too late or not at all → context window crash.
    #
    # We add a SECOND SummarizationMiddleware here with:
    #   • calibrated 2.5 chars/token counter + usage_metadata scaling
    #   • earlier dual trigger: RAI_COMPACT_TOKEN_TRIGGER tokens OR
    #     RAI_COMPACT_MSG_TRIGGER messages (whichever fires first)
    #   • keep last RAI_COMPACT_KEEP messages after compaction
    #   • trim_tokens_to_summarize=None so the full message set is sent to the summary LLM
    #     (default 4000 drops 95%+ of a security session before summarization)
    #   • truncate_args_settings clips old write_file/edit_file payloads at
    #     RAI_COMPACT_TRUNCATE_AT messages with max RAI_COMPACT_TRUNCATE_MAX chars
    #
    # Env vars (all optional, integers):
    #   RAI_COMPACT_MSG_TRIGGER    — message count that fires compaction       (default: 40)
    #   RAI_COMPACT_TOKEN_TRIGGER  — token count that fires compaction         (default: 100000)
    #   RAI_COMPACT_KEEP           — messages kept after compaction            (default: 20)
    #   RAI_COMPACT_TRUNCATE_AT    — message depth to start arg truncation     (default: 30)
    #   RAI_COMPACT_TRUNCATE_MAX   — max chars per tool arg after truncation   (default: 2000)
    #
    # Wrapped in SummarizationToolMiddleware which also exposes the /compact manual command.
    try:
        from functools import partial
        from langchain_core.messages.utils import count_tokens_approximately as _cta
        _calibrated_counter = partial(
            _cta, chars_per_token=2.5, use_usage_metadata_scaling=True
        )
        _msg_trigger      = int(os.environ.get("RAI_COMPACT_MSG_TRIGGER",   40))
        _tok_trigger      = int(os.environ.get("RAI_COMPACT_TOKEN_TRIGGER", 100_000))
        _keep             = int(os.environ.get("RAI_COMPACT_KEEP",          20))
        _truncate_at      = int(os.environ.get("RAI_COMPACT_TRUNCATE_AT",   30))
        _truncate_max     = int(os.environ.get("RAI_COMPACT_TRUNCATE_MAX",  2000))
        # Summarization model: use cfg.compact_model if set, else fall back to
        # resolved_model (main agent model). Empty = inherit parent model/key/url.
        # Configure via: rai agents config-set --compact-model "litellm:openai/bedrock-claude-haiku-4.5-(US)"
        # or env var: RAI_COMPACT_MODEL
        _summ_model = resolved_model  # default: same as main agent
        _compact_model_str = (
            os.environ.get("RAI_COMPACT_MODEL", "")   # env var wins
            or cfg.compact_model                       # config.toml compact_model
        )
        if _compact_model_str:
            try:
                _compact_api_key  = cfg.compact_api_key  or cfg.api_key   # inherit if empty
                _compact_base_url = cfg.compact_base_url or cfg.base_url  # inherit if empty
                _summ_model = _build_llm(
                    _compact_model_str,
                    api_key=_compact_api_key,
                    base_url=_compact_base_url,
                    temperature=0.5,
                    max_tokens=4096,
                )
                logger.debug(
                    "Using compact model for summarization: %s (api_key=%s, base_url=%s)",
                    _compact_model_str,
                    bool(_compact_api_key),
                    bool(_compact_base_url),
                )
            except Exception:
                logger.debug("Failed to build compact model, falling back to main model", exc_info=True)
                _summ_model = resolved_model

        _summ_auto = SummarizationMiddleware(
            model=_summ_model,
            backend=composite_backend,
            trigger=[("tokens", _tok_trigger), ("messages", _msg_trigger)],
            keep=("messages", _keep),
            token_counter=_calibrated_counter,
            trim_tokens_to_summarize=None,
            truncate_args_settings={
                "trigger": ("messages", _truncate_at),
                "keep":    ("messages", _keep),
                "max_length": _truncate_max,
                "truncation_text": "...(truncated)",
            },
        )
        agent_middleware.append(SummarizationToolMiddleware(_summ_auto))  # exposes compact_conversation tool
    except Exception:
        logger.debug("SummarizationToolMiddleware unavailable; skipping manual compact", exc_info=True)

    # 11.5. Prompt caching — tags system message, tools, and last-turn content with
    #        RAIPromptCachingMiddleware disabled — deepagents unconditionally appends
    #        AnthropicPromptCachingMiddleware at its tail which handles system[-1] and
    #        tools. Having both caused _should_apply_caching conflicts and prevented
    #        StaticSystemPromptCacheBreakpointMiddleware from tagging system[0].

    # User-supplied extra middleware — runs after all RAI built-ins, before final sanitizer.
    if extra_middleware:
        agent_middleware.extend(extra_middleware)

    # 12a. Model-call debug logger — activated by RAI_DEBUG_LOG_CALLS=1.
    import os as _os
    if _os.environ.get("RAI_DEBUG_LOG_CALLS"):
        from rai.middleware.model_logger import ModelCallLoggerMiddleware
        agent_middleware.append(ModelCallLoggerMiddleware())

    # 12b. Request inspector — logs full request + optional MITM proxy (RAI_INSPECT=1).
    if _os.environ.get("RAI_INSPECT"):
        from rai.middleware.request_inspector import RequestInspectorMiddleware
        agent_middleware.append(RequestInspectorMiddleware())

    # 12c. Cache TTL upgrade — strips 'ttl' from all cache_control blocks.
    #      AnthropicPromptCachingMiddleware defaults ttl="5m"; removing it lets
    #      Anthropic default to 1h — matching Claude Code (no ttl sent).
    from rai.middleware.cache_ttl import CacheControlTTLUpgradeMiddleware
    agent_middleware.append(CacheControlTTLUpgradeMiddleware())

    # 12d. Last human message cache — stamps cache_control: ephemeral on the last
    #      HumanMessage's last content block so the full conversation history is
    #      cached on the next turn. Matches Claude Code's per-turn caching strategy.
    from rai.middleware.message_cache import LastHumanMessageCacheMiddleware
    agent_middleware.append(LastHumanMessageCacheMiddleware())

    # 12. Empty content sanitizer — must be last (innermost) so it runs just before
    #     serialisation; strips empty text blocks that Bedrock rejects.
    agent_middleware.append(EmptyContentSanitizerMiddleware())

    # ---- Tools ----
    tools: list[BaseTool] = []
    if not disable_native_tools:
        tools.extend(get_security_tools())
        if not is_subagent:
            from rai.tools.security.security import CreateSubagentTool
            tools.append(CreateSubagentTool())
        tools.extend(get_builtin_tools())
        tools.extend(get_memory_tools(agent_name, target))
        if not disable_opplan and not is_subagent:
            from rai.tools.core.opplan import get_opplan_tools  # lazy — avoids circular import
            tools.extend(get_opplan_tools(agent_name))
        from rai.tools.core.references import get_reference_tools  # lazy
        from rai.tools.web.web import get_web_tools  # lazy
        from rai.tools.cloud.cloud import get_cloud_tools  # lazy
        from rai.tools.active_directory.ad import get_ad_tools  # lazy
        from rai.tools.reversing.reversing import get_reversing_tools  # lazy
        from rai.tools.android.android import get_android_tools  # lazy
        from rai.tools.container.container import get_container_tools  # lazy
        tools.extend(get_reference_tools())
        tools.extend(get_web_tools())
        tools.extend(get_cloud_tools())
        tools.extend(get_ad_tools())
        tools.extend(get_reversing_tools())
        tools.extend(get_android_tools())
        tools.extend(get_container_tools())
    tools.extend(extra_tools or [])

    # ---- Subagents ----
    _resolved_subagents: list[Any] = []
    if not disable_subagents or custom_subagents:
        if disable_subagents and custom_subagents:
            # disable_subagents=True + explicit list → use only the caller's subagents
            _resolved_subagents = [dict(s) for s in custom_subagents]
        else:
            # Load from TOML + AGENTS.md, then merge caller-supplied subagents on top
            toml_subagents = load_custom_subagents(agent_name)
            agents_md_subagents = load_subagents_for(
                agent_name,
                parent_api_key=cfg.api_key,
                parent_base_url=cfg.base_url,
            )
            # Deduplicate: AGENTS.md wins over TOML for the same name
            agents_md_names = {s["name"] for s in agents_md_subagents}
            _resolved_subagents = (
                [s for s in toml_subagents if s["name"] not in agents_md_names]
                + agents_md_subagents
            )
            # Caller-supplied subagents take precedence over AGENTS.md
            if custom_subagents:
                existing_names = {s["name"] for s in custom_subagents}
                _resolved_subagents = (
                    [s for s in _resolved_subagents if s["name"] not in existing_names]
                    + [dict(s) for s in custom_subagents]
                )

        # Attach per-subagent extra tools (e.g. from ~/.rai/agents/<name>/mcp.json)
        # Only applies to plain SubAgent specs; AsyncSubAgent/CompiledSubAgent are skipped.
        if subagent_tools_map:
            for subagent in _resolved_subagents:
                if not _is_spec_subagent(subagent):
                    continue
                sname = subagent["name"]
                if sname in subagent_tools_map and subagent_tools_map[sname]:
                    existing: list = list(subagent.get("tools", []))
                    subagent["tools"] = existing + list(subagent_tools_map[sname])

        # Inject memory tools into plain SubAgent specs so they can read/write their own
        # memory and target memory without hallucinating filesystem paths.
        # AsyncSubAgent and CompiledSubAgent are external/pre-built — skip them.
        for subagent in _resolved_subagents:
            if not _is_spec_subagent(subagent):
                continue
            sname = subagent["name"]
            existing = list(subagent.get("tools", []))
            subagent["tools"] = existing + get_memory_tools(sname, target)

        # Pre-resolve LiteLLM models and inject EmptyContentSanitizerMiddleware into
        # every subagent so Bedrock's blank-text-block rejection is handled inside
        # each subagent's own agent loop, not just the parent's loop.
        _resolved_subagents = _postprocess_subagents(
            _resolved_subagents,
            parent_api_key=cfg.api_key,
            parent_base_url=cfg.base_url,
        )

        # Non-blocking background/parallel agent tools — launch subagents via
        # asyncio.create_task() so the core agent is not blocked.
        # Skipped in HTTP harness mode where renamed HTTP tools fill this role.
        if _resolved_subagents and not suppress_local_async:
            agent_middleware.append(
                LocalAsyncAgentMiddleware(
                    subagents=_resolved_subagents,
                    default_tools=tools,
                    default_model=resolved_model,
                    backend=composite_backend,
                    checkpointer=checkpointer,
                    parent_api_key=cfg.api_key,
                    parent_base_url=cfg.base_url,
                )
            )

    # ---- System prompt ----
    if system_prompt is None:
        # Priority: ~/.rai/agents/<name>/prompt.md → slim subagent default → full RAI prompt
        # Custom prompt.md lets users give specific subagents a richer-than-slim but
        # cheaper-than-full prompt. Falls through to the 500-token slim default for
        # generic subagents, saving ~14,000 tokens per turn vs the 14,500-token full prompt.
        system_prompt = get_agent_prompt(agent_name, is_subagent=is_subagent)
    if system_prompt_extra:
        system_prompt = system_prompt + "\n\n" + system_prompt_extra

    # ---- Interrupt on (HITL) ----
    if auto_approve:
        interrupt_on: dict[str, Any] | None = {}
    else:
        interrupt_on = _build_interrupt_on()  # type: ignore[assignment]

    # ---- Conservative context profile (fixes broken token counter in auto-summarization) ----
    # create_deep_agent adds its own SummarizationMiddleware using compute_summarization_defaults().
    # For models with max_input_tokens in their profile, it uses fraction-based trigger (0.85).
    # The internal counter defaults to 4 chars/token — severely underestimates code-heavy content.
    # Setting max_input_tokens=120000 makes it trigger at 0.85×120k=102k "approximate" tokens,
    # which at 4 chars/token = ~408k chars = ~163k real tokens — safely before the 200k hard limit.
    _conservative_ctx = 120000
    try:
        _prof = getattr(resolved_model, "profile", None)
        _current_max = _prof.get("max_input_tokens") if isinstance(_prof, dict) else None
        if not isinstance(_prof, dict) or not _current_max or _current_max > _conservative_ctx:
            resolved_model.profile = {
                **(_prof if isinstance(_prof, dict) else {}),
                "max_input_tokens": _conservative_ctx,
            }
    except Exception:
        pass  # model object may be immutable; internal summarization uses fixed token fallback

    # ---- Create agent ----
    agent = create_deep_agent(
        model=resolved_model,
        system_prompt=system_prompt,
        tools=tools,
        backend=composite_backend,
        middleware=agent_middleware,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        subagents=_resolved_subagents or None,
        name=agent_name,
    )

    return agent, composite_backend
