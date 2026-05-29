"""OPPLAN Pydantic schemas — generalized for all security disciplines.

Covers: pentesting, SAST, DAST, threat modeling, API security,
        red team, cloud security, bug bounty, and custom workflows.

Design adapted from Decepticon (https://github.com/PurpleAILAB/Decepticon)
but generalized to remove red-team-only coupling (OpsecLevel, C2Tier,
kill-chain-specific ObjectivePhase).

RAI improvements:
- File-based persistence (survives session restarts)
- Python 3.11+ compatible (Decepticon requires 3.13)
- No LangGraph / Neo4j transitive deps
- `framework_refs` replaces `mitre` — covers ATT&CK, CWE, OWASP, STRIDE
- `risk_level` replaces `OpsecLevel` + `C2Tier` — universal risk concept
- `tool_hints` replaces `opsec_notes` — actionable per-objective tool guidance
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SecurityDiscipline(StrEnum):
    """Security discipline — drives phase vocabulary and template selection."""

    PENTESTING = "pentesting"
    SAST = "sast"
    DAST = "dast"
    THREAT_MODELING = "threat-modeling"
    API_SECURITY = "api-security"
    RED_TEAM = "red-team"
    CLOUD_SECURITY = "cloud-security"
    BUG_BOUNTY = "bug-bounty"
    CUSTOM = "custom"


class ObjectiveStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class Objective(BaseModel):
    """A single engagement objective — completable in ONE agent context window."""

    id: str = Field(description="Auto-generated: OBJ-001, OBJ-002, ...")
    title: str
    phase: str = Field(description="Workflow phase (free-form): discovery, analysis, testing, reporting, etc.")
    description: str
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Verifiable criteria — each must be independently checkable",
    )
    priority: int = Field(description="Execution order (1 = first, lower = earlier)")
    status: ObjectiveStatus = ObjectiveStatus.PENDING
    risk_level: str = Field(default="medium", description="Risk/impact level: low, medium, high, critical")
    framework_refs: list[str] = Field(
        default_factory=list,
        description="Framework references: MITRE ATT&CK IDs, CWE-xx, OWASP Axx:year, STRIDE categories",
    )
    tool_hints: list[str] = Field(
        default_factory=list,
        description="Suggested tools for this objective (e.g. nmap, semgrep, burpsuite)",
    )
    notes: str = ""
    blocked_by: list[str] = Field(default_factory=list, description="OBJ-xxx IDs that must complete first")
    owner: str = Field(default="", description="Sub-agent or team member executing this objective")
    parent_id: str | None = Field(default=None, description="Parent OBJ-xxx for PTT hierarchical sub-tasks")


class OPPLAN(BaseModel):
    """Operations Plan — discipline-aware tactical task tracker.

    One OPPLAN per agent, persisted to ~/.rai/agents/<name>/opplan.json.
    """

    engagement_name: str = ""
    discipline: str = Field(default="pentesting", description="Security discipline driving phase vocabulary")
    target: str = Field(default="", description="Primary target: URL, IP range, repo URL, cloud account, etc.")
    scope: str = Field(default="", description="In-scope / out-of-scope description")
    methodology: str = Field(default="", description="Framework: OWASP WSTG, PTES, STRIDE, MITRE ATT&CK, etc.")
    threat_profile: str = Field(default="", description="Threat actor profile (red team / threat modeling)")
    objectives: list[Objective] = Field(default_factory=list)
    counter: int = 0

    def by_id(self, objective_id: str) -> Objective | None:
        return next((o for o in self.objectives if o.id == objective_id), None)

    def children_of(self, parent_id: str) -> list[Objective]:
        return [o for o in self.objectives if o.parent_id == parent_id]

    def descendants_of(self, parent_id: str) -> list[Objective]:
        out: list[Objective] = []
        stack = list(self.children_of(parent_id))
        while stack:
            obj = stack.pop()
            out.append(obj)
            stack.extend(self.children_of(obj.id))
        return out

    def has_hierarchy(self) -> bool:
        return any(o.parent_id for o in self.objectives)

    def next_id(self) -> str:
        self.counter += 1
        return f"OBJ-{self.counter:03d}"
