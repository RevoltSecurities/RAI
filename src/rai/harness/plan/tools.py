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
from datetime import UTC, datetime
from pathlib import Path as _Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Structured plan input schema
# ---------------------------------------------------------------------------

_VALID_PRIORITIES = ("critical", "high", "normal", "low")


class PlanStep(BaseModel):
    """Single plan step — validated at the tool boundary."""
    title: str
    description: str = ""
    how_to: str = ""
    depends_on: list[int] = []
    priority: str = "normal"

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Step title cannot be empty")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        if v not in _VALID_PRIORITIES:
            raise ValueError(f"priority must be one of {_VALID_PRIORITIES}, got '{v}'")
        return v


def _validate_steps(steps: list[dict]) -> tuple[list[dict], str | None]:
    """Validate structured step input. Returns (validated_steps, error_msg | None)."""
    if not steps:
        return [], "At least 1 step is required."
    if len(steps) > 50:
        return [], "Maximum 50 steps allowed."

    validated: list[dict] = []
    errors: list[str] = []

    for i, raw in enumerate(steps, 1):
        try:
            step = PlanStep(**raw)
        except Exception as e:
            errors.append(f"Step {i}: {e}")
            continue
        validated.append({
            "number": i,
            "title": step.title,
            "description": step.description,
            "how_to": step.how_to,
            "depends_on": step.depends_on,
            "priority": step.priority,
            "status": "pending",
            "notes": "",
            "reason": "",
        })

    if errors:
        return [], "Step validation failed:\n" + "\n".join(errors)

    # Validate depends_on references
    valid_nums = {s["number"] for s in validated}
    for s in validated:
        for dep in s["depends_on"]:
            if dep not in valid_nums:
                return [], f"Step {s['number']} depends_on step {dep} which doesn't exist."

    # Circular dependency check (topological sort)
    dep_graph: dict[int, list[int]] = {s["number"]: s["depends_on"] for s in validated}
    visited: set[int] = set()
    in_stack: set[int] = set()

    def _has_cycle(node: int) -> bool:
        if node in in_stack:
            return True
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        for dep in dep_graph.get(node, []):
            if _has_cycle(dep):
                return True
        in_stack.discard(node)
        return False

    for num in valid_nums:
        if _has_cycle(num):
            return [], f"Circular dependency detected involving step {num}."

    return validated, None


def _apply_sequential_deps(steps: list[dict]) -> None:
    """For steps with no explicit depends_on, inject depends_on=[N-1] (sequential mode)."""
    for s in steps:
        if not s["depends_on"] and s["number"] > 1:
            s["depends_on"] = [s["number"] - 1]


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
  4. write_plan(title, description, steps)
     — submit structured steps for approval (each step: {title, description, how_to, depends_on, priority})
  5. (execution pauses — wait for the user to approve or reject)
  6. enter_step(N)                  — mark step in_progress (blocked if depends_on unmet)
  7. (perform the step's work)
  8. mark_step_done(N, notes=...)   — record completion; use notes for key findings
     OR mark_step_blocked(N, reason) if the step cannot proceed
  9. Repeat steps 6–8 for every numbered step in the plan
 10. exit_plan_mode()               — gate-check all steps, write self-learning memories

ENFORCED RULES (harness will REJECT violations):
  • write_plan() requires structured steps — title is mandatory for each
  • Steps with depends_on CANNOT be entered until dependencies are done
  • Plans MUST be approved before execution — rejected plans require revision
  • sequential=True (default) means Step N requires Step N-1 to be done
  • Scope changes → call add_steps(), do NOT exit plan mode
  • Critical findings → call escalate_step() to prioritize follow-up

Call enter_plan_mode() now to begin.
</plan-required>"""

_PLAN_MODE_REDIRECT = """\
<plan-required>
You are currently in exploration mode (plan_mode active).
Execution tools are not available until your plan is approved.

Use ask_user() strictly to interact with the user — ask clarifying questions about:
  • Goal and scope of the task
  • Expected outcomes for each phase
  • Constraints, exclusions, or priorities

Once you have a clear picture of what they want, call write_plan(title=...,
description=..., steps=[...]) with structured step definitions.
Execution will begin only after approval.
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
# Plan state persistence (keyed by thread_id — survives across runs)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _map_status(reg: dict) -> str:
    """Derive human-readable plan status from registry state."""
    if reg.get("plan_approved"):
        steps = reg.get("plan_steps", [])
        incomplete = [s for s in steps if s.get("status") not in ("done", "blocked")]
        return "completed" if not incomplete else "executing"
    if reg.get("plan_mode"):
        return "planning"
    return "unknown"


def _plan_state_path(thread_id: str) -> _Path:
    """Plan state persisted per thread (session), not per run."""
    return _Path.home() / ".rai" / "plans" / f"{thread_id}.json"


def _persist_plan(thread_id: str, reg: dict) -> None:
    """Write plan state to ~/.rai/plans/{thread_id}.json — survives restarts."""
    try:
        path = _plan_state_path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                pass

        payload = {
            "thread_id": thread_id,
            "run_id": reg.get("run_id", ""),
            "created_at": existing.get("created_at") or _now_iso(),
            "updated_at": _now_iso(),
            "status": _map_status(reg),
            "title": reg.get("plan_data", {}).get("title", "Plan"),
            "description": reg.get("plan_data", {}).get("description", ""),
            "plan_raw": reg.get("plan", ""),
            "plan_file": reg.get("plan_file", ""),
            "steps": reg.get("plan_steps", []),
        }
        path.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass


def _restore_plan(thread_id: str) -> dict | None:
    """Load persisted plan state for thread; None if not found."""
    try:
        p = _plan_state_path(thread_id)
        if p.exists():
            data = json.loads(p.read_text())
            if data.get("thread_id") == thread_id:
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


def _task_phase_gate(reg: dict) -> str | None:
    """Gate for task-phase-only tools (enter_step, mark_step_done, etc.).

    Returns None if allowed, or a redirect message if not in task phase.
    """
    if reg.get("plan_approved"):
        return None
    if reg.get("plan_mode"):
        return (
            "<plan-harness-restricted>\n"
            "REJECTED: This tool is only available during Task Phase (after plan approval).\n"
            "CURRENT PHASE: Plan (research only).\n"
            "ACTION: Complete your research, then call write_plan(title, description, steps) "
            "to submit a structured plan. This tool becomes available after user approval.\n"
            "</plan-harness-restricted>"
        )
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
    thread_id = ctx["thread_id"]
    bus = ctx["parent_bus"]

    import rai.harness.runner as _runner

    # Check for existing plan on this thread (persisted across runs)
    saved = _restore_plan(thread_id)
    if saved and saved.get("status") not in ("completed",):
        incomplete = [s for s in saved.get("steps", []) if s.get("status") not in ("done", "blocked")]
        if incomplete:
            _runner._RUN_REGISTRY.setdefault(run_id, {}).update({
                "plan_mode":    False,
                "plan_approved": True,
                "plan_data":    {"title": saved.get("title", "Plan"), "description": saved.get("description", "")},
                "plan_steps":   saved.get("steps", []),
                "plan":         saved.get("plan_raw", ""),
                "plan_file":    saved.get("plan_file", ""),
                "status":       "running",
                "run_id":       run_id,
            })
            _persist_plan(thread_id, _runner._RUN_REGISTRY[run_id])
            done_count = len(saved["steps"]) - len(incomplete)
            inc_list = "\n".join(
                f"  {s['number']}. {s.get('title') or s.get('description', '—')}"
                for s in incomplete
            )
            return (
                "<plan-info>\n"
                f"Plan restored: '{saved.get('title', 'Plan')}' ({done_count} done, {len(incomplete)} remaining)\n"
                f"Incomplete:\n{inc_list}\n\n"
                f"Next: enter_step({incomplete[0]['number']})\n"
                "</plan-info>"
            )

    if run_id in _runner._RUN_REGISTRY:
        _runner._RUN_REGISTRY[run_id].update({
            "plan_mode":     True,
            "plan_approved": False,
            "plan_steps":    [],
            "plan_data":     {},
            "plan":          "",
            "plan_file":     "",
            "status":        "planning",
            "run_id":        run_id,
        })
        _persist_plan(thread_id, _runner._RUN_REGISTRY[run_id])

    await bus.publish("plan_mode_entered", {"run_id": run_id})
    return (
        "<plan-info>\n"
        "Plan mode active. Workflow:\n"
        "1. ask_user() — gather goals, scope, constraints\n"
        "2. Research with read-only tools (Read, Grep, WebSearch, GET)\n"
        "3. write_plan(title=..., description=..., steps=[...]) — structured steps:\n"
        "   {\"title\": \"...\", \"description\": \"...\", \"how_to\": \"...\", \"depends_on\": [], \"priority\": \"normal\"}\n"
        "   sequential=True (default) auto-enforces linear ordering.\n"
        "Execution begins only after user approves.\n"
        "</plan-info>"
    )


@tool
async def write_plan(
    title: str = "",
    description: str = "",
    steps: list[dict] | None = None,
    sequential: bool = True,
    slug: str = "",
    content: str = "",
) -> str:
    """Submit a structured plan for user approval before executing.

    Call this AFTER using ask_user() to gather requirements and exploring with
    read-only tools. Execution pauses until the user approves or rejects.

    PREFERRED: Pass structured steps (validated by the harness — malformed plans
    are impossible):

        write_plan(
            title="API Security Assessment",
            description="Full OWASP Top 10 assessment of the target API",
            steps=[
                {"title": "Enumerate Endpoints", "description": "Map all routes", "how_to": "Load OpenAPI spec + gobuster"},
                {"title": "Test Authentication", "description": "Verify auth bypass", "depends_on": [1]},
                {"title": "Check Injection", "priority": "critical", "depends_on": [1]},
            ],
            sequential=True,  # auto-adds depends_on=[N-1] when not explicit
        )

    Step schema (title is required, rest optional):
        title:       str  — short action name (3-7 words)
        description: str  — what this step accomplishes
        how_to:      str  — tools, commands, approach
        depends_on:  list[int]  — step numbers that must complete first
        priority:    str  — "critical" | "high" | "normal" | "low"

    FALLBACK: Pass content= with markdown (legacy — parsed with regex, may fail).

    Args:
        title: Plan title (e.g. "Recon Phase 1").
        description: Brief description of the overall goal.
        steps: List of step dicts. Each must have at least a 'title' field.
        sequential: If True (default), steps without explicit depends_on auto-get
                    depends_on=[N-1], enforcing linear execution.
        slug: Short name for the plan file. Defaults to run_id prefix.
        content: Legacy markdown fallback. Ignored if steps is provided.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    thread_id = ctx["thread_id"]
    bus = ctx["parent_bus"]

    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}

    if reg.get("plan_approved"):
        return _PLAN_APPROVED_REDIRECT

    if not reg.get("plan_mode"):
        return _HARNESS_GUIDE

    # Track rejection count
    reject_count = reg.get("_reject_count", 0)
    if reject_count >= 5:
        _runner._RUN_REGISTRY[run_id]["status"] = "failed"
        return (
            "<plan-error>\n"
            "Plan rejected 5 times. Run aborted — the user's requirements cannot be met "
            "with the current approach. End the conversation and ask them to start fresh.\n"
            "</plan-error>"
        )

    # --- Structured input path (preferred) ---
    if steps is not None:
        validated, error = _validate_steps(steps)
        if error:
            return (
                f"<plan-error>\n{error}\n\n"
                "Fix the step definitions and call write_plan() again.\n"
                "Each step needs at minimum: {\"title\": \"...\"}\n"
                "</plan-error>"
            )
        if sequential:
            _apply_sequential_deps(validated)
        plan_meta = {
            "title": title or "Plan",
            "description": description,
            "total_steps": len(validated),
        }
        plan_steps = validated
        raw_content = _canonical_plan_md(plan_meta, plan_steps)

    # --- Legacy markdown fallback ---
    elif content:
        plan_meta, plan_steps = _parse_plan(content)
        if not plan_steps:
            return (
                "<plan-error>\n"
                "No steps were parsed from your plan. Use STRUCTURED input instead:\n\n"
                "  write_plan(\n"
                "    title=\"...\",\n"
                "    description=\"...\",\n"
                "    steps=[{\"title\": \"Step 1\"}, {\"title\": \"Step 2\"}]\n"
                "  )\n\n"
                "Each step needs at minimum a 'title' field.\n"
                "</plan-error>"
            )
        plan_meta["total_steps"] = len(plan_steps)
        raw_content = content
    else:
        return (
            "<plan-error>\n"
            "No plan provided. You must pass either:\n"
            "  • steps=[{\"title\": \"...\", ...}, ...] — structured (preferred)\n"
            "  • content=\"...\" — legacy markdown fallback\n\n"
            "Use structured steps for reliable plan creation.\n"
            "</plan-error>"
        )

    # Save plan file
    plans_dir = _Path.home() / ".rai" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plans_dir / f"{slug or run_id[:12]}.md"
    plan_file.write_text(raw_content)

    agent_name = reg.get("agent_name", "")
    _runner._RUN_REGISTRY[run_id].update({
        "plan":       raw_content,
        "plan_data":  plan_meta,
        "plan_steps": plan_steps,
        "plan_file":  str(plan_file),
        "status":     "plan_ready",
    })

    # Render canonical formatted markdown for TUI display
    canonical_md = _canonical_plan_md(plan_meta, plan_steps)

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
        decision = await asyncio.wait_for(fut, timeout=18000.0)
    except asyncio.TimeoutError:
        _runner._RUN_REGISTRY[run_id].update({
            "status": "running",
            "plan_mode": False,
            "plan_approved": False,
            "plan_steps": [],
            "plan_data": {},
            "plan": "",
            "plan_file": "",
        })
        _persist_plan(thread_id, _runner._RUN_REGISTRY[run_id])
        await bus.publish("plan_timeout", {"run_id": run_id})
        return (
            "<system-info>\n"
            "Plan approval timed out. Plan mode exited — all tools restored.\n"
            "Continue working in normal mode or call enter_plan_mode() to retry.\n"
            "</system-info>"
        )
    finally:
        _runner._PLAN_FUTURES.pop(run_id, None)

    def _apply_approval(edited_content: str | None = None) -> None:
        if edited_content:
            new_meta, new_steps = _parse_plan(edited_content)
            new_meta["total_steps"] = len(new_steps)
            if new_steps:
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
        _persist_plan(thread_id, _runner._RUN_REGISTRY[run_id])

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
        edited = decision.get("feedback") or raw_content
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
        _runner._RUN_REGISTRY[run_id]["_reject_count"] = reject_count + 1
        await bus.publish("plan_rejected", {"run_id": run_id, "feedback": guidance})
        return (
            "<plan-rejected>\n"
            f"User feedback: {guidance}\n\n"
            "Incorporate this feedback and call write_plan() again with revised steps.\n"
            f"Attempts remaining: {4 - reject_count}\n"
            "</plan-rejected>"
        )

    # Explicit reject
    feedback = decision.get("feedback") or "No feedback provided."
    _runner._RUN_REGISTRY[run_id]["status"] = "planning"
    _runner._RUN_REGISTRY[run_id]["_reject_count"] = reject_count + 1
    await bus.publish("plan_rejected", {"run_id": run_id, "feedback": feedback})
    return (
        "<plan-rejected>\n"
        f"Plan rejected. Feedback: {feedback}\n\n"
        "Revise your plan and call write_plan() again.\n"
        f"Attempts remaining: {4 - reject_count}\n"
        "</plan-rejected>"
    )


@tool
async def read_plan() -> str:
    """Load the current plan for this session/thread.

    Returns the plan with status of each step. Use this to resume work on a
    previously started plan or check progress. The plan file is automatically
    resolved from the current thread/session — no filepath needed.
    """
    ctx = _get_run_context()
    thread_id = ctx["thread_id"]
    run_id = ctx["run_id"]

    saved = _restore_plan(thread_id)
    if not saved:
        return (
            "<plan-info>\n"
            "No plan exists for this session.\n"
            "Call enter_plan_mode() to start planning.\n"
            "</plan-info>"
        )

    # Restore into _RUN_REGISTRY if not already loaded
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id, {})
    if not reg.get("plan_steps") and saved.get("status") != "completed":
        _runner._RUN_REGISTRY.setdefault(run_id, {}).update({
            "plan_data":    {"title": saved.get("title", "Plan"), "description": saved.get("description", "")},
            "plan_steps":   saved.get("steps", []),
            "plan":         saved.get("plan_raw", ""),
            "plan_file":    saved.get("plan_file", ""),
            "plan_mode":    saved["status"] == "planning",
            "plan_approved": saved["status"] in ("executing",),
            "run_id":       run_id,
        })

    steps = saved.get("steps", [])
    done = [s for s in steps if s.get("status") == "done"]
    blocked = [s for s in steps if s.get("status") == "blocked"]
    incomplete = [s for s in steps if s.get("status") not in ("done", "blocked")]

    lines = [
        "<plan-status>",
        f"Plan: {saved.get('title', 'Plan')}",
        f"Status: {saved['status']} | {len(done)} done, {len(blocked)} blocked, {len(incomplete)} remaining",
        "",
    ]

    # Done/blocked steps: compact (title only — agent doesn't need details for completed work)
    for s in steps:
        status = s.get("status", "pending")
        title = s.get("title") or s.get("description", "—")
        if status == "done":
            lines.append(f"  {s['number']}. [x] {title}")
        elif status == "blocked":
            reason = s.get("reason", "")
            lines.append(f"  {s['number']}. [!] {title}" + (f" — {reason[:60]}" if reason else ""))
        elif status == "in_progress":
            lines.append(f"  {s['number']}. [>] {title}")
            if s.get("how_to"):
                lines.append(f"       How to: {s['how_to']}")
        else:
            # Pending: show full detail so agent knows what to do
            lines.append(f"  {s['number']}. [ ] {title}")
            if s.get("description") and s.get("description") != title:
                lines.append(f"       {s['description']}")
            if s.get("how_to"):
                lines.append(f"       How to: {s['how_to']}")

    lines.append("")
    if not incomplete:
        lines.append("All steps resolved. Call exit_plan_mode() to finalize.")
    else:
        next_step = incomplete[0]["number"]
        lines.append(f"Next: enter_step({next_step}) → do work → mark_step_done({next_step})")
    lines.append("</plan-status>")
    return "\n".join(lines)


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
    steps = reg.get("plan_steps", [])
    if not steps:
        return json.dumps({"error": "No plan steps found. Call enter_plan_mode() and write_plan() first."})
    plan_data = reg.get("plan_data", {})
    by_status: dict[str, int] = {}
    for s in steps:
        by_status[s["status"]] = by_status.get(s["status"], 0) + 1
    # Compact done/blocked steps — strip description/how_to/full-notes to save tokens.
    # Pending and in_progress steps are returned in full so the agent has complete detail.
    # notes are summarised to 150 chars for done steps (full notes in mark_step_done ToolMessages).
    _PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}

    def _compact_step(s: dict) -> dict:
        status = s.get("status", "")
        if status in ("done", "blocked"):
            notes = s.get("notes", "") or ""
            reason = s.get("reason", "") or ""
            return {
                "number": s["number"],
                "title":  s.get("title") or s.get("description", ""),
                "status": status,
                "notes":  (notes[:150] + "…") if len(notes) > 150 else notes,
                "reason": (reason[:150] + "…") if len(reason) > 150 else reason,
            }
        # pending/in_progress: full detail (includes priority if set)
        return dict(s)

    # Sort pending/in_progress by priority (critical first) for clear next-action
    def _sort_key(s: dict) -> tuple:
        status_order = {"in_progress": 0, "pending": 1, "done": 2, "blocked": 3}
        return (
            status_order.get(s.get("status", "pending"), 1),
            _PRIORITY_ORDER.get(s.get("priority", "normal"), 2),
            s["number"],
        )

    sorted_steps = sorted(steps, key=_sort_key)

    return json.dumps({
        "plan_title":  plan_data.get("title", "Plan"),
        "total":       len(steps),
        "done":        by_status.get("done", 0),
        "blocked":     by_status.get("blocked", 0),
        "in_progress": by_status.get("in_progress", 0),
        "pending":     by_status.get("pending", 0),
        "steps":       [_compact_step(s) for s in sorted_steps],
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
    thread_id = ctx["thread_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _task_phase_gate(reg)
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

    # --- Phase gating: enforce depends_on ---
    deps = step.get("depends_on", [])
    if deps:
        unmet = []
        for dep_num in deps:
            dep_step = _get_step(steps, dep_num)
            if dep_step is None:
                continue  # dangling dep ref — treat as satisfied (step was removed/renumbered)
            if dep_step["status"] not in ("done", "blocked"):
                unmet.append(dep_step)
        if unmet:
            unmet_lines = [
                f"  • Step {s['number']}: {s.get('title', '—')} [{s['status']}]"
                for s in unmet
            ]
            return (
                f"<step-gated>\n"
                f"Cannot enter Step {step_number} — dependencies not met:\n"
                + "\n".join(unmet_lines) + "\n\n"
                "Complete these steps first, then retry.\n"
                "Call list_plan_steps() to see current status.\n"
                "</step-gated>"
            )

    step["status"] = "in_progress"
    step["started_at"] = _now_iso()
    # Track current active steps for finding linkage (parallel support)
    current_steps: list[int] = reg.setdefault("current_steps", [])
    if step_number not in current_steps:
        current_steps.append(step_number)
    _persist_plan(thread_id, reg)
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
    thread_id = ctx["thread_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _task_phase_gate(reg)
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
        warning = (
            f"Note: enter_step({step_number}) was not called before marking done. "
            "Call enter_step(N) before starting each step to keep step tracking accurate.\n"
        )

    step["status"] = "done"
    step["completed_at"] = _now_iso()
    if notes:
        step["notes"] = notes
    # Remove from active step tracking
    current_steps: list[int] = reg.get("current_steps", [])
    if step_number in current_steps:
        current_steps.remove(step_number)
    _persist_plan(thread_id, reg)

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
    thread_id = ctx["thread_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _task_phase_gate(reg)
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
    # Remove from active step tracking
    current_steps: list[int] = reg.get("current_steps", [])
    if step_number in current_steps:
        current_steps.remove(step_number)
    _persist_plan(thread_id, reg)

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
    """Exit plan mode. Always callable — serves as escape hatch from Plan Phase.

    Behavior depends on current phase:
    - Plan Phase (not approved): Clean exit, all tools restored (escape hatch).
    - Task Phase (approved): Gate-checks all steps before allowing exit.
    - Normal mode: No-op.

    Always call this when you believe execution is complete or want to exit planning.
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    thread_id = ctx["thread_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner

    reg = _runner._RUN_REGISTRY.get(run_id) or {}

    # Case 1: Plan Phase (not approved) — escape hatch, clean exit
    if reg.get("plan_mode") and not reg.get("plan_approved"):
        _runner._RUN_REGISTRY[run_id].update({
            "status": "running",
            "plan_mode": False,
            "plan_approved": False,
            "plan_steps": [],
            "plan_data": {},
            "plan": "",
            "plan_file": "",
        })
        _persist_plan(thread_id, _runner._RUN_REGISTRY[run_id])
        await bus.publish("plan_exited", {"run_id": run_id, "reason": "escape"})
        return (
            "<system-info>\n"
            "Plan mode exited. All tools restored — operating in normal mode.\n"
            "You may continue working freely or call enter_plan_mode() to restart planning.\n"
            "</system-info>"
        )

    # Case 3: Normal mode — no-op
    if not reg.get("plan_approved"):
        return "<system-info>\nNot in plan mode. No action needed.\n</system-info>"

    # Case 2: Task Phase (approved) — enforce completion gate
    steps = reg.get("plan_steps", [])
    incomplete = [s for s in steps if s["status"] not in ("done", "blocked")]

    if incomplete:
        lines = [
            "<task-harness-enforce>",
            f"EXIT DENIED: {len(incomplete)} of {len(steps)} steps still unresolved.",
            "ENFORCEMENT: You CANNOT exit until all steps are completed or blocked.",
            "PENDING STEPS:",
        ]
        for s in incomplete:
            label = s.get("title") or s.get("description", "—")
            lines.append(f"  {s['number']}. [{s['status']}] {label}")
        lines += [
            "",
            "ACTION REQUIRED: For each pending step:",
            "  enter_step(N) → execute the work → mark_step_done(N, notes=...)",
            "  OR mark_step_blocked(N, reason=...) if it cannot proceed.",
            "Then call exit_plan_mode() again.",
            "</task-harness-enforce>",
        ]
        return "\n".join(lines)

    plan_data = reg.get("plan_data", {})
    plan_file = reg.get("plan_file", "")

    # Fully clear plan state so a new plan can start on the same run/thread
    _runner._RUN_REGISTRY[run_id].update({
        "status":        "completed",
        "plan_mode":     False,
        "plan_approved": False,
        "plan_steps":    [],
        "plan_data":     {},
        "plan":          "",
        "plan_file":     "",
    })
    total_steps = len(steps)

    # Delete the raw .md plan file (not needed after completion)
    if plan_file:
        try:
            _Path(plan_file).unlink(missing_ok=True)
        except OSError:
            pass

    # Clear plan state file — allows next plan on same thread to start fresh
    try:
        _plan_state_path(thread_id).write_text("{}")
    except Exception:
        pass

    await bus.publish("plan_completed", {
        "run_id":      run_id,
        "plan_file":   plan_file,
        "total_steps": total_steps,
    })

    done_steps    = [s for s in steps if s["status"] == "done"]
    blocked_steps = [s for s in steps if s["status"] == "blocked"]

    # Compact result — step digest removed (agent reads mark_step_done ToolMessages directly).
    target_name = plan_data.get("target") or reg.get("metadata", {}).get("target", "")
    target_line = (
        f"\n  memory_write(scope='target', file='methodology') — NEW techniques specific to this target\n"
        f"  memory_write(scope='target', file='notes')       — target-{target_name}: key facts, quirks, credentials\n"
        if target_name else
        "\n  memory_write(scope='target', file='methodology') — if a target is active, write target-specific techniques\n"
    )
    return (
        f"All plan steps complete "
        f"({len(done_steps)} done, {len(blocked_steps)} blocked) — "
        f"'{plan_data.get('title', 'Plan')}'\n\n"
        "── SELF-LEARNING MEMORY PHASE ──────────────────────────────────────────\n"
        "Read your mark_step_done notes above. Write ONLY entries that are NEW —\n"
        "skip anything already in memory. Use these tools:\n\n"
        "  memory_write(scope='agent', file='methodology')  — NEW techniques you used that\n"
        "    aren't in methodology.md yet (tool combos, attack patterns, ordering that worked)\n"
        f"{target_line}"
        "  memory_write(scope='agent', file='feedback')     — wrong assumptions, lessons learned\n"
        "  memory_write(scope='agent', file='user')         — anything new about user preferences\n\n"
        "Before writing: call memory_read first to check what already exists — avoid duplicates.\n"
        "────────────────────────────────────────────────────────────────────────\n\n"
        "After writing memories, provide your final summary: what was accomplished,\n"
        "key findings, anything blocked and why, and recommended next steps."
    )


@tool
async def escalate_step(step_number: int, priority: str = "critical", reason: str = "") -> str:
    """Escalate a pending step's priority. Critical steps should be executed next.

    Call this when a finding in the current step reveals that a later step is
    more urgent than originally planned (e.g. critical SQLi found → escalate
    the "Test Injection" step).

    Args:
        step_number: 1-based step number to escalate.
        priority: New priority — "critical", "high", "normal", or "low".
        reason: Why the step is being escalated (e.g. "Critical SQLi found in auth endpoint").
    """
    if priority not in _VALID_PRIORITIES:
        return f"Invalid priority '{priority}'. Must be one of: {_VALID_PRIORITIES}"

    ctx = _get_run_context()
    run_id = ctx["run_id"]
    thread_id = ctx["thread_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _task_phase_gate(reg)
    if redirect:
        return redirect

    steps = reg.get("plan_steps", [])
    step = _get_step(steps, step_number)

    if step is None:
        valid = [s["number"] for s in steps]
        return f"Step {step_number} not found. Valid step numbers: {valid}."
    if step["status"] in ("done", "blocked"):
        return f"Step {step_number} is already {step['status']} — cannot escalate."

    old_priority = step.get("priority", "normal")
    step["priority"] = priority
    _persist_plan(thread_id, reg)

    label = step.get("title") or step.get("description", "—")
    await bus.publish("step_escalated", {
        "run_id": run_id,
        "step_number": step_number,
        "priority": priority,
        "old_priority": old_priority,
        "reason": reason,
    })
    return (
        f"Step {step_number} ({label}) escalated: {old_priority} → {priority}.\n"
        + (f"Reason: {reason}\n" if reason else "")
        + "Execute it NEXT after completing the current step."
    )


@tool
async def add_steps(
    steps: list[dict],
    after_step: int | None = None,
    reason: str = "",
) -> str:
    """Add new steps to the plan mid-execution without exiting plan mode.

    Use this when scope changes are discovered during execution (e.g. found a
    new subdomain, discovered an additional attack surface). Does NOT require
    re-approval — the steps are added and can be executed immediately.

    Args:
        steps: List of step dicts. Each must have at least a 'title' field.
               Same schema as write_plan steps: {title, description, how_to, depends_on, priority}.
        after_step: Insert these steps with depends_on=[after_step]. None = append with no deps.
        reason: Why steps are being added (e.g. "Discovered new subdomain admin.target.com").
    """
    ctx = _get_run_context()
    run_id = ctx["run_id"]
    thread_id = ctx["thread_id"]
    bus = ctx["parent_bus"]
    import rai.harness.runner as _runner
    reg = _runner._RUN_REGISTRY.get(run_id) or {}
    redirect = _task_phase_gate(reg)
    if redirect:
        return redirect

    if not steps:
        return "No steps provided. Pass at least one step dict with a 'title' field."
    if len(steps) > 10:
        return "Maximum 10 new steps at a time (guard against runaway). Split into multiple calls."

    existing_steps = reg.get("plan_steps", [])
    max_num = max((s["number"] for s in existing_steps), default=0)

    # Validate after_step reference
    if after_step is not None and not _get_step(existing_steps, after_step):
        valid = [s["number"] for s in existing_steps]
        return f"after_step={after_step} not found. Valid step numbers: {valid}."

    # Validate new steps using same schema
    validated: list[dict] = []
    errors: list[str] = []
    for i, raw in enumerate(steps, 1):
        try:
            step = PlanStep(**raw)
        except Exception as e:
            errors.append(f"New step {i}: {e}")
            continue
        new_num = max_num + i
        deps = step.depends_on if step.depends_on else ([after_step] if after_step else [])
        validated.append({
            "number": new_num,
            "title": step.title,
            "description": step.description,
            "how_to": step.how_to,
            "depends_on": deps,
            "priority": step.priority,
            "status": "pending",
            "notes": "",
            "reason": "",
        })

    if errors:
        return "Step validation failed:\n" + "\n".join(errors)

    existing_steps.extend(validated)
    plan_data = reg.get("plan_data", {})
    plan_data["total_steps"] = len(existing_steps)
    _persist_plan(thread_id, reg)

    await bus.publish("steps_added", {
        "run_id": run_id,
        "steps": validated,
        "reason": reason,
    })

    added_list = "\n".join(
        f"  {s['number']}. {s['title']}" + (f" [depends_on={s['depends_on']}]" if s['depends_on'] else "")
        for s in validated
    )
    return (
        f"{len(validated)} step(s) added to plan.\n"
        + (f"Reason: {reason}\n" if reason else "")
        + f"\nNew steps:\n{added_list}\n\n"
        f"Total steps now: {len(existing_steps)}. "
        "Continue execution — these steps are immediately available."
    )


def get_current_step() -> int:
    """Return the most recently entered step number, or 0 if none active.

    Used by findings_add to auto-link findings to the current plan step.
    """
    try:
        ctx = _get_run_context()
        run_id = ctx["run_id"]
        import rai.harness.runner as _runner
        reg = _runner._RUN_REGISTRY.get(run_id, {})
        current_steps = reg.get("current_steps", [])
        return current_steps[-1] if current_steps else 0
    except Exception:
        return 0


def get_plan_tools() -> list:
    """Return all plan-mode tools to inject into the HTTP parent agent."""
    return [
        enter_plan_mode,
        write_plan,
        read_plan,
        list_plan_steps,
        enter_step,
        mark_step_done,
        mark_step_blocked,
        exit_plan_mode,
        escalate_step,
        add_steps,
    ]
