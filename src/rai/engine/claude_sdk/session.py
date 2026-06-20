"""Session management helpers for the RAI Claude SDK.

Thin, typed wrappers around claude_agent_sdk session functions so RAI users
never need to import from claude_agent_sdk._internal directly.

All listing / reading functions are synchronous (disk reads).
Mutation functions (rename, tag, delete, fork) are also synchronous.
SessionStore-backed async variants are available via the *_from_store suffixed
counterparts on the ClaudeSessionStore class.

Quick start::

    from rai.sdk import ClaudeSessionManager

    mgr = ClaudeSessionManager()

    # List recent sessions
    sessions = mgr.list(limit=20)
    for s in sessions:
        print(s.session_id[:8], s.summary, s.last_modified)

    # Read conversation history
    messages = mgr.messages(sessions[0].session_id)
    for msg in messages:
        print(msg.type, msg.message)

    # List subagents that ran in a session
    agent_ids = mgr.subagents(sessions[0].session_id)
    sub_msgs = mgr.subagent_messages(sessions[0].session_id, agent_ids[0])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Re-export SDK types so callers import from rai.sdk only
# ---------------------------------------------------------------------------

from claude_agent_sdk.types import SDKSessionInfo, SessionMessage
from claude_agent_sdk._internal.session_mutations import ForkSessionResult


# ---------------------------------------------------------------------------
# ClaudeSessionManager
# ---------------------------------------------------------------------------


@dataclass
class SubagentInfo:
    """Thin container for a subagent ID + its messages (lazily loaded)."""

    agent_id: str
    session_id: str


class ClaudeSessionManager:
    """Easy API for reading and managing Claude Code sessions.

    All operations default to searching all projects under
    ``~/.claude/projects/`` when ``directory`` is None.  Pass the RAI
    working directory (e.g. ``str(Path.cwd())``) to scope to a single project.

    Example::

        from rai.sdk import ClaudeSessionManager

        mgr = ClaudeSessionManager(directory="/path/to/project")

        sessions = mgr.list(limit=10)
        msgs = mgr.messages(sessions[0].session_id)
        mgr.rename(sessions[0].session_id, "Pentest — example.com")
    """

    def __init__(self, directory: str | None = None) -> None:
        self.directory = directory

    # ---------------------------------------------------------------- listing

    def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        include_worktrees: bool = True,
    ) -> list[SDKSessionInfo]:
        """List sessions sorted by last_modified descending.

        Args:
            limit: Max sessions to return. None = all.
            offset: Skip this many sessions (pagination).
            include_worktrees: Include git worktree sessions when directory is set.
        """
        from claude_agent_sdk import list_sessions
        return list_sessions(
            directory=self.directory,
            limit=limit,
            offset=offset,
            include_worktrees=include_worktrees,
        )

    def info(self, session_id: str) -> SDKSessionInfo | None:
        """Return metadata for a single session, or None if not found."""
        from claude_agent_sdk import get_session_info
        return get_session_info(session_id, directory=self.directory)

    # ---------------------------------------------------------------- messages

    def messages(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SessionMessage]:
        """Return conversation messages in chronological order.

        Args:
            session_id: UUID of the session.
            limit: Max messages to return.
            offset: Skip this many messages (pagination).
        """
        from claude_agent_sdk import get_session_messages
        return get_session_messages(
            session_id,
            directory=self.directory,
            limit=limit,
            offset=offset,
        )

    # ---------------------------------------------------------------- subagents

    def subagents(self, session_id: str) -> list[str]:
        """Return list of subagent IDs that ran in a session."""
        from claude_agent_sdk import list_subagents
        return list_subagents(session_id, directory=self.directory)

    def subagent_messages(
        self,
        session_id: str,
        agent_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SessionMessage]:
        """Return messages for a specific subagent within a session."""
        from claude_agent_sdk import get_subagent_messages
        return get_subagent_messages(
            session_id,
            agent_id,
            directory=self.directory,
            limit=limit,
            offset=offset,
        )

    def all_subagent_messages(
        self, session_id: str
    ) -> dict[str, list[SessionMessage]]:
        """Return messages for every subagent in a session keyed by agent_id."""
        return {
            agent_id: self.subagent_messages(session_id, agent_id)
            for agent_id in self.subagents(session_id)
        }

    # ---------------------------------------------------------------- mutations

    def rename(self, session_id: str, title: str) -> None:
        """Rename a session. Raises ValueError / FileNotFoundError on failure."""
        from claude_agent_sdk import rename_session
        rename_session(session_id, title, directory=self.directory)

    def tag(self, session_id: str, tag: str | None) -> None:
        """Tag a session. Pass None to clear the tag."""
        from claude_agent_sdk import tag_session
        tag_session(session_id, tag, directory=self.directory)

    def delete(self, session_id: str) -> None:
        """Hard-delete a session and its subagent transcripts.

        For soft-delete use tag(session_id, '__hidden') and filter on listing.
        """
        from claude_agent_sdk import delete_session
        delete_session(session_id, directory=self.directory)

    def fork(
        self,
        session_id: str,
        *,
        up_to_message_id: str | None = None,
        title: str | None = None,
    ) -> ForkSessionResult:
        """Fork a session into a new branch.

        Args:
            session_id: Source session UUID.
            up_to_message_id: Slice up to this message UUID (inclusive).
            title: Custom title for the fork. Defaults to original + " (fork)".

        Returns:
            ForkSessionResult with the new session's UUID.
        """
        from claude_agent_sdk import fork_session
        return fork_session(
            session_id,
            directory=self.directory,
            up_to_message_id=up_to_message_id,
            title=title,
        )

    # ---------------------------------------------------------------- convenience

    def full_history(self, session_id: str) -> dict[str, Any]:
        """Return a complete dict with session info, messages, and all subagent messages.

        Useful for exporting or displaying a full session in one call::

            history = mgr.full_history(session_id)
            print(history["info"].summary)
            for msg in history["messages"]:
                print(msg.type, msg.message)
            for agent_id, msgs in history["subagents"].items():
                print(f"  subagent {agent_id}: {len(msgs)} messages")
        """
        return {
            "info": self.info(session_id),
            "messages": self.messages(session_id),
            "subagents": self.all_subagent_messages(session_id),
        }

    def resume_options(self, session_id: str) -> dict[str, str]:
        """Return kwargs to pass to ClaudeAgentBuilder to resume this session.

        Example::

            opts = mgr.resume_options(session_id)
            async with ClaudeAgent.builder().agent_name("rai").resume(opts["resume"]).build() as agent:
                result = await agent.run("continue from where we left off")
        """
        return {"resume": session_id}
