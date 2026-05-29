"""OPPLAN middleware — injects engagement objective status into every LLM call.

Zero token overhead when no objectives are defined (skips injection entirely).
"""

from __future__ import annotations

from langchain.agents.middleware.types import AgentMiddleware

_OPPLAN_SYSTEM_PROMPT = """\
## OPPLAN — Operational Objective Tracker

You have access to 9 OPPLAN tools for managing engagement objectives:

- **opplan_init** — Initialize engagement (discipline, target, scope, methodology). \
Loads standard template objectives for the chosen discipline. Call FIRST for any new engagement.
- **opplan_add** — Add a custom objective with phase, description, acceptance criteria, \
risk_level, framework_refs, tool_hints.
- **opplan_get** — Read one objective's full details. ALWAYS call before opplan_update.
- **opplan_list** — List all objectives with progress summary table.
- **opplan_update** — Update status/notes/owner. Valid transitions: \
pending→in-progress, in-progress→completed/blocked, blocked→in-progress/completed.
- **opplan_expand** — Break a broad objective into child sub-tasks (Task Tree pattern).
- **opplan_collapse** — Cancel all descendants of a parent objective.
- **opplan_save** — Export OPPLAN to engagement workspace directory.
- **opplan_load** — Load a previously saved OPPLAN to resume an engagement.

### Discipline Phase Vocabulary
| Discipline | Typical Phases |
|---|---|
| pentesting | discovery → scanning → exploitation → validation → reporting |
| sast | triage → analysis → validation → reporting |
| dast | setup → scanning → testing → validation → reporting |
| threat-modeling | scope → identification → analysis → mitigation → reporting |
| api-security | discovery → auth-testing → data-testing → logic-testing → reporting |
| red-team | recon → initial-access → persistence → lateral-movement → exfiltration → reporting |
| cloud-security | discovery → access-review → config-review → data-exposure → reporting |
| bug-bounty | recon → testing → exploitation → reporting |

### Rules
1. Call opplan_get BEFORE opplan_update — never update from memory.
2. Mark an objective in-progress BEFORE starting work on it.
3. Include evidence or findings in notes when completing an objective.
4. Include failure reason when blocking an objective.
5. An objective is completable in ONE context window — use opplan_expand for broad tasks.
6. Status transitions are enforced — follow the sequence above.
"""


def _prepend_reminder(request, reminder: str):
    """Prepend a <system-reminder> block to the last HumanMessage (ephemeral, not saved)."""
    from langchain_core.messages import HumanMessage

    msgs = list(request.messages)
    if not msgs or not isinstance(msgs[-1], HumanMessage):
        return request
    last = msgs[-1]
    tag = f"<system-reminder>\n{reminder}\n</system-reminder>\n\n"
    content = last.content
    if isinstance(content, str):
        new_content = tag + content
    elif isinstance(content, list):
        new_content = [{"type": "text", "text": tag}] + list(content)
    else:
        return request
    msgs[-1] = HumanMessage(content=new_content, additional_kwargs=last.additional_kwargs)
    return request.override(messages=msgs)


class OPPLANMiddleware(AgentMiddleware):
    """Inject OPPLAN status into every LLM call.

    Injected only when the OPPLAN has objectives — zero token overhead otherwise.

    Split injection strategy:
    - _OPPLAN_SYSTEM_PROMPT (static ~380 tokens): appended as a new content_block to
      system_message via append_to_system_message() once objectives exist. This preserves
      the cache_control breakpoint stamped by StaticSystemPromptCacheBreakpointMiddleware,
      so only the OPPLAN instructions block (not the full stable prefix) cache-busts at init.
    - Dynamic status table: injected as <system-reminder> in the last user message —
      ephemeral, never stored in LangGraph state, never busts the system prompt cache.
    """

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name
        self._last_hash: int = 0
        self._last_status: str = ""

    def wrap_model_call(self, request, handler):
        return handler(self._inject(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._inject(request))

    def _inject(self, request):
        from rai.tools.core.opplan import _load, format_opplan_status

        opplan = _load(self._agent_name)
        if not opplan.objectives:
            return request
        status = format_opplan_status(opplan)
        h = hash(status)
        if h != self._last_hash:
            self._last_hash = h
            self._last_status = status
        # Static OPPLAN tool docs → system_message content_blocks (preserves cache_control).
        # MUST use append_to_system_message here — override(system_prompt=...) flattens
        # all existing content_blocks to a plain string, destroying the cache_control
        # breakpoint that StaticSystemPromptCacheBreakpointMiddleware stamped at pos 9.
        # append_to_system_message preserves the existing blocks (including cache_control)
        # and adds the OPPLAN instructions as a new block after the breakpoint.
        try:
            from deepagents.middleware._utils import append_to_system_message
            new_sm = append_to_system_message(request.system_message, _OPPLAN_SYSTEM_PROMPT)
            request = request.override(system_message=new_sm)
        except Exception:
            # Fallback: deprecated string property (keeps agent working if SDK changes)
            system_prompt = (request.system_prompt or "") + "\n\n" + _OPPLAN_SYSTEM_PROMPT
            request = request.override(system_prompt=system_prompt)
        # Dynamic status table → ephemeral <system-reminder> (never busts system prompt cache)
        return _prepend_reminder(request, self._last_status)
