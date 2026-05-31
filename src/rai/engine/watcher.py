"""SubagentWatcher — pure Python/asyncio loop that re-invokes the core agent
when background subagent notifications arrive.

The loop continuation is enforced entirely in Python code, not via LLM
instructions.  When a subagent completes, _on_done writes to
_PENDING_NOTIFICATIONS.  SubagentWatcher detects this and re-invokes
agent.ainvoke() with a synthetic HumanMessage.  awrap_model_call intercepts
that invocation and replaces the effective input with the real notification
content before the LLM sees it — the synthetic message text is irrelevant.

Thread-safety: all accesses to _PENDING_NOTIFICATIONS are guarded by
_NOTIF_LOCK (threading.Lock defined in agents.background).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage

from rai.agents.background import _NOTIF_LOCK, _PENDING_NOTIFICATIONS

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_POLL_INTERVAL: float = 2.0
# Synthetic message text is irrelevant — awrap_model_call replaces the
# effective input with real notification content before the LLM sees it.
_SYNTHETIC_MSG = "process pending results"


class SubagentWatcher:
    """Polls _PENDING_NOTIFICATIONS every 2 s and re-invokes the agent when
    notifications are ready and the agent is idle.

    Usage::

        watcher = SubagentWatcher(agent, thread_config)
        watcher.start()

        watcher.set_busy()
        result = await agent.ainvoke(...)
        watcher.set_idle()

        await watcher.stop()
    """

    def __init__(self, agent: Any, thread_config: dict[str, Any]) -> None:
        self._agent = agent
        self._thread_config = thread_config
        # asyncio.Event: set = busy (agent is mid-invocation), clear = idle
        self._busy: asyncio.Event = asyncio.Event()
        self._stop: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # Tracks the result of the most recent successful ainvoke() so run_agent()
        # can return it as the final result instead of calling aget_state().
        self.last_result: Any = None

    # ------------------------------------------------------------------
    # Public control interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background watcher task."""
        self._stop.clear()
        self._task = asyncio.create_task(self._watch_loop(), name="subagent-watcher")

    async def stop(self) -> None:
        """Signal the watcher to stop and await its cancellation."""
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def set_busy(self) -> None:
        self._busy.set()

    def set_idle(self) -> None:
        self._busy.clear()

    def is_busy(self) -> bool:
        return self._busy.is_set()

    # ------------------------------------------------------------------
    # Internal poll loop
    # ------------------------------------------------------------------

    async def _watch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(_POLL_INTERVAL)
            except asyncio.CancelledError:
                return

            if self._stop.is_set():
                return

            # Atomically check whether there is pending work.
            with _NOTIF_LOCK:
                has_notifs = bool(_PENDING_NOTIFICATIONS)

            if not has_notifs:
                continue

            # Don't pile up invocations — wait until the current one finishes.
            if self._busy.is_set():
                continue

            # Guard: reading the last message prevents us from sending a second
            # HumanMessage when one is already last in state, which the Anthropic
            # API would reject as consecutive HumanMessages.
            if not await self._last_message_is_safe():
                continue

            # Re-invoke the agent.  awrap_model_call will pop _PENDING_NOTIFICATIONS
            # and inject the real notification content before the LLM processes it.
            self.set_busy()
            try:
                logger.debug("SubagentWatcher: re-invoking agent (pending notifications present)")
                result = await self._agent.ainvoke(
                    {"messages": [HumanMessage(content=_SYNTHETIC_MSG)]},
                    self._thread_config,
                )
                self.last_result = result
            except Exception:
                logger.exception("SubagentWatcher: re-invocation failed")
            finally:
                self.set_idle()

    async def _last_message_is_safe(self) -> bool:
        """Return True if it is safe to append a new HumanMessage.

        Reads current thread state via agent.aget_state().  If the last message
        is already a HumanMessage, adding another would create a consecutive pair
        that the Anthropic API rejects.  Returns False in that case, or if the
        state cannot be read (fail-safe: skip this cycle).
        """
        try:
            snapshot = await self._agent.aget_state(self._thread_config)
            messages = (snapshot.values or {}).get("messages", [])
            if messages and isinstance(messages[-1], HumanMessage):
                logger.debug("SubagentWatcher: skipping cycle — last state message is HumanMessage")
                return False
        except Exception:
            logger.debug("SubagentWatcher: could not read state — skipping cycle", exc_info=True)
            return False
        return True
