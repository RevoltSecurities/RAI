"""RAI HTTP subagent harness.

Three parent-agent tools injected at compile time (when http_mode is active):
  http_spawn_agent      — fire-and-forget background spawn (Ctrl+B equivalent)
  http_run_agent_sync   — synchronous: blocks the tool call until subagent done
  http_spawn_pipeline   — DAG pipeline with depends_on topology

Each spawned subagent:
  - Compiled with create_rai_agent() (full capabilities, HITL enabled)
  - Streams tokens + tool events to its own RunEventBus (/subagents/{id}/stream)
  - Fans the same events to the parent run's bus (prefixed with task_id)
  - On completion, writes to _PENDING_NOTIFICATIONS so the parent watcher loop
    picks it up, re-invokes the parent LLM, which sees the output via
    LocalAsyncAgentMiddleware context injection
"""

from rai.harness.subagents.registry import (
    _RUN_CONTEXT,
    _SUBAGENT_BUSES,
    _SUBAGENT_HITL,
    _SUBAGENT_LOCK,
    _SUBAGENT_OUTPUTS,
    _SUBAGENT_REGISTRY,
    _SUBAGENT_TASKS,
    RunContext,
    SubagentMeta,
)
from rai.harness.subagents.tools import get_http_subagent_tools

__all__ = [
    "get_http_subagent_tools",
    "_RUN_CONTEXT",
    "RunContext",
    "SubagentMeta",
    "_SUBAGENT_REGISTRY",
    "_SUBAGENT_BUSES",
    "_SUBAGENT_TASKS",
    "_SUBAGENT_OUTPUTS",
    "_SUBAGENT_HITL",
    "_SUBAGENT_LOCK",
]
