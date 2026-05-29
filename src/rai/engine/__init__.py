"""RAI engine package — agent factory, model helpers, runner, and watcher."""

from rai.engine.model import DEFAULT_AGENT_NAME, DEFAULT_MODEL, _build_llm, _is_litellm_format
from rai.engine.factory import (
    create_rai_agent,
    get_system_prompt,
    load_custom_subagents,
    _build_interrupt_on,
    _postprocess_subagents,
    _memory_write_desc,
    _memory_update_desc,
)
from rai.engine.runner import run_agent
from rai.engine.watcher import SubagentWatcher

__all__ = [
    "DEFAULT_AGENT_NAME",
    "DEFAULT_MODEL",
    "_build_llm",
    "_is_litellm_format",
    "create_rai_agent",
    "get_system_prompt",
    "load_custom_subagents",
    "_build_interrupt_on",
    "_postprocess_subagents",
    "_memory_write_desc",
    "_memory_update_desc",
    "run_agent",
    "SubagentWatcher",
]
