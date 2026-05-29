"""Agent endpoints: GET /agents, GET /agents/{name}."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from rai.harness.models import AgentInfo, SubagentInfo

router = APIRouter(tags=["agents"])


def _builder_to_info(name: str, builder) -> AgentInfo:
    model = getattr(builder, "_model", "") or ""
    if hasattr(model, "model_name"):
        model = model.model_name
    # _auto_approve=True  → HITL disabled (all tools auto-approved, no pause for review).
    # _auto_approve=False → HITL enabled (tool calls that match interrupt_on will pause).
    # The CLI defaults to without_hitl(); pass --hitl to enable.
    auto_approve = getattr(builder, "_auto_approve", True)
    hitl_enabled = not auto_approve
    return AgentInfo(
        name=name,
        model=str(model),
        description=getattr(builder, "_description", ""),
        hitl_enabled=hitl_enabled,
        hitl_note="" if hitl_enabled else (
            f"HITL is disabled — tool calls are auto-approved. "
            f"To enable, restart with: rai http --hitl --agent {name}"
        ),
    )


@router.get("/agents", response_model=list[AgentInfo])
async def list_agents(request: Request) -> list[AgentInfo]:
    pool = request.app.state.pool
    return [_builder_to_info(n, pool.get_builder(n)) for n in pool.agent_names()]


@router.get("/agents/{name}/subagents", response_model=list[SubagentInfo])
async def list_agent_subagents(name: str, request: Request) -> list[SubagentInfo]:
    """List configured subagent definitions for a parent agent (from ~/.rai/agents/)."""
    pool = request.app.state.pool
    if pool.get_builder(name) is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    from rai.agents.loader import load_subagents_for
    from rai.config.agent import load_agent_config
    from rai.config.settings import settings

    try:
        cfg = load_agent_config(name)
    except Exception:
        cfg = None

    subagents = load_subagents_for(
        name,
        parent_api_key=cfg.api_key if cfg else "",
        parent_base_url=cfg.base_url if cfg else "",
    )

    result: list[SubagentInfo] = []
    for sa in subagents:
        sa_name = sa.get("name", "")
        has_cfg = (settings.agent_dir(sa_name) / "config.toml").exists() if sa_name else False

        # AGENTS.md only includes "model" when it differs from parent_model="".
        # Fall back to config.toml so the display reflects the effective model.
        model_str = str(sa.get("model", "")) if sa.get("model") else ""
        if not model_str and has_cfg and sa_name:
            try:
                sub_cfg = load_agent_config(sa_name)
                if sub_cfg and sub_cfg.model:
                    model_str = sub_cfg.model
            except Exception:
                pass

        result.append(SubagentInfo(
            name=sa_name,
            description=sa.get("description", ""),
            model=model_str,
            has_own_config=has_cfg,
        ))
    return result


@router.get("/agents/{name}", response_model=AgentInfo)
async def get_agent(name: str, request: Request) -> AgentInfo:
    pool = request.app.state.pool
    builder = pool.get_builder(name)
    if builder is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return _builder_to_info(name, builder)
