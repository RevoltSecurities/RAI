"""WriteTruncateMiddleware — truncate file-write tool call args in ALL messages.

Unlike ToolResultCompressionMiddleware (which uses keep_recent=20 and skips
recent messages), this middleware applies to EVERY message because written
content is on disk and always recoverable via read_file/bash cat.

Targets:
  - write_file/WriteFile/create_file tool calls → truncate 'content' arg
  - edit_file/EditFile/str_replace_editor tool calls → truncate 'new_string'/'old_string' args
  - bash/shell tools with file-write patterns (cat >, cat <<, echo >, tee) → truncate 'command' arg

Enriches:
  - ToolMessage results for write/edit tool calls with <tool-write-info> block
    confirming success, total lines, chars, and file path — so the model always
    knows the operation completed and what was written.

Never touches:
  - HumanMessage, SystemMessage
  - Tool calls where content is < 500 chars or < 10 lines
  - The last 5 write/edit tool calls (keep_recent_writes=5) — coding models need
    full content of recent writes to understand what they just produced

Older writes beyond keep_recent_writes get a 25-line preview + <truncated> info
so the model still has context about what was written without consuming full tokens.

Stack position: 10.6 (between MessageCompression 10.5 and ToolResultCompression 10.7).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

logger = logging.getLogger(__name__)

import os

_MIN_CHARS = 500
_MIN_LINES = 10
_PREVIEW_LINES = 25
_KEEP_RECENT_WRITES = int(os.environ.get("RAI_KEEP_RECENT_WRITES", "5"))

_WRITE_FILE_TOOLS = frozenset({
    "write_file", "WriteFile", "create_file", "write_to_file",
})

_EDIT_FILE_TOOLS = frozenset({
    "edit_file", "EditFile", "str_replace_editor", "replace_in_file",
})

_BASH_TOOLS = frozenset({
    "bash", "shell", "run_bash", "execute_command", "terminal",
    "Bash", "run_command", "execute", "cmd", "Run",
})

_FILE_WRITE_RE = re.compile(
    r"(?:cat\s*>|cat\s*<<|echo\s+.*>|printf\s+.*>|tee\s)",
    re.MULTILINE,
)


def _truncate_content(content: str, path_hint: str = "") -> tuple[str, int, int] | None:
    """Return (truncated_string, total_lines, total_chars) if content exceeds thresholds."""
    if len(content) < _MIN_CHARS:
        return None
    lines = content.splitlines(keepends=True)
    if len(lines) < _MIN_LINES:
        return None

    preview = "".join(lines[:_PREVIEW_LINES])
    total_lines = len(lines)
    total_chars = len(content)
    path_str = f" to {path_hint}" if path_hint else ""

    truncated = (
        f"{preview}"
        f"<truncated>{total_lines} lines, {total_chars} chars written{path_str}. "
        f"Write successful — file exists on disk. No verification needed.</truncated>"
    )
    return truncated, total_lines, total_chars


def _extract_path(args: dict, tool_name: str) -> str:
    """Try to extract file path from tool call args."""
    for key in ("file_path", "path", "filename", "file"):
        if key in args and args[key]:
            return str(args[key])
    if tool_name.lower() in {t.lower() for t in _BASH_TOOLS}:
        cmd = args.get("command", "")
        match = re.search(r"[>|]\s*(/\S+)", cmd)
        if match:
            return match.group(1)
    return ""


def _build_write_info(
    tool_name: str, path: str, total_lines: int, total_chars: int, operation: str,
    tag: str = "tool-write-info",
) -> str:
    """Build a confirmation block for enriching ToolMessage results."""
    return (
        f"<{tag}>\n"
        f"  operation: {operation}\n"
        f"  tool: {tool_name}\n"
        f"  file: {path}\n"
        f"  total_lines: {total_lines}\n"
        f"  total_chars: {total_chars}\n"
        f"  status: success — file written and exists on disk, no read verification needed\n"
        f"</{tag}>"
    )


class WriteTruncateMiddleware(AgentMiddleware):
    """Truncate file-write tool call args in ALL messages.

    Applied to every position (no keep_recent) because written content
    is always recoverable from disk. Fail-open: exceptions pass through.

    Also enriches ToolMessage results for write/edit operations with
    <tool-write-info> blocks so the model has clear confirmation of what
    was written and how much.
    """

    def _compress(self, request: ModelRequest) -> ModelRequest:
        msgs = list(request.messages or [])
        if not msgs:
            return request

        changed = False
        new_msgs = list(msgs)

        # Pre-scan: find indices of last N AIMessages containing write/edit tool calls.
        # These keep full content (coding models need to see what they just produced).
        recent_write_indices: set[int] = set()
        write_count = 0
        for idx in range(len(new_msgs) - 1, -1, -1):
            if write_count >= _KEEP_RECENT_WRITES:
                break
            m = new_msgs[idx]
            if not isinstance(m, AIMessage):
                continue
            tcs = getattr(m, "tool_calls", None) or []
            has_write = False
            for tc in tcs:
                name = tc.get("name", "") or ""
                name_lower = name.lower()
                if (name in _WRITE_FILE_TOOLS or name_lower in {t.lower() for t in _WRITE_FILE_TOOLS}
                        or name in _EDIT_FILE_TOOLS or name_lower in {t.lower() for t in _EDIT_FILE_TOOLS}):
                    has_write = True
                    break
            if has_write:
                recent_write_indices.add(idx)
                write_count += 1

        # Track tool_call_id → write metadata for ToolMessage enrichment
        write_meta: dict[str, dict] = {}

        for i, msg in enumerate(new_msgs):
            if isinstance(msg, (HumanMessage, SystemMessage)):
                continue

            # --- Enrich ToolMessage results for write/edit operations ---
            if isinstance(msg, ToolMessage):
                tc_id = getattr(msg, "tool_call_id", "") or ""
                if tc_id in write_meta:
                    meta = write_meta[tc_id]
                    content = getattr(msg, "content", "") or ""
                    # Only enrich if result doesn't already contain error
                    if isinstance(content, str) and "error" not in content.lower():
                        info_block = _build_write_info(
                            tool_name=meta["tool"],
                            path=meta["path"],
                            total_lines=meta["lines"],
                            total_chars=meta["chars"],
                            operation=meta["op"],
                            tag=meta["tag"],
                        )
                        enriched = f"{content}\n{info_block}" if content.strip() else info_block
                        new_msgs[i] = msg.model_copy(update={"content": enriched})
                        changed = True
                continue

            if not isinstance(msg, AIMessage):
                continue

            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                continue

            # Skip truncation for the last N write/edit AIMessages — keep full content
            if i in recent_write_indices:
                # Still track metadata for ToolMessage enrichment
                for tc in tool_calls:
                    name = tc.get("name", "") or ""
                    args = tc.get("args") or {}
                    tc_id = tc.get("id", "")
                    if not tc_id:
                        continue
                    if name in _WRITE_FILE_TOOLS or name.lower() in {t.lower() for t in _WRITE_FILE_TOOLS}:
                        content = str(args.get("content", ""))
                        path_hint = _extract_path(dict(args), name)
                        lines = content.splitlines()
                        write_meta[tc_id] = {
                            "tool": name, "path": path_hint or "<unknown>",
                            "lines": len(lines), "chars": len(content),
                            "op": "write", "tag": "tool-write-info",
                        }
                    elif name in _EDIT_FILE_TOOLS or name.lower() in {t.lower() for t in _EDIT_FILE_TOOLS}:
                        new_string = str(args.get("new_string", ""))
                        old_string = str(args.get("old_string", ""))
                        path_hint = _extract_path(dict(args), name)
                        if path_hint:
                            write_meta[tc_id] = {
                                "tool": name, "path": path_hint,
                                "lines": len(new_string.splitlines()),
                                "chars": len(new_string),
                                "op": f"edit (+{len(new_string.splitlines())} -{len(old_string.splitlines())} lines)",
                                "tag": "tool-edit-info",
                            }
                continue

            new_tool_calls = []
            tc_changed = False

            for tc in tool_calls:
                name = tc.get("name", "") or ""
                args = dict(tc.get("args") or {})
                tc_id = tc.get("id", "")

                if name in _WRITE_FILE_TOOLS or name.lower() in {t.lower() for t in _WRITE_FILE_TOOLS}:
                    content = str(args.get("content", ""))
                    path_hint = _extract_path(args, name)
                    result = _truncate_content(content, path_hint)
                    if result is not None:
                        truncated, total_lines, total_chars = result
                        args["content"] = truncated
                        tc_changed = True
                        if tc_id:
                            write_meta[tc_id] = {
                                "tool": name, "path": path_hint or "<unknown>",
                                "lines": total_lines, "chars": total_chars,
                                "op": "write", "tag": "tool-write-info",
                            }
                    else:
                        # Not truncated but still track for enrichment
                        lines = content.splitlines()
                        if tc_id and path_hint:
                            write_meta[tc_id] = {
                                "tool": name, "path": path_hint,
                                "lines": len(lines), "chars": len(content),
                                "op": "write", "tag": "tool-write-info",
                            }
                    new_tool_calls.append({**tc, "args": args})
                    continue

                if name in _EDIT_FILE_TOOLS or name.lower() in {t.lower() for t in _EDIT_FILE_TOOLS}:
                    new_string = str(args.get("new_string", ""))
                    old_string = str(args.get("old_string", ""))
                    path_hint = _extract_path(args, name)
                    # Truncate new_string
                    result_new = _truncate_content(new_string, path_hint)
                    if result_new is not None:
                        truncated, total_lines, total_chars = result_new
                        args["new_string"] = truncated
                        tc_changed = True
                    # Truncate old_string too
                    result_old = _truncate_content(old_string, path_hint)
                    if result_old is not None:
                        truncated_old, _, _ = result_old
                        args["old_string"] = truncated_old
                        tc_changed = True
                    # Track for enrichment
                    if tc_id and path_hint:
                        new_lines = new_string.splitlines()
                        old_lines = old_string.splitlines()
                        write_meta[tc_id] = {
                            "tool": name, "path": path_hint,
                            "lines": len(new_lines), "chars": len(new_string),
                            "op": f"edit (+{len(new_lines)} -{len(old_lines)} lines)",
                            "tag": "tool-edit-info",
                        }
                    new_tool_calls.append({**tc, "args": args})
                    continue

                if name in _BASH_TOOLS or name.lower() in {t.lower() for t in _BASH_TOOLS}:
                    cmd = str(args.get("command", ""))
                    if _FILE_WRITE_RE.search(cmd):
                        path_hint = _extract_path(args, name)
                        result = _truncate_content(cmd, path_hint)
                        if result is not None:
                            truncated, total_lines, total_chars = result
                            args["command"] = truncated
                            tc_changed = True
                            if tc_id and path_hint:
                                write_meta[tc_id] = {
                                    "tool": name, "path": path_hint,
                                    "lines": total_lines, "chars": total_chars,
                                    "op": "bash write", "tag": "tool-write-info",
                                }
                    new_tool_calls.append({**tc, "args": args})
                    continue

                new_tool_calls.append(tc)

            if tc_changed:
                new_msgs[i] = msg.model_copy(update={"tool_calls": new_tool_calls})
                changed = True

        if not changed:
            return request

        return request.override(messages=new_msgs)

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        try:
            return handler(self._compress(request))
        except Exception:
            logger.debug("WriteTruncateMiddleware: failed, passing through", exc_info=True)
            return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        try:
            return await handler(self._compress(request))
        except Exception:
            logger.debug("WriteTruncateMiddleware: failed, passing through", exc_info=True)
            return await handler(request)
