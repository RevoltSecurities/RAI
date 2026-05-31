"""Plan-mode lifecycle tools for the HTTP parent agent.

These tools implement the full plan harness: entering plan mode, submitting a
plan for approval, tracking step execution, and gating the exit. They are only
injected into the HTTP harness parent agent — subagents do not receive them and
can use write_todos freely for their own task tracking.
"""

from __future__ import annotations

import asyncio
import json
import re as _re
from pathlib import Path as _Path
from typing import Any

from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Harness redirect messages
# ---------------------------------------------------------------------------

_HARNESS_GUIDE = """\
<plan-required>
This tool is only available during approved plan execution.
You must follow the full plan harness in order:

  1. enter_plan_mode()              — enter exploration/research mode
  2. ask_user()                     — STRICTLY required: gather goals, scope, constraints,
                                      and expected outcomes from the user before planning
  3. (use read-only tools to research the task)
  4. write_plan(content)            — submit a detailed numbered step-by-step plan for approval
  5. (execution pauses — wait for the user to approve or reject)
  6. enter_step(N)                  — mark the step in_progress before starting work
  7. (perform the step's work)
  8. mark_step_done(N, notes=...)   — record completion; use notes for key findings
     OR mark_step_blocked(N, reason) if the step cannot proceed
  9. Repeat steps 6–8 for every numbered step in the plan
 10. exit_plan_mode()               — gate-check all steps, write self-learning memories,
                                      then summarise and end

Call enter_plan_mode() now to begin. Then immediately use ask_user() to understand
what the user wants before you start any research or planning.
</plan-required>"""

_PLAN_MODE_REDIRECT = """\
<plan-required>
You are currently in exploration mode (plan_mode active).
Execution tools are not available until your plan is approved.

Use ask_user() strictly to interact with the user — ask clarifying questions about:
  • Goal and scope of the task
  • Expected outcomes for each phase
  • Constraints, exclusions, or priorities

Once you have a clear picture of what they want, call write_plan(content) with a
detailed numbered step-by-step plan. Execution will begin only after approval.
</plan-required>"""

_PLAN_APPROVED_REDIRECT = """\
<plan-required>
Your plan is already approved and executing.
Do not write a new plan — continue executing the approved plan steps:
  enter_step(N) → do the work → mark_step_done(N) (or mark_step_blocked(N, reason))
Call list_plan_steps() to see the current step statuses.
</plan-required>"""


# ---------------------------------------------------------------------------
# Plan parsing helpers
# ---------------------------------------------------------------------------

# Ordered by specificity — first match wins per line.
_STEP_PATTERNS: list[tuple[str, _re.Pattern]] = [
    # Bold numbered: **1.** **1)** *1.*
    ("bold_num",   _re.compile(r'^\s*\*{1,2}(\d+)[.\)]\*{0,2}\s+(.+)')),
    # Header style: ### Step 1: desc  /  ## Step 1 - desc
    ("hdr_step",   _re.compile(r'^\s*#{1,6}\s+[Ss]tep\s+(\d+)\s*[:\-–—.]\s*(.+)', _re.I)),
    # Word-based: Step 1: desc  /  Step 1 - desc
    ("word_step",  _re.compile(r'^[Ss]tep\s+(\d+)\s*[:\-–—.]\s*(.+)', _re.I)),
    # Standard numbered: 1. / 1) / 1:
    ("std_num",    _re.compile(r'^\s*(\d+)[.\):]\s+(.+)')),
    # Checkbox: - [ ] / - [x] / * [ ]
    ("checkbox",   _re.compile(r'^\s*[-*]\s+\[[ xX✓]\]\s+(.+)')),
]

_BULLET_PAT = _re.compile(r'^\s*[-*•]\s+(.+)')
_HEADING_PAT = _re.compile(r'^\s*#{1,6}\s+(.+)')

# Sub-field patterns for richer step format
_DESC_PAT = _re.compile(r'^[Dd]escription\s*[:\-]\s*(.+)')
_HOW_PAT  = _re.compile(r'^[Hh]ow(?:\s+to\s+(?:do\s+it|proceed|work))?\s*[:\-]\s*(.+)', _re.I)


def _match_step_line(line: str) -> tuple[int | None, str] | None:
    """Return (step_number | None, title_text) for a step line, else None."""
    for _, pat in _STEP_PATTERNS[:-1]:  # numbered patterns
        m = pat.match(line)
        if m:
            groups = m.groups()
            if len(groups) == 2:
                # Strip markdown bold/italic markers from both ends, then any trailing dash/colon
                raw = groups[1].strip()
                raw = _re.sub(r'^\*{1,2}|^\_{1,2}', '', raw)
                raw = _re.sub(r'\*{1,2}$|\_{1,2}$', '', raw)
                raw = raw.strip().rstrip(" —-:")
                return int(groups[0]), raw
    # Checkbox (auto-number)
    m = _STEP_PATTERNS[-1][1].match(line)
    if m:
        return None, m.group(1).strip()
    return None


def _parse_plan(raw: str) -> tuple[dict, list[dict]]:
    """Parse raw agent markdown into (plan_meta dict, steps list).

    Supports: numbered lists (1. 1) 1:), bold-numbered (**1.**), Step N headers,
    checkbox bullets, and plain bullets (auto-numbered fallback).

    Each step may carry three sub-fields written as labeled continuation lines
    immediately after the heading line:
      Description: what this step accomplishes
      How to do it: tools, commands, approach, expected output

    Multi-strategy: tries explicit numbered first; falls back to bullets if 0 steps found.
    """
    lines = raw.split("\n")
    title = ""
    desc_lines: list[str] = []
    auto_counter = 0
    found_first_step = False

    # --- First pass: collect step heading positions and plan-level text ---
    step_positions: list[tuple[int, int, str]] = []  # (line_idx, number, title_text)

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Plan title: first heading before any step
        if not found_first_step and _HEADING_PAT.match(line):
            heading_text = _HEADING_PAT.match(line).group(1).strip()  # type: ignore[union-attr]
            if not _re.match(r'[Ss]tep\s+\d+', heading_text, _re.I):
                if not title:
                    title = heading_text
                continue

        result = _match_step_line(line)
        if result is not None:
            found_first_step = True
            num, step_title = result
            if num is None:
                auto_counter += 1
                num = auto_counter
            else:
                auto_counter = max(auto_counter, num)
            step_positions.append((idx, num, step_title))
        elif not found_first_step and stripped:
            desc_lines.append(stripped)

    # Fallback: plain bullets if no steps found
    if not step_positions:
        auto_counter = 0
        for idx, line in enumerate(lines):
            m = _BULLET_PAT.match(line)
            if m:
                auto_counter += 1
                step_positions.append((idx, auto_counter, m.group(1).strip()))

    # --- Second pass: for each step, look ahead for Description: / How to do it: ---
    def _collect_subfields(start_line: int, end_line: int) -> tuple[str, str]:
        """Scan lines[start_line:end_line] for Description: and How to do it: labels."""
        desc_parts: list[str] = []
        how_parts: list[str] = []
        last_field: str = ""  # "desc" | "how" | ""

        for line in lines[start_line:end_line]:
            stripped = line.strip()
            if not stripped:
                continue
            dm = _DESC_PAT.match(stripped)
            if dm:
                desc_parts.append(dm.group(1).strip())
                last_field = "desc"
                continue
            hm = _HOW_PAT.match(stripped)
            if hm:
                how_parts.append(hm.group(1).strip())
                last_field = "how"
                continue
            # Indented continuation: append to last matched label
            if last_field and (line.startswith("   ") or line.startswith("\t")):
                if last_field == "desc":
                    desc_parts.append(stripped)
                elif last_field == "how":
                    how_parts.append(stripped)
                continue
            # Any other line (plain text, unrecognised label) — stop lookahead
            break

        return " ".join(desc_parts), " ".join(how_parts)

    # Deduplicate by step number (keep first), build final step dicts
    seen: set[int] = set()
    steps: list[dict] = []

    sorted_positions = sorted(step_positions, key=lambda x: x[0])
    for i, (line_idx, num, step_title) in enumerate(sorted_positions):
        if num in seen:
            continue
        seen.add(num)

        # Sub-fields live between this step's heading line and the next step's heading line
        next_line_idx = sorted_positions[i + 1][0] if i + 1 < len(sorted_positions) else len(lines)
        desc_text, how_text = _collect_subfields(line_idx + 1, next_line_idx)

        steps.append({
            "number":      num,
            "title":       step_title,
            "description": desc_text,
            "how_to":      how_text,
            "status":      "pending",
            "notes":       "",
            "reason":      "",
        })

    steps = sorted(steps, key=lambda s: s["number"])[:50]

    plan_meta: dict[str, Any] = {
        "title":       title or "Plan",
        "description": " ".join(desc_lines)[:400] if desc_lines else "",
        "total_steps": len(steps),
    }
    return plan_meta, steps


def _canonical_plan_md(plan_meta: dict, steps: list[dict]) -> str:
    """Render structured plan as beautiful, fully readable markdown for TUI display."""
    n = len(steps)
    about = plan_meta.get("description") or plan_meta.get("title", "Plan")

    lines: list[str] = [
        "Here **RAI** plan is ready:",
        "",
        f"**About the Plan:** {about}",
        "",
        "---",
        "",
    ]

    _STATUS_ICON = {
        "pending":     "⬜",
        "in_progress": "🔄",
        "done":        "✅",
        "blocked":     "🚫",
    }
    for s in steps:
        icon = _STATUS_ICON.get(s["status"], "⬜")
        # Use title as heading; fall back to description for old schema compat
        heading = s.get("title") or s.get("description", "—")
        lines.append(f"**{s['number']}. {heading}**  {icon}")

        step_desc = s.get("description", "")
        step_how  = s.get("how_to", "")
        # Only show description bullet if it differs from the heading
        # (avoids duplicating text when old-schema plans have no title)
        if step_desc and step_desc != heading:
            lines.append(f"   * {step_desc}")
        if step_how:
            lines.append(f"   * 🔧 {step_how}")
        if s.get("notes"):
            lines.append(f"   > _{s['notes']}_")
        if s.get("reason") and s["status"] == "blocked":
            lines.append(f"   > ⚠ {s['reason']}")
        lines.append("")

    step_word = "step" if n == 1 else "steps"
    lines.append(f"*{n} {step_word} total*")
    return "\n".join(lines)


def _get_step(steps: list[dict], n: int) -> dict | None:
    for s in steps:
        if s["number"] == n:
            return s
    return None


# ---------------------------------------------------------------------------
# Plan state persistence (keyed by run_id — NOT thread_id)
# ---------------------------------------------------------------------------

def _plan_state_path(run_id: str) -> _Path:
    return _Path.home() / ".rai" / "plans" / f"{run_id[:16]}.json"


def _persist_plan(run_id: str, reg: dict) -> None:
    """Write plan registry state to disk so it survives server restarts."""
    try:
        _plan_state_path(run_id).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "plan_data": reg.get("plan_data", {}),
            "plan_steps": reg.get("plan_steps", []),
            "plan": reg.get("plan", ""),
            "plan_file": reg.get("plan_file", ""),
            "status": reg.get("status", ""),
            "plan_mode": reg.get("plan_mode", False),
            "plan_approved": reg.get("plan_approved", False),
        }
        _plan_state_path(run_id).write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def _restore_plan(run_id: str) -> dict | None:
    """Load persisted plan state for run_id; return None if not found or mismatched."""
    try:
        p = _plan_state_path(run_id)
        if p.exists():
            data = json.loads(p.read_text())
            if data.get("run_id") == run_id:
                return data
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Context helper
# ---------------------------------------------------------------------------

def _get_run_context() -> dict:
    from rai.harness.subagents.registry import _RUN_CONTEXT
    ctx = _RUN_CONTEXT.get()
    if ctx is None:
        raise RuntimeError(
            "Plan tools called outside an HTTP run context. "
            "These tools only work when the agent is served via `rai http serve`."
        )
    return ctx


def _exec_gate(reg: dict) -> str | None:
    if reg.get("plan_approved"):
        return None
    if reg.get("plan_mode"):
        return _PLAN_MODE_REDIRECT
    return _HARNESS_GUIDE


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
async def enter_plan_mode() -> str:
    """Enter plan mode: switch to research-and-plan before executing anything.

    After calling this you MUST:
      1. Use ask_user() to gather the user's goals, scope, constraints, and
         expected outcomes. Ask clarifying questions until you have a detailed
         picture of what they want.
      2. Use ONLY read-only tools (Read, Glob, Grep, WebSearch, WebFetch, GET
         requests) to research. Do NOT write files, execute code, or make
         mutating API calls.
      3. Call write_plan() to submit a detailed numbered plan built from what
         the user told you. Execution begins only after they approve it.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    bus = ctx["parent_bus"]

    import rai.harness.runner as _runner

    # Check if a plan was already approved for this run_id (disk restore)
    saved = _restore_plan(run_id)
    if saved and saved.get("plan_approved") and saved.get("status") in ("running",):
        _runner._RUN_REGISTRY.setdefault(run_id, {}).update({
            "plan_mode":    False,
            "plan_approved": True,
            "plan_data":    saved.get("plan_data", {}),
            "plan_steps":   saved.get("plan_steps", []),
            "plan":         saved.get("plan", ""),
            "plan_file":    saved.get("plan_file", ""),
            "status":       "running",
        })
        return (
            "Plan state restored from disk — your approved plan is already active.\n"
            "Call list_plan_steps() to see current status and continue execution."
        )

    if run_id in _runner._RUN_REGISTRY:
        _runner._RUN_REGISTRY[run_id]["plan_mode"] = True
        _runner._RUN_REGISTRY[run_id]["plan_approved"] = False
        _runner._RUN_REGISTRY[run_id]["status"] = "planning"

    await bus.publish("plan_mode_entered", {"run_id": run_id})
    return (
        "Plan mode active.\n\n"
        "STEP 1 — Gather requirements: use ask_user() STRICTLY to ask the user:\n"
        "  • What is the exact goal or target?\n"
        "  • What outcomes do they expect from each phase?\n"
        "  • Any constraints, exclusions, priorities, or preferred approach?\n"
        "Keep asking until you have a clear, detailed picture of what they want.\n\n"
        "STEP 2 — Research: use ONLY read-only tools (Read, Glob, Grep, WebSearch,\n"
        "  WebFetch, GET requests). Do NOT write files, execute code, or make mutating calls.\n\n"
        "STEP 3 — Plan: call write_plan(content). Each step MUST use this three-line format:\n\n"
        "  N. **Title** — short action name (3-7 words)\n"
        "     Description: What this step accomplishes and why it is needed.\n"
        "     How to do it: Specific tools, commands, approach; expected output.\n\n"
        "Example:\n"
        "  1. **Enumerate API Endpoints**\n"
        "     Description: Map all exposed routes using the OpenAPI spec and live probing.\n"
        "     How to do it: Load spec with load_openapi_spec, verify with GET requests,\n"
        "       use gobuster for undocumented routes. Expected: full endpoint list.\n\n"
        "Execution begins only after the user approves the plan."
    )


@tool
async def write_plan(content: str, slug: str = "") -> str:
    """Submit your research as a structured plan for user approval before executing.

    Call this AFTER using ask_user() to gather requirements and exploring with
    read-only tools. Execution pauses until the user approves or rejects.

    Each step MUST use this three-line format:

      N. **Title** — short action name (3-7 words)
         Description: What this step accomplishes and why it is needed.
         How to do it: Specific tools, commands, approach; expected output.

    Example:
      1. **Enumerate API Endpoints**
         Description: Map all exposed routes using the OpenAPI spec and live probing.
         How to do it: Load spec with load_openapi_spec, verify with GET requests,
           use gobuster for undocumented routes. Expected: full endpoint list.

    Args:
        content: Full plan in markdown. Must reflect what the user told you via
                 ask_user(). Start with a title (# heading) and a brief description
                 of the overall goal, then numbered steps in the format above.
        slug: Short name for the plan file, e.g. "recon-phase-1". Defaults to run_id.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    bus = ctx["parent_bus"]

    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}

    if reg.get("plan_approved"):
        return _PLAN_APPROVED_REDIRECT

    if not reg.get("plan_mode"):
        return _HARNESS_GUIDE

    # Parse into structured data
    plan_meta, steps = _parse_plan(content)
    if not steps:
        return (
            "<plan-error>\n"
            "No steps were parsed from your plan. Ensure each step is on its own line "
            "using a numbered list format:\n\n"
            "  1. First step description\n"
            "  2. Second step description\n\n"
            "Revise and call write_plan() again.\n"
            "</plan-error>"
        )

    plan_meta["total_steps"] = len(steps)

    # Save raw markdown plan file
    plans_dir = _Path.home() / ".rai" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plans_dir / f"{slug or run_id[:12]}.md"
    plan_file.write_text(content)

    agent_name = reg.get("agent_name", "")
    _runner._RUN_REGISTRY[run_id].update({
        "plan":       content,
        "plan_data":  plan_meta,
        "plan_steps": steps,
        "plan_file":  str(plan_file),
        "status":     "plan_ready",
    })

    # Render canonical formatted markdown for TUI display
    canonical_md = _canonical_plan_md(plan_meta, steps)

    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _runner._PLAN_FUTURES[run_id] = fut

    await bus.publish("plan_ready", {
        "run_id":      run_id,
        "plan":        canonical_md,
        "plan_file":   str(plan_file),
        "approve_url": f"/agents/{agent_name}/runs/{run_id}/plan/approve",
        "reject_url":  f"/agents/{agent_name}/runs/{run_id}/plan/reject",
    })

    try:
        decision = await asyncio.wait_for(fut, timeout=3600.0)
    except asyncio.TimeoutError:
        _runner._RUN_REGISTRY[run_id]["status"] = "failed"
        return "Plan approval timed out after 1 hour."
    finally:
        _runner._PLAN_FUTURES.pop(run_id, None)

    def _apply_approval(edited_content: str | None = None) -> None:
        if edited_content:
            new_meta, new_steps = _parse_plan(edited_content)
            new_meta["total_steps"] = len(new_steps)
            _runner._RUN_REGISTRY[run_id].update({
                "plan":       edited_content,
                "plan_data":  new_meta,
                "plan_steps": new_steps,
            })
        _runner._RUN_REGISTRY[run_id].update({
            "status":       "running",
            "plan_mode":    False,
            "plan_approved": True,
        })
        _persist_plan(run_id, _runner._RUN_REGISTRY[run_id])

    _EXEC_REMINDER = (
        "For each step: enter_step(N) → do the work → mark_step_done(N, notes=...).\n"
        "Use notes to capture key findings — they feed your self-learning memory at exit.\n"
        "Call exit_plan_mode() when all steps are complete."
    )

    if decision["action"] == "approve":
        _apply_approval()
        await bus.publish("plan_approved", {"run_id": run_id})
        step_list = "\n".join(
            f"  {s['number']}. {s.get('title') or s.get('description', '—')}"
            for s in _runner._RUN_REGISTRY[run_id]["plan_steps"]
        )
        return (
            f"Plan approved ({plan_meta['total_steps']} steps).\n\n"
            f"Steps:\n{step_list}\n\n"
            f"{_EXEC_REMINDER}"
        )

    if decision["action"] == "edit":
        edited = decision.get("feedback") or content
        _apply_approval(edited)
        await bus.publish("plan_approved", {"run_id": run_id})
        new_steps = _runner._RUN_REGISTRY[run_id]["plan_steps"]
        step_list = "\n".join(
            f"  {s['number']}. {s.get('title') or s.get('description', '—')}"
            for s in new_steps
        )
        return (
            f"Plan edited and approved ({len(new_steps)} steps).\n\n"
            f"Steps:\n{step_list}\n\n"
            f"{_EXEC_REMINDER}"
        )

    if decision["action"] == "respond":
        guidance = decision.get("feedback") or "No guidance provided."
        _runner._RUN_REGISTRY[run_id]["status"] = "planning"
        await bus.publish("plan_rejected", {"run_id": run_id, "feedback": guidance})
        return (
            f"User provided plan guidance:\n\n{guidance}\n\n"
            "Incorporate this guidance to write an improved plan. "
            "Call write_plan() again with the updated plan."
        )

    feedback = decision.get("feedback") or "No feedback provided."
    _runner._RUN_REGISTRY[run_id]["status"] = "planning"
    await bus.publish("plan_rejected", {"run_id": run_id, "feedback": feedback})
    return (
        f"Plan rejected. User feedback: {feedback}\n\n"
        "Revise your plan and call write_plan() again."
    )


@tool
async def list_plan_steps() -> str:
    """List all steps in the current approved plan with their status.

    Returns JSON: {plan_title, steps: [{number, title, description, how_to,
    status, notes, reason}], total, done, blocked, in_progress, pending}.
    Call this to see what remains before deciding which step to execute next.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _exec_gate(reg)
    if redirect:
        return redirect
    steps = reg.get("plan_steps", [])
    if not steps:
        return json.dumps({"error": "No plan steps found. Call enter_plan_mode() and write_plan() first."})
    plan_data = reg.get("plan_data", {})
    by_status: dict[str, int] = {}
    for s in steps:
        by_status[s["status"]] = by_status.get(s["status"], 0) + 1
    return json.dumps({
        "plan_title":  plan_data.get("title", "Plan"),
        "total":       len(steps),
        "done":        by_status.get("done", 0),
        "blocked":     by_status.get("blocked", 0),
        "in_progress": by_status.get("in_progress", 0),
        "pending":     by_status.get("pending", 0),
        "steps":       steps,
    }, indent=2)


@tool
async def enter_step(step_number: int) -> str:
    """Mark a plan step as in_progress before you begin executing it.

    Call this BEFORE starting work on each step. Publishes step_start SSE
    (▶ Step N in TUI). Then call mark_step_done() or mark_step_blocked() when done.

    Args:
        step_number: 1-based step number from the approved plan.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _exec_gate(reg)
    if redirect:
        return redirect

    steps = reg.get("plan_steps", [])
    step = _get_step(steps, step_number)

    if step is None:
        valid = [s["number"] for s in steps]
        return (
            f"Step {step_number} not found. Valid step numbers: {valid}.\n"
            "Call list_plan_steps() to review the full plan."
        )
    if step["status"] == "done":
        return (
            f"Step {step_number} is already marked done. "
            "Call list_plan_steps() to see which steps still need work."
        )
    if step["status"] == "blocked":
        return (
            f"Step {step_number} is already blocked. "
            "Call list_plan_steps() to see remaining steps."
        )
    if step["status"] == "in_progress":
        return (
            f"Step {step_number} is already in progress. "
            f"Complete the work then call mark_step_done({step_number}) or "
            f"mark_step_blocked({step_number}, reason)."
        )

    step["status"] = "in_progress"
    _persist_plan(run_id, reg)
    label = step.get("title") or step.get("description", "—")
    await bus.publish("step_start", {
        "run_id":      run_id,
        "step_number": step_number,
        "description": label,
    })
    msg = f"Step {step_number} started: {label}"
    if step.get("description") and step.get("title"):
        msg += f"\n  What: {step['description']}"
    if step.get("how_to"):
        msg += f"\n  🔧 Approach: {step['how_to']}"
    return msg


@tool
async def mark_step_done(step_number: int, notes: str = "") -> str:
    """Mark a numbered plan step as complete during plan execution.

    Call this after completing a step's work. Requires enter_step(N) to have
    been called first — warns and proceeds if it was skipped.

    Args:
        step_number: The 1-based step number to mark complete.
        notes: Key findings, discoveries, or observations from this step.
               Used in self-learning memory at exit_plan_mode(). Be specific.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _exec_gate(reg)
    if redirect:
        return redirect

    steps = reg.get("plan_steps", [])
    step = _get_step(steps, step_number)

    if step is None:
        valid = [s["number"] for s in steps]
        return (
            f"Step {step_number} not found. Valid step numbers: {valid}.\n"
            "Call list_plan_steps() to review the full plan."
        )
    if step["status"] == "done":
        return (
            f"Step {step_number} is already marked done."
            + (f" Notes: {step.get('notes', '')}" if step.get("notes") else "")
        )
    if step["status"] == "blocked":
        return (
            f"Step {step_number} is blocked — cannot mark done. "
            "If the block was resolved, call enter_step() again to restart it."
        )

    warning = ""
    if step["status"] == "pending":
        # Auto-advance: pending → in_progress → done (warn agent to use enter_step next time)
        warning = (
            f"Note: enter_step({step_number}) was not called before marking done. "
            "Call enter_step(N) before starting each step to keep step tracking accurate.\n"
        )

    step["status"] = "done"
    if notes:
        step["notes"] = notes
    _persist_plan(run_id, reg)

    label = step.get("title") or step.get("description", "—")
    await bus.publish("step_complete", {
        "run_id":      run_id,
        "step_number": step_number,
        "description": label,
        "notes":       notes,
    })

    remaining = [s for s in steps if s["status"] not in ("done", "blocked")]
    remaining_msg = (
        f"  {len(remaining)} step(s) remaining."
        if remaining
        else "  All steps resolved — call exit_plan_mode()."
    )
    return f"{warning}Step {step_number} ({label}) marked complete.{remaining_msg}"


@tool
async def mark_step_blocked(step_number: int, reason: str = "") -> str:
    """Mark a plan step as blocked when it cannot proceed.

    Publishes step_blocked SSE (✗ Step N in TUI). Blocked steps count as
    resolved — they do not prevent exit_plan_mode() from succeeding.

    Args:
        step_number: 1-based step number.
        reason: Why the step is blocked. Be specific — this appears in the summary.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _exec_gate(reg)
    if redirect:
        return redirect

    steps = reg.get("plan_steps", [])
    step = _get_step(steps, step_number)

    if step is None:
        valid = [s["number"] for s in steps]
        return f"Step {step_number} not found. Valid step numbers: {valid}."
    if step["status"] == "done":
        return f"Step {step_number} is already marked done. Cannot block a completed step."
    if step["status"] == "blocked":
        return f"Step {step_number} is already blocked: {step.get('reason', '')}."

    step["status"] = "blocked"
    if reason:
        step["reason"] = reason
    _persist_plan(run_id, reg)

    label = step.get("title") or step.get("description", "—")
    await bus.publish("step_blocked", {
        "run_id":      run_id,
        "step_number": step_number,
        "description": label,
        "reason":      reason,
    })

    remaining = [s for s in steps if s["status"] not in ("done", "blocked")]
    remaining_msg = (
        f"  {len(remaining)} step(s) still pending."
        if remaining
        else "  All steps resolved — call exit_plan_mode()."
    )
    return f"Step {step_number} ({label}) blocked: {reason or '(no reason given)'}.{remaining_msg}"


@tool
async def exit_plan_mode() -> str:
    """Signal that plan execution is complete and request to end the run.

    Hard gate: checks all plan steps before allowing exit.
    - Not in approved plan → <plan-required> harness guide.
    - Steps still pending/in_progress → <plan-reminder> listing incomplete steps.
    - All steps done or blocked → deletes plan file, emits plan_completed SSE,
      returns the self-learning memory prompt so the agent consolidates knowledge.

    Always call this when you believe execution is complete.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner

    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _exec_gate(reg)
    if redirect:
        return redirect

    steps = reg.get("plan_steps", [])
    incomplete = [s for s in steps if s["status"] not in ("done", "blocked")]

    if incomplete:
        lines = [
            "<plan-reminder>",
            f"{len(incomplete)} of {len(steps)} steps still unresolved — you CANNOT exit yet:",
        ]
        for s in incomplete:
            label = s.get("title") or s.get("description", "—")
            lines.append(f"  {s['number']}. [{s['status']}] {label}")
        lines += [
            "",
            "For each: enter_step(N) → do the work → mark_step_done(N, notes=...).",
            "Or mark_step_blocked(N, reason) if a step cannot proceed.",
            "Then call exit_plan_mode() again.",
            "</plan-reminder>",
        ]
        return "\n".join(lines)

    _runner._RUN_REGISTRY[run_id]["status"] = "completed"
    plan_data = reg.get("plan_data", {})
    plan_file = reg.get("plan_file", "")
    total_steps = len(steps)

    if plan_file:
        try:
            _Path(plan_file).unlink(missing_ok=True)
        except OSError:
            pass

    # Clean up persisted state file now that the plan is done
    try:
        _plan_state_path(run_id).unlink(missing_ok=True)
    except Exception:
        pass

    await bus.publish("plan_completed", {
        "run_id":      run_id,
        "plan_file":   plan_file,
        "total_steps": total_steps,
    })

    done_steps    = [s for s in steps if s["status"] == "done"]
    blocked_steps = [s for s in steps if s["status"] == "blocked"]

    notes_lines: list[str] = []
    for s in steps:
        note   = s.get("notes", "")
        reason = s.get("reason", "")
        suffix = f" — {note}" if note else (f" — blocked: {reason}" if reason else "")
        label  = s.get("title") or s.get("description", "—")
        notes_lines.append(f"  {s['number']}. [{s['status']}] {label}{suffix}")
    step_digest = "\n".join(notes_lines) if notes_lines else "  (no steps recorded)"

    return (
        f"All plan steps complete "
        f"({len(done_steps)} done, {len(blocked_steps)} blocked) — "
        f"'{plan_data.get('title', 'Plan')}'\n\n"
        "── SELF-LEARNING MEMORY PHASE ──────────────────────────────────────────\n"
        "Before writing your final summary, reflect on what you learned and write\n"
        "memory entries using memory_write (scope='agent') for anything worth\n"
        "preserving for future runs. Use the step digest below as your source.\n\n"
        f"Step execution digest:\n{step_digest}\n\n"
        "Write entries for each category that applies:\n\n"
        "  🎯 TARGET / PROJECT FACTS\n"
        "     New non-obvious knowledge: tech stack, endpoints, credential patterns,\n"
        "     API quirks, exposed services, config specifics, architecture details.\n\n"
        "  🔬 METHODOLOGY\n"
        "     What research or execution approaches worked well (or poorly).\n"
        "     Techniques, tool combinations, ordering that was effective.\n\n"
        "  🚧 BLOCKERS & WORKAROUNDS\n"
        "     What was blocked, why, and how resolved (or not).\n\n"
        "  💡 LESSONS LEARNED\n"
        "     Edge cases hit, wrong assumptions corrected, things to do differently.\n\n"
        "Skip categories where you have nothing new to record.\n"
        "────────────────────────────────────────────────────────────────────────\n\n"
        "After writing memories, provide your final summary: what was accomplished,\n"
        "key findings, anything blocked and why, and recommended next steps."
    )


def get_plan_tools() -> list:
    """Return all plan-mode tools to inject into the HTTP parent agent."""
    return [
        enter_plan_mode,
        write_plan,
        list_plan_steps,
        enter_step,
        mark_step_done,
        mark_step_blocked,
        exit_plan_mode,
    ]
