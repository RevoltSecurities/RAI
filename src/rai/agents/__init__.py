"""RAI agents package — AGENTS.md parser, loader, and background agent dispatch."""

from rai.agents.parser import (
    AgentMDEntry,
    parse_agents_md,
    scaffold_agents_md,
)
from rai.agents.loader import (
    to_deepagents_subagent,
    subagent_mcp_config_path,
    load_subagents_for,
)
from rai.agents.background import (
    LocalAsyncAgentMiddleware,
    LocalAsyncAgentState,
    LocalAsyncTask,
    pop_pending_notification_text,
    get_running_agent_names,
    _TASK_REGISTRY,
    _PENDING_NOTIFICATIONS,
    _NOTIF_LOCK,
    _TASK_AGENT_NAMES,
)

__all__ = [
    "AgentMDEntry",
    "parse_agents_md",
    "scaffold_agents_md",
    "to_deepagents_subagent",
    "subagent_mcp_config_path",
    "load_subagents_for",
    "LocalAsyncAgentMiddleware",
    "LocalAsyncAgentState",
    "LocalAsyncTask",
    "pop_pending_notification_text",
    "get_running_agent_names",
    "_TASK_REGISTRY",
    "_PENDING_NOTIFICATIONS",
    "_NOTIF_LOCK",
    "_TASK_AGENT_NAMES",
]
