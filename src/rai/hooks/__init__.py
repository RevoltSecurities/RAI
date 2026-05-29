"""RAI hooks package."""

from rai.hooks.runner import (
    PRE_TOOL_USE,
    POST_TOOL_USE,
    STOP,
    PRE_MODEL_CALL,
    POST_MODEL_CALL,
    ALL_EVENTS,
    HookDecision,
    get_session_id,
    get_hooks_for_event,
    reload_hooks_config,
    fire_pre_tool_use,
    afire_pre_tool_use,
    fire_post_tool_use_bg,
    fire_stop_bg,
    fire_model_event_bg,
)

__all__ = [
    "PRE_TOOL_USE",
    "POST_TOOL_USE",
    "STOP",
    "PRE_MODEL_CALL",
    "POST_MODEL_CALL",
    "ALL_EVENTS",
    "HookDecision",
    "get_session_id",
    "get_hooks_for_event",
    "reload_hooks_config",
    "fire_pre_tool_use",
    "afire_pre_tool_use",
    "fire_post_tool_use_bg",
    "fire_stop_bg",
    "fire_model_event_bg",
]
