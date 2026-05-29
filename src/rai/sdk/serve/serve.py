"""rai.serve — programmatic LangGraph serve API for SDK users.

Two main entry points:

  RAIAgentBuilder.serve(config)
      For agents built entirely from serializable builder state
      (model string, agent name, feature flags, system prompt, custom subagents
      as dicts, tools from importable modules, CompositeBackend + FilesystemBackend
      routes).  Raises ServeError with a helpful message if the builder holds
      non-serializable objects.

  serve_module(module_ref, config, env, ...)
      For agents with custom backends, custom middleware, or tools defined in
      __main__.  ``module_ref`` is either a ``"path/to/file.py:symbol"`` string
      or a Python callable.  The symbol / return-value must be a compiled
      LangGraph graph (Pregel).  A thin wrapper module is generated so
      ``langgraph dev`` can import it.

Both functions block until Ctrl-C, mirroring ``rai serve`` behaviour.

Example usage::

    from rai.sdk import RAIAgent, ServeConfig, serve_module

    # Simple case — all state serializable
    (RAIAgent.builder()
        .model("anthropic:claude-sonnet-4-6")
        .agent_name("pentest")
        .without_hitl()
        .serve(ServeConfig.prod(port=8080, host="0.0.0.0")))

    # Complex case — custom backend
    serve_module(
        "examples/10_threat_modeling.py:graph",
        ServeConfig(port=2024),
        env={"TM_PROJECT_NAME": "myapp", "TM_PROJECT_PATH": "/src/myapp"},
    )

    # Callable factory
    serve_module(build_my_graph, ServeConfig(port=2024))
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NoReturn


# ---------------------------------------------------------------------------
# ServeConfig
# ---------------------------------------------------------------------------


@dataclass
class ServeConfig:
    """Configuration for serving an RAI agent via the LangGraph API.

    Attributes:
        port:        Port to bind (default 2024 — LangGraph's default).
        host:        Network interface (default ``127.0.0.1``; use ``0.0.0.0`` to
                     bind all interfaces).
        reload:      Enable hot-reload (``True`` = dev mode, ``False`` = prod mode).
        workers:     Max concurrent jobs per worker process.
        browser:     Open LangSmith Studio in the browser on startup.
        tunnel:      Expose via Cloudflare public tunnel (requires internet).
        log_level:   Server log level passed to ``langgraph dev``.
        debug_port:  Enable remote Python debugger on this port when set.
        env_file:    Path to a ``.env`` file loaded by the ``langgraph dev``
                     subprocess.
        hitl:        Enable human-in-the-loop approval prompts. Requires an
                     interactive client (e.g. LangSmith Studio).
    """

    port: int = 2024
    host: str = "127.0.0.1"
    reload: bool = True
    workers: int = 10
    browser: bool = True
    tunnel: bool = False
    log_level: str = "warning"
    debug_port: int | None = None
    env_file: str | None = None
    hitl: bool = False

    # ---------------------------------------------------------------- factories

    @classmethod
    def dev(cls, **kwargs: Any) -> "ServeConfig":
        """Dev preset: hot reload on, LangSmith Studio opened in browser."""
        kwargs.setdefault("reload", True)
        kwargs.setdefault("browser", True)
        return cls(**kwargs)

    @classmethod
    def prod(cls, **kwargs: Any) -> "ServeConfig":
        """Prod preset: no reload, no browser, info-level logging."""
        kwargs.setdefault("reload", False)
        kwargs.setdefault("browser", False)
        kwargs.setdefault("log_level", "info")
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# ServeError
# ---------------------------------------------------------------------------


class ServeError(Exception):
    """Raised when builder.serve() cannot serialize agent state.

    The error message explains which field cannot be serialized and points
    to ``serve_module()`` as the escape hatch.
    """


# ---------------------------------------------------------------------------
# Subprocess engine (shared by builder.serve and serve_module)
# ---------------------------------------------------------------------------


def _spawn_langgraph(
    config_path: Path,
    config: ServeConfig,
    env: dict[str, str],
    *,
    cwd: str | None = None,
) -> NoReturn:
    """Build the langgraph dev command, spawn it, and block until Ctrl-C."""
    cmd = [
        sys.executable, "-m", "langgraph_cli", "dev",
        "--config", str(config_path),
        "--host", config.host,
        "--port", str(config.port),
        "--n-jobs-per-worker", str(config.workers),
        "--server-log-level", config.log_level,
    ]
    if not config.reload:
        cmd.append("--no-reload")
    if not config.browser:
        cmd.append("--no-browser")
    if config.tunnel:
        cmd.append("--tunnel")
    if config.debug_port is not None:
        cmd.extend(["--debug-port", str(config.debug_port)])

    work_dir = cwd or str(config_path.parent)
    proc = subprocess.Popen(cmd, cwd=work_dir, env=env)  # noqa: S603

    def _shutdown(_sig: int, _frame: object) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    raise SystemExit(proc.wait())


def _base_serve_env() -> dict[str, str]:
    """Start from os.environ and strip LangSmith cloud keys."""
    env = os.environ.copy()
    env["LANGGRAPH_AUTH_TYPE"] = "noop"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in (
        "LANGGRAPH_AUTH",
        "LANGGRAPH_CLOUD_LICENSE_KEY",
        "LANGSMITH_CONTROL_PLANE_API_KEY",
        "LANGSMITH_TENANT_ID",
    ):
        env.pop(key, None)
    return env


# ---------------------------------------------------------------------------
# Tool / backend serialization helpers (used by builder.serve)
# ---------------------------------------------------------------------------


def _serialize_tool_ref(t: Any) -> str | None:
    """Return ``"module:attr"`` for a LangChain tool, or None if not serializable.

    Tries the underlying function's ``__module__`` + ``__name__`` first (works
    for ``@tool``-decorated functions), then falls back to the tool's own
    ``__module__``.  Returns None when the tool is defined in ``__main__`` or
    cannot be round-tripped through ``importlib``.
    """
    func = getattr(t, "func", None)
    module: str | None = (
        getattr(func, "__module__", None)
        if func is not None
        else getattr(t, "__module__", None)
    )
    # For @tool-decorated functions, func.__name__ == the Python variable name
    attr: str | None = (
        getattr(func, "__name__", None)
        if func is not None
        else getattr(t, "name", getattr(t, "__name__", None))
    )

    if not module or not attr or module == "__main__":
        return None

    # Verify round-trip: import the module and look up the attr
    try:
        mod = importlib.import_module(module)
        if getattr(mod, attr, None) is None:
            return None
    except Exception:
        return None

    return f"{module}:{attr}"


def _serialize_backend(backend: Any) -> dict | None:
    """Serialize a CompositeBackend(LocalShellBackend, FilesystemBackend routes)
    to a JSON-safe dict.  Returns None for any other backend type.
    """
    try:
        from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
    except ImportError:
        return None

    if not isinstance(backend, CompositeBackend):
        return None
    if not isinstance(backend.default, LocalShellBackend):
        return None

    routes: dict[str, str] = {}
    for vp, b in (backend.routes or {}).items():
        if not isinstance(b, FilesystemBackend):
            return None
        routes[str(vp)] = str(b.cwd)  # FilesystemBackend stores root as .cwd

    return {"type": "composite", "routes": routes}


# ---------------------------------------------------------------------------
# Graph template for RAIAgentBuilder.serve()
# ---------------------------------------------------------------------------

_BUILDER_GRAPH_TEMPLATE = textwrap.dedent("""\
    \"\"\"RAI SDK builder graph — auto-generated by RAIAgentBuilder.serve(). Do not edit.\"\"\"
    from __future__ import annotations
    import asyncio, importlib, json, os, sys
    from pathlib import Path

    _src = {src_path!r}
    if _src not in sys.path:
        sys.path.insert(0, _src)

    from rai.engine.factory import create_rai_agent
    from rai.agents.loader import load_subagents_for
    from rai.mcp.loader import resolve_and_load_mcp_tools, load_subagents_mcp_tools_map
    from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend

    # ── Core identity ────────────────────────────────────────────────────────
    _model      = os.environ.get("RAI_SERVE_MODEL",     {default_model!r})
    _agent      = os.environ.get("RAI_SERVE_AGENT",     "rai")
    _api_key    = os.environ.get("RAI_SERVE_API_KEY",   "")
    _base_url   = os.environ.get("RAI_SERVE_BASE_URL",  "")
    _target     = os.environ.get("RAI_SERVE_TARGET",    "")
    _rate_limit = os.environ.get("RAI_SERVE_RATE_LIMIT","")
    _cwd_str    = os.environ.get("RAI_SERVE_CWD",       "")
    _cwd        = Path(_cwd_str) if _cwd_str else Path.cwd()
    _hitl       = os.environ.get("RAI_SERVE_HITL",      "0") == "1"
    _no_mcp     = os.environ.get("RAI_SERVE_NO_MCP",    "0") == "1"

    # ── Extended builder state ────────────────────────────────────────────────
    _system_prompt  = os.environ.get("RAI_SERVE_SYSTEM_PROMPT",        "") or None
    _sp_extra       = os.environ.get("RAI_SERVE_SYSTEM_PROMPT_EXTRA",  "") or None
    _disable_subs   = os.environ.get("RAI_SERVE_DISABLE_SUBAGENTS",    "0") == "1"
    _disable_native = os.environ.get("RAI_SERVE_DISABLE_NATIVE_TOOLS", "0") == "1"
    _enable_memory  = os.environ.get("RAI_SERVE_ENABLE_MEMORY",  "1") == "1"
    _enable_shell   = os.environ.get("RAI_SERVE_ENABLE_SHELL",   "1") == "1"
    _enable_skills  = os.environ.get("RAI_SERVE_ENABLE_SKILLS",  "1") == "1"
    _enable_audit   = os.environ.get("RAI_SERVE_ENABLE_AUDIT",   "1") == "1"

    _custom_subagents = json.loads(os.environ.get("RAI_SERVE_CUSTOM_SUBAGENTS", "[]")) or None

    # ── Extra tools (importable module:attr references) ───────────────────────
    _extra_tools: list = []
    for _ref in json.loads(os.environ.get("RAI_SERVE_TOOLS", "[]")):
        _mod_name, _, _attr = _ref.rpartition(":")
        try:
            _extra_tools.append(getattr(importlib.import_module(_mod_name), _attr))
        except Exception as _e:
            print(f"[RAI serve] Warning: could not import tool {{_ref!r}}: {{_e}}", flush=True)

    # ── Custom backend spec ───────────────────────────────────────────────────
    _backend = None
    _backend_spec_raw = os.environ.get("RAI_SERVE_BACKEND_SPEC", "")
    if _backend_spec_raw:
        _spec = json.loads(_backend_spec_raw)
        if _spec.get("type") == "composite":
            _routes = {{
                vp: FilesystemBackend(root_dir=Path(rp), virtual_mode=True)
                for vp, rp in _spec.get("routes", {{}}).items()
            }}
            _backend = CompositeBackend(default=LocalShellBackend(), routes=_routes)

    # ── MCP loading (skipped when --no-mcp) ──────────────────────────────────
    async def _load_all_mcp():
        async def _main_mcp():
            try:
                return await resolve_and_load_mcp_tools(cwd=_cwd, agent_name=_agent)
            except Exception:
                return [], None, []

        async def _sub_mcp():
            try:
                defs = load_subagents_for(_agent)
                if not defs:
                    return {{}}, []
                names = [s["name"] for s in defs if s.get("name")]
                return await load_subagents_mcp_tools_map(names)
            except Exception:
                return {{}}, []

        return await asyncio.gather(_main_mcp(), _sub_mcp())

    def _build() -> object:
        mcp_tools: list = []
        mcp_info = None
        subagent_tools_map = None
        if not _no_mcp:
            (mcp_tools, _, mcp_info), (subagent_tools_map, _) = asyncio.run(_load_all_mcp())

        all_tools = _extra_tools + (mcp_tools or [])

        _g, _ = create_rai_agent(
            model=_model,
            agent_name=_agent,
            extra_tools=all_tools or None,
            mcp_server_info=mcp_info,
            subagent_tools_map=subagent_tools_map or None,
            system_prompt=_system_prompt,
            system_prompt_extra=_sp_extra,
            custom_subagents=_custom_subagents,
            custom_backend=_backend,
            api_key=_api_key,
            base_url=_base_url,
            target=_target,
            rate_limit_profile=_rate_limit,
            cwd=_cwd,
            interactive=False,
            auto_approve=not _hitl,
            disable_subagents=_disable_subs,
            disable_native_tools=_disable_native,
            enable_memory=_enable_memory,
            enable_shell=_enable_shell,
            enable_skills=_enable_skills,
            enable_audit_log=_enable_audit,
        )
        return _g

    graph = _build()
""")

# Wrapper template for serve_module() — wraps any user module/callable
_MODULE_WRAPPER_TEMPLATE = textwrap.dedent("""\
    \"\"\"serve_module wrapper — auto-generated. Do not edit.\"\"\"
    import sys as _sys
    for _p in {sys_paths!r}:
        if _p not in _sys.path:
            _sys.path.insert(0, _p)

    import importlib.util as _util
    _spec = _util.spec_from_file_location("_rai_user_graph", {module_path!r})
    _mod = _util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _obj = getattr(_mod, {symbol!r})
    # Support both a compiled graph variable and a zero-arg factory function
    graph = _obj() if callable(_obj) else _obj
""")


# ---------------------------------------------------------------------------
# Builder env serialization
# ---------------------------------------------------------------------------


def _build_env_from_builder(
    builder: Any,
    *,
    model: str,
    cwd: str,
    config: ServeConfig,
) -> dict[str, str]:
    """Serialize RAIAgentBuilder state into the RAI_SERVE_* env var map.

    Raises ServeError if any state cannot be serialized (e.g. tool defined in
    __main__, non-CompositeBackend backend, custom middleware).
    """
    env = _base_serve_env()

    # ---- Core fields -------------------------------------------------------
    env["RAI_SERVE_MODEL"]      = model
    env["RAI_SERVE_AGENT"]      = builder._agent_name
    env["RAI_SERVE_API_KEY"]    = builder._api_key
    env["RAI_SERVE_BASE_URL"]   = builder._base_url
    env["RAI_SERVE_TARGET"]     = builder._target or ""
    env["RAI_SERVE_RATE_LIMIT"] = builder._rate_limit_profile
    env["RAI_SERVE_CWD"]        = cwd
    # ServeConfig.hitl overrides builder._auto_approve when explicitly True
    hitl = config.hitl or (not builder._auto_approve)
    env["RAI_SERVE_HITL"]       = "1" if hitl else "0"

    # ---- MCP ---------------------------------------------------------------
    env["RAI_SERVE_NO_MCP"] = "1" if getattr(builder, "_no_mcp", False) else "0"

    # ---- Extended builder state -------------------------------------------
    env["RAI_SERVE_SYSTEM_PROMPT"]        = builder._system_prompt or ""
    env["RAI_SERVE_SYSTEM_PROMPT_EXTRA"]  = builder._system_prompt_extra or ""
    env["RAI_SERVE_CUSTOM_SUBAGENTS"]     = json.dumps(builder._custom_subagents or [])
    env["RAI_SERVE_DISABLE_SUBAGENTS"]    = "1" if builder._disable_subagents else "0"
    env["RAI_SERVE_DISABLE_NATIVE_TOOLS"] = "1" if not builder._enable_native_tools else "0"
    env["RAI_SERVE_ENABLE_MEMORY"]        = "1" if builder._enable_memory else "0"
    env["RAI_SERVE_ENABLE_SHELL"]         = "1" if builder._enable_shell else "0"
    env["RAI_SERVE_ENABLE_SKILLS"]        = "1" if builder._enable_skills else "0"
    env["RAI_SERVE_ENABLE_AUDIT"]         = "1" if getattr(builder, "_enable_audit_log", True) else "0"

    # ---- Custom middleware — not serializable --------------------------------
    if builder._custom_middleware:
        raise ServeError(
            "RAIAgentBuilder.serve() does not support custom_middleware.\n"
            "Define a graph module with a `graph` variable and use:\n\n"
            "    serve_module('your_module.py:graph', config, env={...})\n\n"
            "See examples/10_threat_modeling.py for a complete example."
        )

    # ---- Extra tools — serialize as module:attr references -----------------
    tool_refs: list[str] = []
    for t in builder._extra_tools or []:
        ref = _serialize_tool_ref(t)
        if ref is None:
            tool_name = getattr(t, "name", getattr(t, "__name__", repr(t)))
            raise ServeError(
                f"Tool {tool_name!r} cannot be serialized for serve.\n"
                "Tools defined in __main__ or with non-matching variable names "
                "cannot be re-imported in the langgraph subprocess.\n\n"
                "Options:\n"
                "  1. Define the tool in an importable module (not a script).\n"
                "  2. Use serve_module('your_module.py:graph', config).\n\n"
                "See examples/10_threat_modeling.py for option 2."
            )
        tool_refs.append(ref)
    env["RAI_SERVE_TOOLS"] = json.dumps(tool_refs)

    # ---- Custom backend — serialize if it's a standard CompositeBackend ----
    backend_spec: dict | None = None
    if builder._custom_backend is not None:
        backend_spec = _serialize_backend(builder._custom_backend)
        if backend_spec is None:
            raise ServeError(
                f"Custom backend {type(builder._custom_backend).__name__!r} cannot be "
                "auto-serialized.\n"
                "Only CompositeBackend(default=LocalShellBackend(), "
                "routes={str: FilesystemBackend(...)}) is supported.\n\n"
                "Use serve_module('your_module.py:graph', config, env={...}) instead.\n"
                "See examples/10_threat_modeling.py for a complete example."
            )
    env["RAI_SERVE_BACKEND_SPEC"] = json.dumps(backend_spec) if backend_spec else ""

    return env


# ---------------------------------------------------------------------------
# Module ref parsing
# ---------------------------------------------------------------------------


def _parse_module_ref(module_ref: str) -> tuple[Path, str]:
    """Parse ``"path/to/file.py:symbol"`` or ``"pkg.module:symbol"``.

    Returns (absolute_module_path, symbol_name).
    """
    if ":" not in module_ref:
        raise ValueError(
            f"module_ref must contain a ':' separator, e.g. 'myfile.py:graph'. "
            f"Got: {module_ref!r}"
        )
    module_part, _, symbol = module_ref.rpartition(":")

    # File path form: contains '/' or ends with '.py'
    if "/" in module_part or os.sep in module_part or module_part.endswith(".py"):
        path = Path(module_part).resolve()
        return path, symbol

    # Dotted module name form
    spec = importlib.util.find_spec(module_part)
    if spec is None or not spec.origin:
        raise ModuleNotFoundError(
            f"Cannot find module {module_part!r}. "
            "Use a file path ('path/to/module.py:symbol') or an importable dotted name."
        )
    return Path(spec.origin), symbol


def _callable_to_module_ref(func: Callable) -> str:  # type: ignore[type-arg]
    """Convert a callable to a ``"path:name"`` module ref string."""
    module_name: str | None = getattr(func, "__module__", None)
    qual_name: str = getattr(func, "__qualname__", None) or getattr(func, "__name__", "")
    # Use only the top-level name (no nested class separators)
    attr = qual_name.split(".")[0]

    if not attr:
        raise ServeError(
            f"Cannot determine name for callable {func!r}. "
            "Use serve_module('path/to/file.py:symbol') explicitly."
        )

    if module_name == "__main__":
        try:
            source_file = inspect.getfile(func)
        except TypeError:
            raise ServeError(
                f"Callable {attr!r} is defined in __main__ and has no source file. "
                "Use serve_module('path/to/file.py:symbol') instead."
            )
        return f"{source_file}:{attr}"

    # Importable module — resolve to file path for the wrapper
    if module_name:
        spec = importlib.util.find_spec(module_name)
        if spec and spec.origin:
            return f"{spec.origin}:{attr}"

    raise ServeError(
        f"Cannot resolve module for callable {func!r}. "
        "Use serve_module('path/to/file.py:symbol') explicitly."
    )


# ---------------------------------------------------------------------------
# serve_module — main API for custom agents
# ---------------------------------------------------------------------------


def serve_module(
    module_ref: "str | Callable[[], Any]",
    config: ServeConfig | None = None,
    env: dict[str, str] | None = None,
    *,
    src_path: str | None = None,
    **kwargs: Any,
) -> NoReturn:
    """Serve a custom graph defined in a Python module via the LangGraph API.

    This is the escape hatch for agents with non-serializable state (custom
    backends, middleware, or tools in ``__main__``).

    Args:
        module_ref:  Either a ``"path/to/file.py:symbol"`` string (the symbol
                     may be a compiled graph **or** a zero-arg factory function
                     that returns one) **or** a Python callable directly.
        config:      :class:`ServeConfig` instance.  Defaults are used when
                     ``None``.
        env:         Extra environment variables forwarded to the subprocess
                     (e.g. ``{"TM_PROJECT_NAME": "myapp"}``).
        src_path:    Additional directory inserted into ``sys.path`` in the
                     wrapper module — useful when your module imports from a
                     local ``src/`` tree.
        **kwargs:    Forwarded to ``ServeConfig(**kwargs)`` when ``config`` is
                     ``None``.
    """
    if config is None:
        config = ServeConfig(**kwargs) if kwargs else ServeConfig()

    # Normalize callable → string ref
    if callable(module_ref):
        module_ref = _callable_to_module_ref(module_ref)  # type: ignore[arg-type]

    module_path, symbol = _parse_module_ref(module_ref)  # type: ignore[arg-type]
    if not module_path.exists():
        raise FileNotFoundError(
            f"Module file not found: {module_path}\n"
            f"Resolved from: {module_ref!r}"
        )

    rai_src = str(Path(__file__).parent.parent.parent.parent)  # sdk/serve/serve.py → src/
    module_dir = str(module_path.parent)
    sys_paths = [rai_src, module_dir]
    if src_path:
        sys_paths.insert(0, src_path)

    wrapper_src = _MODULE_WRAPPER_TEMPLATE.format(
        sys_paths=sys_paths,
        module_path=str(module_path),
        symbol=symbol,
    )

    merged_env = _base_serve_env()
    if env:
        merged_env.update(env)
    if config.hitl:
        merged_env["RAI_SERVE_HITL"] = "1"

    with tempfile.TemporaryDirectory(prefix="rai_servemod_") as tmp_str:
        tmp = Path(tmp_str)
        (tmp / "wrapper.py").write_text(wrapper_src, encoding="utf-8")

        lg_config: dict = {
            "dependencies": ["."],
            "graphs": {"agent": "./wrapper.py:graph"},
        }
        if config.env_file:
            lg_config["env"] = config.env_file
        config_path = tmp / "langgraph.json"
        config_path.write_text(json.dumps(lg_config, indent=2), encoding="utf-8")

        print(f"[RAI serve] module:   {module_path}")
        print(f"[RAI serve] symbol:   {symbol}")
        print(f"[RAI serve] address:  http://{config.host}:{config.port}")
        print(f"[RAI serve] workspace: {tmp_str}")
        print("Press Ctrl-C to stop.\n")

        _spawn_langgraph(config_path, config, merged_env, cwd=tmp_str)


# ---------------------------------------------------------------------------
# Builder serve (used by RAIAgentBuilder.serve — imported in sdk.py)
# ---------------------------------------------------------------------------


def _serve_from_builder(builder: Any, config: ServeConfig) -> NoReturn:
    """Invoked by RAIAgentBuilder.serve() after validating config."""
    from rai import DEFAULT_MODEL as _DEFAULT_MODEL

    # Resolve model to a string
    model = builder._model
    if not isinstance(model, str):
        raise ServeError(
            "RAIAgentBuilder.serve() requires a model *string* "
            "(e.g. \"anthropic:claude-sonnet-4-6\"). "
            "Pass a BaseChatModel only when using .build() for in-process runs."
        )
    if not model:
        model = os.environ.get("RAI_MODEL") or _DEFAULT_MODEL

    cwd = str(builder._cwd or Path.cwd())
    src_path = str(Path(__file__).parent.parent.parent.parent)  # sdk/serve/serve.py → src/

    env = _build_env_from_builder(builder, model=model, cwd=cwd, config=config)

    with tempfile.TemporaryDirectory(prefix="rai_serve_") as tmp_str:
        tmp = Path(tmp_str)
        (tmp / "rai_graph.py").write_text(
            _BUILDER_GRAPH_TEMPLATE.format(
                src_path=src_path,
                default_model=model,
            ),
            encoding="utf-8",
        )

        lg_config: dict = {
            "dependencies": ["."],
            "graphs": {"agent": "./rai_graph.py:graph"},
        }
        if config.env_file:
            lg_config["env"] = config.env_file
        config_path = tmp / "langgraph.json"
        config_path.write_text(json.dumps(lg_config, indent=2), encoding="utf-8")

        print(
            f"[RAI serve] agent={builder._agent_name!r}  "
            f"model={model!r}  "
            f"http://{config.host}:{config.port}"
        )
        print(f"[RAI serve] workspace: {tmp_str}")
        print("Press Ctrl-C to stop.\n")

        _spawn_langgraph(config_path, config, env, cwd=tmp_str)
