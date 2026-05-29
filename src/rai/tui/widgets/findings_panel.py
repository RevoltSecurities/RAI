"""FindingsPanel — overlay panel showing security findings with severity badges."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.widget import Widget
from textual.widgets import Markdown, Static


_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high":     1,
    "medium":   2,
    "low":      3,
    "info":     4,
}

_SEVERITY_BADGE: dict[str, str] = {
    "critical": "[bold red]CRITICAL[/bold red]",
    "high":     "[bold orange1]HIGH[/bold orange1]",
    "medium":   "[bold yellow]MEDIUM[/bold yellow]",
    "low":      "[cyan]LOW[/cyan]",
    "info":     "[dim]INFO[/dim]",
}


def _render_findings_md(findings: list[dict]) -> str:
    """Render findings list as markdown for the panel Markdown widget."""
    if not findings:
        return (
            "# Security Findings\n\n"
            "_No findings recorded yet._\n\n"
            "Use **findings_add** tool in a run to record vulnerabilities."
        )

    sorted_f = sorted(
        findings,
        key=lambda f: _SEVERITY_ORDER.get(f.get("severity", "info").lower(), 5),
    )

    # Summary counts
    counts: dict[str, int] = {}
    for f in sorted_f:
        sev = f.get("severity", "info").lower()
        counts[sev] = counts.get(sev, 0) + 1

    lines: list[str] = [f"# Security Findings  —  {len(findings)} total\n"]
    summary_parts: list[str] = []
    for sev in ("critical", "high", "medium", "low", "info"):
        if sev in counts:
            summary_parts.append(f"**{sev.upper()}:** {counts[sev]}")
    lines.append("  ·  ".join(summary_parts))
    lines.append("\n---\n")

    for i, f in enumerate(sorted_f, 1):
        sev = f.get("severity", "unknown").lower()
        title = f.get("title") or f.get("name") or "Untitled"

        lines.append(f"## {i}. {title}\n")
        lines.append(f"**Severity:** {sev.upper()}\n")

        # Key metadata
        for label, key in (
            ("Location",    "location"),
            ("Endpoint",    "endpoint"),
            ("Parameter",   "parameter"),
            ("CVE",         "cve"),
            ("CWE",         "cwe"),
            ("CVSS",        "cvss"),
            ("OWASP",       "owasp"),
            ("Target",      "target"),
        ):
            val = f.get(key, "")
            if val:
                lines.append(f"**{label}:** {val}  ")

        lines.append("")

        desc = f.get("description", "")
        if desc:
            lines.append(f"{desc}\n")

        impact = f.get("impact", "")
        if impact:
            lines.append(f"**Impact:** {impact}\n")

        remediation = f.get("remediation", "")
        if remediation:
            lines.append(f"**Remediation:** {remediation}\n")

        evidence = f.get("evidence", "")
        if evidence:
            lines.append(f"**Evidence:**\n```\n{evidence}\n```\n")

        tags = f.get("tags", [])
        if tags:
            lines.append(f"**Tags:** {', '.join(tags)}\n")

        lines.append("---\n")

    return "\n".join(lines)


class FindingsPanel(Widget):
    """Overlay panel for security findings — toggle with /findings."""

    can_focus = True

    DEFAULT_CSS = """
    FindingsPanel {
        height: 0;
        max-height: 14;
        overflow: hidden hidden;
    }
    FindingsPanel.panel-open {
        height: auto;
        max-height: 70%;
        background: $surface 95%;
        border: round $error 55%;
        padding: 1 2;
    }
    FindingsPanel #findings-header {
        color: $error;
        text-style: bold;
        margin-bottom: 1;
    }
    FindingsPanel #findings-content {
        height: auto;
        max-height: 50;
        overflow-y: auto;
    }
    FindingsPanel #findings-footer {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close_panel", "Close",   show=True),
        Binding("r",      "refresh",     "Refresh", show=True),
        Binding("e",      "export_hint", "Export",  show=True),
    ]

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._visible = False

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold red]●[/bold red] [bold]Security Findings[/bold]"
            "  [dim]·[/dim]  [dim]r[/dim] refresh"
            "  [dim]·[/dim]  [dim]e[/dim] export hint"
            "  [dim]·[/dim]  [dim]esc[/dim] close",
            id="findings-header",
        )
        yield Markdown("", id="findings-content")
        yield Static("", id="findings-footer")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def toggle(self) -> None:
        if self._visible:
            self._close()
        else:
            self._open()

    def show_panel(self) -> None:
        if not self._visible:
            self._open()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open(self) -> None:
        self._visible = True
        self.add_class("panel-open")
        self._refresh_findings()
        self.focus()

    def _close(self) -> None:
        self._visible = False
        self.remove_class("panel-open")

    def _refresh_findings(self) -> None:
        """Load findings from the in-memory store and re-render."""
        try:
            from rai.tools.core.findings import _get_findings
            findings = _get_findings()
            self.query_one("#findings-content", Markdown).update(
                _render_findings_md(findings)
            )
            count = len(findings)
            if count:
                footer = (
                    f"[dim]{count} finding(s)  ·  "
                    "run findings_export(format='markdown') to generate a report[/dim]"
                )
            else:
                footer = "[dim]No findings yet — use findings_add tool in a run[/dim]"
            self.query_one("#findings-footer", Static).update(footer)
        except Exception as exc:
            try:
                self.query_one("#findings-content", Markdown).update(
                    f"# Error\n\nCould not load findings: {exc}"
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_close_panel(self) -> None:
        self._close()

    def action_refresh(self) -> None:
        self._refresh_findings()

    def action_export_hint(self) -> None:
        try:
            self.app.notify(
                "Ask the agent: findings_export(format='markdown', output='report.md')\n"
                "Formats: markdown · json · sarif · csv · hackerone · bugcrowd",
                title="Export Findings",
                timeout=6,
            )
        except Exception:
            pass
