"""10_threat_modeling.py — Threat modeling orchestrator with shared virtual project path.

Two modes, same agent:

  CLI mode   — run once synchronously and exit (one project, one task)
  Serve mode — expose the agent as a LangGraph API server via `langgraph dev`,
               exactly the same way `rai serve` does it.  All LangGraph REST
               endpoints become available (stream, threads, runs, Studio UI).

Virtual path design
-------------------
  /project/<project-name>/  →  real filesystem directory (project_path)

Both the core orchestrator and the component-threat-mapper subagent share the
same CompositeBackend — the factory wires it into every compiled subagent
automatically via FilesystemMiddleware.

CLI usage:
    python examples/10_threat_modeling.py \\
        --project-name myapp \\
        --project-path /path/to/myapp

Serve usage (LangGraph API):
    python examples/10_threat_modeling.py --serve \\
        --project-name myapp \\
        --project-path /path/to/myapp \\
        --port 2024

    GET  http://localhost:2024/ok          health check
    POST http://localhost:2024/runs/stream stream a run
    GET  http://localhost:2024/docs        OpenAPI / Swagger
"""

# NOTE: Pylance may show "rai.sdk could not be resolved" — the package is
# installed as `revolt-rai`; the import is correct at runtime.
from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from rai import DEFAULT_MODEL  # type: ignore[import]
from rai.sdk import (  # type: ignore[import]
    RAIAgent,
    ServeConfig,
    SubAgent,
    CompositeBackend,
    FilesystemBackend,
    LocalShellBackend,
    serve_module,
)
from rai.engine.factory import create_rai_agent  # type: ignore[import]


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------


def build_project_backend(project_name: str, project_path: Path) -> CompositeBackend:
    """Mount the real project directory at the virtual path /project/<name>/.

    Both the core agent and every compiled subagent share this backend
    because LocalAsyncAgentMiddleware passes it through FilesystemMiddleware.
    """
    large_results = FilesystemBackend(
        root_dir=Path(tempfile.mkdtemp(prefix=f"rai_tm_{project_name}_")),
        virtual_mode=True,
    )
    return CompositeBackend(
        default=LocalShellBackend(),
        routes={
            f"/project/{project_name}/": FilesystemBackend(root_dir=project_path),
            "/large_results/": large_results,
        },
    )


# ---------------------------------------------------------------------------
# Subagent definition
# ---------------------------------------------------------------------------

_MAPPER_SYSTEM_PROMPT = """\
You are a threat modeling expert specialising in STRIDE and MITRE ATT&CK.
Your scope is ONE architectural component at a time.

Virtual path
------------
All project files are under /project/{project_name}/.
Read architecture docs, source code, configs, and API specs relevant to the
component before writing threats.

Output format (no prose outside this structure)
------------------------------------------------
## Component: <name>

### Trust Boundaries
List every trust boundary the component crosses.

### STRIDE Threat Table
| # | Category                | Threat Description | Affected Asset | CVSS est. | Mitigation |
|---|-------------------------|--------------------|----------------|-----------|------------|

Categories: Spoofing, Tampering, Repudiation, Information Disclosure,
            Denial of Service, Elevation of Privilege

### MITRE ATT&CK Mappings
| Threat # | Technique ID | Technique Name | Tactic |
|----------|--------------|----------------|--------|

### Attack Surface Notes
Bullet list of exposed interfaces, ingress/egress, and data flows.
"""


def make_component_mapper_subagent(project_name: str) -> SubAgent:
    return {
        "name": "component-threat-mapper",
        "description": (
            "STRIDE and MITRE ATT&CK expert. Analyses one architectural component "
            "from /project/<name>/ and produces structured threat tables."
        ),
        "system_prompt": _MAPPER_SYSTEM_PROMPT.format(project_name=project_name),
        "tools": [],
    }


# ---------------------------------------------------------------------------
# Orchestrator system prompt
# ---------------------------------------------------------------------------

_ORCHESTRATOR_PROMPT = """\
You are a threat modeling orchestrator for the project "{project_name}".
All project files are at /project/{project_name}/.

Workflow
--------
1. Discover components
   Read /project/{project_name}/ — README, architecture docs, docker-compose,
   Kubernetes manifests, OpenAPI specs, .env.example, config files.
   Build a component list (e.g. "API Gateway", "Auth Service", "PostgreSQL").

2. Dispatch analysis in parallel
   For each component call start_parallel_agents to invoke component-threat-mapper:
     "Analyse the <component> component of {project_name}. Read relevant files from
      /project/{project_name}/ and produce the full STRIDE + MITRE ATT&CK table."

3. Wait and collect
   Use check_agent_tasks to collect all component results.

4. Synthesise — write the master threat model to
   /project/{project_name}/threat_model.md with this structure:

   # Threat Model: {project_name}
   ## 1. Architecture Overview
   ## 2. Component Threat Tables   (all component outputs merged, deduped)
   ## 3. MITRE ATT&CK Heat Map     (Tactic | Techniques | Highest-severity component)
   ## 4. Top 10 Priority Threats   (cross-component, ranked by likelihood × impact)
   ## 5. Overall Risk Rating        (Critical / High / Medium / Low + justification)

Rules
-----
- Read files before assuming anything about the architecture.
- All file paths must be under /project/{project_name}/.
- If the directory is empty, report it and stop.
"""


# ---------------------------------------------------------------------------
# Core async run (CLI mode)
# ---------------------------------------------------------------------------


async def run_threat_model(
    project_name: str,
    project_path: Path,
    task: str,
    model: str = DEFAULT_MODEL,
    api_key: str = "",
    base_url: str = "",
) -> str:
    if not project_path.is_dir():
        raise FileNotFoundError(f"Project path not found: {project_path}")

    backend = build_project_backend(project_name, project_path)
    mapper = make_component_mapper_subagent(project_name)

    builder = (
        RAIAgent.builder()
        .model(model)
        .agent_name("threat-model-orchestrator")
        .system_prompt(_ORCHESTRATOR_PROMPT.format(project_name=project_name))
        .backend(backend)
        .without_subagents()
        .add_subagent(mapper)
        .without_hitl()
    )
    if api_key:
        builder = builder.api_key(api_key)
    if base_url:
        builder = builder.base_url(base_url)

    async with await builder.build() as agent:
        print(f"\n[threat-model] project={project_name!r}  thread={agent.thread_id}")
        result = await agent.run(task, timeout=1800.0)
        return str(result)


# ---------------------------------------------------------------------------
# Module-level graph factory — used by serve_module() and langgraph dev
# ---------------------------------------------------------------------------
# TM_* env vars are set by main() --serve (via serve_module env=) before
# the subprocess is spawned, so the module is hot-reloadable and contains
# no hard-coded paths.


def build_graph() -> object:
    """Build the threat-modeling graph from TM_* environment variables."""
    project_name = os.environ.get("TM_PROJECT_NAME", "project")
    project_path = Path(os.environ.get("TM_PROJECT_PATH", ".")).expanduser().resolve()
    model        = os.environ.get("TM_MODEL",    DEFAULT_MODEL)
    api_key      = os.environ.get("TM_API_KEY",  "")
    base_url     = os.environ.get("TM_BASE_URL", "")
    hitl         = os.environ.get("TM_HITL", "0") == "1"

    backend = build_project_backend(project_name, project_path)
    mapper  = make_component_mapper_subagent(project_name)

    g, _ = create_rai_agent(
        model=model,
        agent_name="threat-model-orchestrator",
        system_prompt=_ORCHESTRATOR_PROMPT.format(project_name=project_name),
        custom_backend=backend,
        custom_subagents=[mapper],
        disable_subagents=True,
        api_key=api_key,
        base_url=base_url,
        auto_approve=not hitl,
        interactive=False,
    )
    return g


# Top-level graph variable required by `langgraph dev` (and serve_module).
graph = build_graph()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAI Threat Modeling Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--project-name", required=True, dest="project_name",
                        help="Short project identifier — used as the virtual path prefix")
    parser.add_argument("--project-path", required=True, dest="project_path", type=Path,
                        help="Real filesystem path to the project directory")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default="", dest="api_key")
    parser.add_argument("--base-url", default="", dest="base_url")

    # ---- CLI mode ----------------------------------------------------------
    parser.add_argument("--task",
                        default="Generate a complete STRIDE threat model for this project.")

    # ---- Serve mode --------------------------------------------------------
    parser.add_argument("--serve", action="store_true",
                        help="Serve via LangGraph API instead of running once")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2024)
    parser.add_argument("--no-dev", action="store_true", dest="no_dev",
                        help="Disable hot reload (production mode)")
    parser.add_argument("--no-browser", action="store_true", dest="no_browser",
                        help="Do not open LangSmith Studio in the browser")
    parser.add_argument("--hitl", action="store_true",
                        help="Enable HITL approval prompts (requires interactive client)")
    parser.add_argument("--env-file", default=None, dest="env_file",
                        help=".env file passed to the langgraph subprocess")

    args = parser.parse_args()
    project_path = args.project_path.expanduser().resolve()

    if args.serve:
        # serve_module() points langgraph dev at build_graph() in this file.
        # TM_* env vars tell build_graph() which project to load at import time.
        serve_module(
            f"{__file__}:graph",
            ServeConfig(
                port=args.port,
                host=args.host,
                reload=not args.no_dev,
                browser=not args.no_browser,
                hitl=args.hitl,
                env_file=args.env_file,
            ),
            env={
                "TM_PROJECT_NAME": args.project_name,
                "TM_PROJECT_PATH": str(project_path),
                "TM_MODEL":        args.model,
                "TM_API_KEY":      args.api_key,
                "TM_BASE_URL":     args.base_url,
            },
            src_path=str(Path(__file__).parent.parent / "src"),
        )
    else:
        result = asyncio.run(
            run_threat_model(
                project_name=args.project_name,
                project_path=project_path,
                task=args.task,
                model=args.model,
                api_key=args.api_key,
                base_url=args.base_url,
            )
        )
        print(result)


if __name__ == "__main__":
    main()
