"""OPPLAN tools — generalized operational objective tracking for all security disciplines.

Covers pentesting, SAST, DAST, threat modeling, API security, red team, cloud security,
bug bounty, and custom engagements.

File-based persistence to ~/.rai/agents/<name>/opplan.json.
Adapted from Decepticon (https://github.com/PurpleAILAB/Decepticon) but:
- Works on Python 3.11+ (Decepticon requires 3.13)
- Generalized across disciplines (not red-team-only)
- Persists across session restarts (Decepticon loses state on container stop)
- No LangGraph InjectedState/Command wiring — uses deepagents BaseTool pattern
- Hierarchy: expand/collapse sub-tasks (Pentesting Task Tree pattern)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, ClassVar

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from rai.engine.opplan.schemas import (
    OPPLAN,
    Objective,
    ObjectiveStatus,
    SecurityDiscipline,
)

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in-progress", "cancelled"},
    "in-progress": {"completed", "blocked", "cancelled"},
    "blocked": {"in-progress", "completed", "cancelled"},
}

_opplan_lock = threading.RLock()

_TEMPLATES_DIR = Path(__file__).parent.parent / "data" / "opplan_templates"


def _opplan_path(agent_name: str) -> Path:
    from rai.config.settings import settings
    return settings.agent_dir(agent_name) / "opplan.json"


def _load(agent_name: str) -> OPPLAN:
    path = _opplan_path(agent_name)
    if path.exists():
        try:
            return OPPLAN.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return OPPLAN()


def _save(agent_name: str, opplan: OPPLAN) -> None:
    path = _opplan_path(agent_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(opplan.model_dump_json(indent=2), encoding="utf-8")


def _load_template(discipline: str) -> list[dict[str, Any]]:
    """Load discipline template JSON, returning empty list if not found."""
    slug = discipline.lower().replace("_", "-")
    path = _TEMPLATES_DIR / f"{slug}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Status formatting — compact battle-tracker table for system-prompt injection
# ---------------------------------------------------------------------------

_STATUS_MARKERS = {
    "completed": "COMPLETED",
    "blocked": "BLOCKED",
    "cancelled": "CANCELLED",
    "in-progress": ">>IN-PROGRESS<<",
    "pending": "pending",
    "skipped": "skipped",
}

_TERMINAL = {"completed", "cancelled", "skipped"}


def format_opplan_status(opplan: OPPLAN) -> str:
    """Format OPPLAN as a compact table for system-prompt injection."""
    objectives = opplan.objectives
    total = len(objectives)
    if total == 0:
        return ""

    done = sum(1 for o in objectives if o.status == "completed")
    blocked = sum(1 for o in objectives if o.status == "blocked")
    in_prog = sum(1 for o in objectives if o.status == "in-progress")
    pending = sum(1 for o in objectives if o.status == "pending")
    cancelled = sum(1 for o in objectives if o.status in ("cancelled", "skipped"))

    progress = (
        f"Progress: {done}/{total} completed, {blocked} blocked, "
        f"{in_prog} in-progress, {pending} pending"
    )
    if cancelled:
        progress += f", {cancelled} cancelled/skipped"

    lines = [
        "<OPPLAN_STATUS>",
        f"Engagement: {opplan.engagement_name or '(unnamed)'}",
        f"Discipline: {opplan.discipline} | Target: {opplan.target or '(not set)'}",
    ]
    if opplan.methodology:
        lines.append(f"Methodology: {opplan.methodology}")
    lines += [
        progress,
        "",
        "| ID | Phase | Title | Status | Risk | Priority | Owner |",
        "|---|---|---|---|---|---|---|",
    ]

    actionable = [o for o in objectives if o.status not in _TERMINAL]
    terminal = [o for o in objectives if o.status in _TERMINAL]

    for o in sorted(actionable, key=lambda x: x.priority):
        marker = _STATUS_MARKERS.get(o.status, o.status)
        lines.append(
            f"| {o.id} | {o.phase} | {o.title} | {marker} | {o.risk_level} | {o.priority} | {o.owner or '-'} |"
        )
    for o in sorted(terminal, key=lambda x: x.priority)[:20]:
        marker = _STATUS_MARKERS.get(o.status, o.status)
        lines.append(
            f"| {o.id} | {o.phase} | {o.title} | {marker} | {o.risk_level} | {o.priority} | {o.owner or '-'} |"
        )

    hidden_terminal = max(0, len(terminal) - 20)
    if hidden_terminal:
        lines.append(f"| … | … | _{hidden_terminal} more terminal objectives_ | … | … | … | … |")

    actionable_next = [o for o in actionable if o.status in ("pending", "in-progress")]
    if actionable_next:
        nxt = sorted(actionable_next, key=lambda x: x.priority)[0]
        lines.extend([
            "",
            f"**Next**: {nxt.id} — {nxt.title}",
            f"  Phase: {nxt.phase} | Risk: {nxt.risk_level}",
        ])
        if nxt.framework_refs:
            lines.append(f"  Framework refs: {', '.join(nxt.framework_refs)}")
        if nxt.tool_hints:
            lines.append(f"  Suggested tools: {', '.join(nxt.tool_hints)}")
        if nxt.acceptance_criteria:
            lines.append("  Acceptance Criteria:")
            for c in nxt.acceptance_criteria:
                lines.append(f"    - [ ] {c}")
    else:
        all_done = all(o.status == "completed" for o in objectives)
        lines.append("")
        lines.append(
            "**ALL OBJECTIVES COMPLETE**"
            if all_done
            else "**No actionable objectives** — review blocked items."
        )

    lines.append("</OPPLAN_STATUS>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool input schemas
# ---------------------------------------------------------------------------


class OPPLANInitInput(BaseModel):
    discipline: str = Field(
        default="pentesting",
        description=(
            "Security discipline: pentesting, sast, dast, threat-modeling, api-security, "
            "red-team, cloud-security, bug-bounty, custom"
        ),
    )
    engagement_name: str = Field(default="", description="Name for this engagement")
    target: str = Field(
        default="",
        description="Primary target: URL, IP range, repo path, cloud account ID, etc.",
    )
    scope: str = Field(default="", description="In-scope and out-of-scope description")
    methodology: str = Field(
        default="",
        description="Framework or methodology: OWASP WSTG, PTES, STRIDE, MITRE ATT&CK, etc.",
    )
    threat_profile: str = Field(
        default="",
        description="Threat actor profile (used by red team and threat modeling disciplines)",
    )
    load_template: bool = Field(
        default=True,
        description=(
            "Auto-create standard objectives from the discipline template. "
            "Set False to start with a blank OPPLAN."
        ),
    )


class OPPLANAddInput(BaseModel):
    title: str = Field(description="Objective title — one sentence, action-oriented")
    phase: str = Field(
        description=(
            "Workflow phase (free-form). Examples by discipline: "
            "pentesting: discovery/scanning/exploitation/validation/reporting; "
            "sast: triage/analysis/validation/reporting; "
            "red-team: recon/initial-access/persistence/lateral-movement/exfiltration/reporting"
        )
    )
    description: str = Field(description="What to accomplish in this objective")
    acceptance_criteria: list[str] = Field(description="Verifiable criteria — each independently checkable")
    priority: int = Field(description="Execution order (1 = first). Lower = earlier.")
    framework_refs: list[str] = Field(
        default_factory=list,
        description="Framework references: MITRE ATT&CK IDs (T1190), CWE-xx, OWASP Axx:year, STRIDE categories",
    )
    tool_hints: list[str] = Field(
        default_factory=list,
        description="Suggested tools for this objective (e.g. nmap, semgrep, burpsuite)",
    )
    risk_level: str = Field(
        default="medium",
        description="Risk/impact level: low, medium, high, critical",
    )
    blocked_by: list[str] = Field(
        default_factory=list,
        description="OBJ-xxx IDs that must complete before this",
    )
    parent_id: str = Field(
        default="",
        description="Parent objective ID for hierarchical tasks (PTT pattern)",
    )


class OPPLANGetInput(BaseModel):
    objective_id: str = Field(description="Objective ID (e.g. OBJ-001)")


class OPPLANUpdateInput(BaseModel):
    objective_id: str = Field(description="Objective ID to update")
    status: str = Field(
        default="",
        description="New status: pending, in-progress, completed, blocked, cancelled",
    )
    notes: str = Field(
        default="",
        description="Notes — include evidence when completing, failure reason when blocking",
    )
    owner: str = Field(default="", description="Sub-agent or team member executing this objective")
    add_blocked_by: list[str] = Field(
        default_factory=list,
        description="Additional OBJ-xxx dependencies to add",
    )


class OPPLANExpandInput(BaseModel):
    parent_id: str = Field(description="Parent objective ID to expand into sub-tasks")
    children: list[dict] = Field(
        description=(
            "List of child dicts, each with: title, description, acceptance_criteria (list), "
            "and optionally phase, priority, risk_level, framework_refs, tool_hints, blocked_by"
        )
    )


class OPPLANCollapseInput(BaseModel):
    parent_id: str = Field(description="Parent objective ID whose descendants to cancel")


class OPPLANSaveInput(BaseModel):
    workspace_path: str = Field(
        default="",
        description="Engagement workspace directory. Empty = agent dir.",
    )


class OPPLANLoadInput(BaseModel):
    workspace_path: str = Field(description="Directory containing plan/opplan.json to load")


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------


class OPPLANInitTool(BaseTool):
    """Initialize an OPPLAN for a security engagement, optionally loading discipline templates."""

    name: str = "opplan_init"
    description: str = (
        "Initialize the OPPLAN for a security engagement. "
        "Set discipline, target, scope, and methodology. "
        "With load_template=True (default), auto-creates standard objectives for the chosen discipline "
        "(pentesting, sast, dast, threat-modeling, api-security, red-team, cloud-security, bug-bounty, custom). "
        "This is the recommended first tool call for any new engagement."
    )
    args_schema: ClassVar[type[BaseModel]] = OPPLANInitInput
    agent_name: str = ""

    def _run(
        self,
        discipline: str = "pentesting",
        engagement_name: str = "",
        target: str = "",
        scope: str = "",
        methodology: str = "",
        threat_profile: str = "",
        load_template: bool = True,
    ) -> str:
        valid_disciplines = [d.value for d in SecurityDiscipline]
        if discipline not in valid_disciplines:
            return f"Error: invalid discipline '{discipline}'. Valid: {', '.join(valid_disciplines)}"

        with _opplan_lock:
            opplan = OPPLAN(
                engagement_name=engagement_name,
                discipline=discipline,
                target=target,
                scope=scope,
                methodology=methodology,
                threat_profile=threat_profile,
            )

            if load_template:
                templates = _load_template(discipline)
                for tmpl in sorted(templates, key=lambda t: t.get("priority", 99)):
                    obj_id = opplan.next_id()
                    obj = Objective(
                        id=obj_id,
                        title=tmpl.get("title", ""),
                        phase=tmpl.get("phase", ""),
                        description=tmpl.get("description", ""),
                        acceptance_criteria=tmpl.get("acceptance_criteria") or [],
                        priority=tmpl.get("priority", opplan.counter),
                        risk_level=tmpl.get("risk_level", "medium"),
                        framework_refs=tmpl.get("framework_refs") or [],
                        tool_hints=tmpl.get("tool_hints") or [],
                    )
                    opplan.objectives.append(obj)

            _save(self.agent_name, opplan)

        loaded = len(opplan.objectives)
        msg = f"OPPLAN initialized: discipline={discipline}, target={target or '(not set)'}"
        if loaded:
            msg += f"\nLoaded {loaded} template objectives:"
            for o in opplan.objectives:
                msg += f"\n  {o.id} [{o.phase}] {o.title}"
        else:
            msg += "\nNo template objectives loaded (blank OPPLAN). Use opplan_add to create objectives."
        return msg


class OPPLANAddObjectiveTool(BaseTool):
    """Add one objective to the OPPLAN. Auto-generates ID (OBJ-001, OBJ-002, ...)."""

    name: str = "opplan_add"
    description: str = (
        "Add a single objective to the OPPLAN. Auto-generates ID. "
        "Each objective MUST be completable in ONE agent context window. "
        "Use blocked_by for sequential dependencies. "
        "Call opplan_init first if OPPLAN is not yet initialized."
    )
    args_schema: ClassVar[type[BaseModel]] = OPPLANAddInput
    agent_name: str = ""

    def _run(
        self,
        title: str,
        phase: str,
        description: str,
        acceptance_criteria: list[str],
        priority: int,
        framework_refs: list[str] | None = None,
        tool_hints: list[str] | None = None,
        risk_level: str = "medium",
        blocked_by: list[str] | None = None,
        parent_id: str = "",
    ) -> str:
        with _opplan_lock:
            opplan = _load(self.agent_name)

            if parent_id:
                if not opplan.by_id(parent_id):
                    existing = [o.id for o in opplan.objectives]
                    return f"Error: parent '{parent_id}' not found. Existing: {', '.join(existing) or 'none'}"

            obj_id = opplan.next_id()
            obj = Objective(
                id=obj_id,
                title=title,
                phase=phase,
                description=description,
                acceptance_criteria=acceptance_criteria,
                priority=priority,
                risk_level=risk_level,
                framework_refs=framework_refs or [],
                tool_hints=tool_hints or [],
                blocked_by=blocked_by or [],
                parent_id=parent_id or None,
            )
            opplan.objectives.append(obj)
            _save(self.agent_name, opplan)

        return f"Added {obj_id}: {title} (phase: {phase}, risk: {risk_level}, priority: {priority})"


class OPPLANGetObjectiveTool(BaseTool):
    """Read a single objective's full details. ALWAYS call before update."""

    name: str = "opplan_get"
    description: str = (
        "Read a single OPPLAN objective by ID. "
        "ALWAYS call this before opplan_update to prevent staleness. "
        "Returns status, description, acceptance criteria, dependencies, notes."
    )
    args_schema: ClassVar[type[BaseModel]] = OPPLANGetInput
    agent_name: str = ""

    def _run(self, objective_id: str) -> str:
        opplan = _load(self.agent_name)
        obj = opplan.by_id(objective_id)
        if not obj:
            available = ", ".join(o.id for o in opplan.objectives)
            return f"Error: '{objective_id}' not found. Available: {available or 'none (use opplan_add first)'}"

        lines = [
            f"## {obj.id} [{obj.status.upper()}]",
            f"Title: {obj.title}",
            f"Phase: {obj.phase} | Priority: {obj.priority} | Risk: {obj.risk_level}",
            f"Description: {obj.description}",
        ]
        if obj.framework_refs:
            lines.append(f"Framework Refs: {', '.join(obj.framework_refs)}")
        if obj.tool_hints:
            lines.append(f"Tool Hints: {', '.join(obj.tool_hints)}")
        if obj.acceptance_criteria:
            check = "x" if obj.status == "completed" else " "
            lines.append("Acceptance Criteria:")
            for c in obj.acceptance_criteria:
                lines.append(f"  - [{check}] {c}")
        if obj.blocked_by:
            lines.append(f"Blocked By: {', '.join(obj.blocked_by)}")
        if obj.owner:
            lines.append(f"Owner: {obj.owner}")
        if obj.parent_id:
            lines.append(f"Parent: {obj.parent_id}")
        children = opplan.children_of(obj.id)
        if children:
            lines.append(f"Children: {', '.join(c.id for c in children)}")
        if obj.notes:
            lines.append(f"Notes: {obj.notes}")
        return "\n".join(lines)


class OPPLANListObjectivesTool(BaseTool):
    """List all OPPLAN objectives with progress summary."""

    name: str = "opplan_list"
    description: str = (
        "List all OPPLAN objectives with engagement progress. "
        "Use when selecting the next objective, reviewing progress, or situational awareness."
    )
    args_schema: ClassVar[type[BaseModel]] = BaseModel
    agent_name: str = ""

    def _run(self, **_kwargs: Any) -> str:
        opplan = _load(self.agent_name)
        if not opplan.objectives:
            return "No objectives defined yet. Use opplan_init (with load_template=True) or opplan_add."

        total = len(opplan.objectives)
        completed = sum(1 for o in opplan.objectives if o.status == "completed")
        blocked = sum(1 for o in opplan.objectives if o.status == "blocked")

        lines = [
            f"# OPPLAN: {opplan.engagement_name or '(unnamed)'}",
            f"Discipline: {opplan.discipline} | Target: {opplan.target or '(not set)'}",
        ]
        if opplan.methodology:
            lines.append(f"Methodology: {opplan.methodology}")
        lines += [
            f"Progress: {completed}/{total} completed, {blocked} blocked",
            "",
            "| ID | Phase | Title | Status | Risk | Priority | Owner | Blocked By |",
            "|---|---|---|---|---|---|---|---|",
        ]

        for o in sorted(opplan.objectives, key=lambda x: x.priority):
            blocked_by = ", ".join(o.blocked_by) or "-"
            title = ("↳ " if o.parent_id else "") + o.title
            lines.append(
                f"| {o.id} | {o.phase} | {title} | {o.status} | "
                f"{o.risk_level} | {o.priority} | {o.owner or '-'} | {blocked_by} |"
            )

        if opplan.has_hierarchy():
            lines.extend(["", "## Task Tree"])
            rendered: set[str] = set()

            def _render(parent_id: str | None, depth: int) -> None:
                kids = sorted(
                    [o for o in opplan.objectives if o.parent_id == parent_id],
                    key=lambda x: x.priority,
                )
                for o in kids:
                    if o.id in rendered:
                        continue
                    rendered.add(o.id)
                    marker = {
                        "completed": "[x]", "blocked": "[!]",
                        "cancelled": "[-]", "in-progress": "[~]",
                    }.get(o.status, "[ ]")
                    lines.append(f"{'  ' * depth}- {marker} {o.id} {o.title} ({o.status})")
                    _render(o.id, depth + 1)

            _render(None, 0)

        actionable = [o for o in opplan.objectives if o.status in ("pending", "in-progress")]
        if actionable:
            nxt = sorted(actionable, key=lambda x: x.priority)[0]
            lines.append(f"\nNext: {nxt.id} — {nxt.title} (phase: {nxt.phase}, priority: {nxt.priority})")
        else:
            all_done = all(o.status == "completed" for o in opplan.objectives)
            lines.append(
                "\nALL OBJECTIVES COMPLETE — Generate final engagement report."
                if all_done
                else "\nNo actionable objectives — review blocked items for retry."
            )

        return "\n".join(lines)


class OPPLANUpdateObjectiveTool(BaseTool):
    """Update objective status/notes/owner. ALWAYS call opplan_get first."""

    name: str = "opplan_update"
    description: str = (
        "Update a single OPPLAN objective. ALWAYS call opplan_get first. "
        "Valid transitions: pending→in-progress, in-progress→completed/blocked, "
        "blocked→in-progress (retry) or completed (abandon). "
        "Include evidence when marking completed. Include failure reason when blocking."
    )
    args_schema: ClassVar[type[BaseModel]] = OPPLANUpdateInput
    agent_name: str = ""

    def _run(
        self,
        objective_id: str,
        status: str = "",
        notes: str = "",
        owner: str = "",
        add_blocked_by: list[str] | None = None,
    ) -> str:
        with _opplan_lock:
            opplan = _load(self.agent_name)
            obj = opplan.by_id(objective_id)
            if not obj:
                available = ", ".join(o.id for o in opplan.objectives)
                return f"Error: '{objective_id}' not found. Available: {available}"

            updated: list[str] = []

            if status:
                try:
                    new_status = ObjectiveStatus(status)
                except ValueError:
                    valid = [s.value for s in ObjectiveStatus]
                    return f"Error: invalid status '{status}'. Valid: {', '.join(valid)}"

                current = obj.status.value
                if status not in _VALID_TRANSITIONS.get(current, set()):
                    valid_next = ", ".join(sorted(_VALID_TRANSITIONS.get(current, set())))
                    return f"Error: invalid transition {current} → {status}. Valid from '{current}': {valid_next}"

                if status == "in-progress":
                    unresolved = [
                        bid for bid in obj.blocked_by
                        if (dep := opplan.by_id(bid)) and dep.status != "completed"
                    ]
                    if unresolved:
                        return f"Error: cannot start {objective_id}: blocked by unresolved {', '.join(unresolved)}"

                if status == "completed":
                    children = opplan.children_of(objective_id)
                    open_kids = [c.id for c in children if c.status not in ("completed", "cancelled", "skipped")]
                    if open_kids:
                        return (
                            f"Error: cannot complete {objective_id}: children still open: {', '.join(open_kids)}. "
                            f"Complete or cancel each child first, or call opplan_collapse({objective_id})."
                        )

                obj.status = new_status
                updated.append(f"status → {status}")

            if notes:
                obj.notes = notes
                updated.append("notes")
            if owner:
                obj.owner = owner
                updated.append("owner")
            if add_blocked_by:
                all_ids = {o.id for o in opplan.objectives}
                invalid = [bid for bid in add_blocked_by if bid not in all_ids]
                if invalid:
                    return f"Error: invalid blocked_by references: {', '.join(invalid)}"
                existing = set(obj.blocked_by)
                obj.blocked_by = sorted(existing | set(add_blocked_by))
                updated.append("blocked_by")

            if not updated:
                return f"No changes specified for {objective_id}."

            _save(self.agent_name, opplan)

        total = len(opplan.objectives)
        done = sum(1 for o in opplan.objectives if o.status == "completed")
        return f"Updated {objective_id}: {', '.join(updated)}. Progress: {done}/{total} completed."


class OPPLANExpandObjectiveTool(BaseTool):
    """Break a parent objective into child sub-tasks (Pentesting Task Tree pattern)."""

    name: str = "opplan_expand"
    description: str = (
        "Expand a parent objective into child sub-tasks (Pentesting Task Tree / PTT pattern). "
        "Children auto-get IDs. Parent cannot move to COMPLETED until all children are COMPLETED/CANCELLED. "
        "Use when an objective is broad or recon reveals sub-tasks."
    )
    args_schema: ClassVar[type[BaseModel]] = OPPLANExpandInput
    agent_name: str = ""

    def _run(self, parent_id: str, children: list[dict]) -> str:
        with _opplan_lock:
            opplan = _load(self.agent_name)
            parent = opplan.by_id(parent_id)
            if not parent:
                return f"Error: parent '{parent_id}' not found."
            if parent.status in ("completed", "cancelled", "skipped"):
                return f"Error: cannot expand {parent_id}: status is {parent.status}."
            if not children:
                return "Error: children list is empty."

            created: list[str] = []
            for idx, child in enumerate(children, 1):
                title = str(child.get("title", "")).strip()
                description = str(child.get("description", "")).strip()
                acceptance = child.get("acceptance_criteria") or []
                if not title or not description or not acceptance:
                    return f"Error: child #{idx} missing required fields (title, description, acceptance_criteria)."

                phase_str = child.get("phase", parent.phase)
                try:
                    priority = int(child.get("priority", parent.priority + idx))
                except (ValueError, TypeError):
                    priority = parent.priority + idx

                obj_id = opplan.next_id()
                opplan.objectives.append(Objective(
                    id=obj_id,
                    title=title,
                    phase=phase_str,
                    description=description,
                    acceptance_criteria=list(acceptance),
                    priority=priority,
                    risk_level=child.get("risk_level", parent.risk_level),
                    framework_refs=list(child.get("framework_refs") or []),
                    tool_hints=list(child.get("tool_hints") or []),
                    blocked_by=list(child.get("blocked_by") or []),
                    parent_id=parent_id,
                ))
                created.append(obj_id)

            _save(self.agent_name, opplan)

        return f"Expanded {parent_id} into {len(created)} children: {', '.join(created)}"


class OPPLANCollapseObjectiveTool(BaseTool):
    """Cancel every descendant of a parent objective."""

    name: str = "opplan_collapse"
    description: str = (
        "Cancel every descendant of a parent objective. "
        "Use when abandoning a hierarchical task — sets each open child to 'cancelled' "
        "so the parent can then be moved to COMPLETED or CANCELLED itself."
    )
    args_schema: ClassVar[type[BaseModel]] = OPPLANCollapseInput
    agent_name: str = ""

    def _run(self, parent_id: str) -> str:
        with _opplan_lock:
            opplan = _load(self.agent_name)
            if not opplan.by_id(parent_id):
                return f"Error: parent '{parent_id}' not found."

            cancelled: list[str] = []
            for desc in opplan.descendants_of(parent_id):
                if desc.status in ("pending", "in-progress", "blocked"):
                    desc.status = ObjectiveStatus.CANCELLED
                    cancelled.append(desc.id)

            _save(self.agent_name, opplan)

        return (
            f"Cancelled {len(cancelled)} descendants of {parent_id}"
            + (f": {', '.join(cancelled)}" if cancelled else "")
        )


class OPPLANSaveTool(BaseTool):
    """Persist the OPPLAN to an engagement workspace directory."""

    name: str = "opplan_save"
    description: str = (
        "Save the current OPPLAN to <workspace>/plan/opplan.json for the engagement record. "
        "Call after user approves the plan and after major re-planning. "
        "The plan is always auto-saved to ~/.rai/agents/<name>/opplan.json — "
        "this tool additionally saves to a named workspace for hand-off."
    )
    args_schema: ClassVar[type[BaseModel]] = OPPLANSaveInput
    agent_name: str = ""

    def _run(self, workspace_path: str = "") -> str:
        opplan = _load(self.agent_name)
        if not opplan.objectives:
            return "No objectives defined yet."

        if workspace_path:
            out_dir = Path(workspace_path) / "plan"
        else:
            from rai.config.settings import settings
            out_dir = settings.agent_dir(self.agent_name)

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "opplan.json"
        out_path.write_text(opplan.model_dump_json(indent=2), encoding="utf-8")
        return (
            f"OPPLAN saved to {out_path} "
            f"({len(opplan.objectives)} objectives, engagement: {opplan.engagement_name or 'unnamed'})"
        )


class OPPLANLoadTool(BaseTool):
    """Load OPPLAN from an engagement workspace to resume."""

    name: str = "opplan_load"
    description: str = (
        "Load plan/opplan.json from a workspace into the active OPPLAN. "
        "Call on session startup when resuming a previous engagement."
    )
    args_schema: ClassVar[type[BaseModel]] = OPPLANLoadInput
    agent_name: str = ""

    def _run(self, workspace_path: str) -> str:
        path = Path(workspace_path) / "plan" / "opplan.json"
        if not path.exists():
            path = Path(workspace_path) / "opplan.json"
        if not path.exists():
            return f"No opplan.json found at {path.parent}."

        try:
            opplan = OPPLAN.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as e:
            return f"Failed to load opplan.json: {e}"

        with _opplan_lock:
            _save(self.agent_name, opplan)

        return (
            f"Loaded {len(opplan.objectives)} objectives from {path}. "
            f"Engagement: {opplan.engagement_name or 'unnamed'} | "
            f"Discipline: {opplan.discipline} | Counter at OBJ-{opplan.counter:03d}"
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_opplan_tools(agent_name: str) -> list[BaseTool]:
    """Return all OPPLAN tools bound to the given agent's storage."""
    return [
        OPPLANInitTool(agent_name=agent_name),
        OPPLANAddObjectiveTool(agent_name=agent_name),
        OPPLANGetObjectiveTool(agent_name=agent_name),
        OPPLANListObjectivesTool(agent_name=agent_name),
        OPPLANUpdateObjectiveTool(agent_name=agent_name),
        OPPLANExpandObjectiveTool(agent_name=agent_name),
        OPPLANCollapseObjectiveTool(agent_name=agent_name),
        OPPLANSaveTool(agent_name=agent_name),
        OPPLANLoadTool(agent_name=agent_name),
    ]
