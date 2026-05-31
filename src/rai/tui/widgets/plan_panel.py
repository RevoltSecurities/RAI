"""PlanPanel — approve / reject / edit / respond to agent plan from plan_ready event."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Markdown, Static, TextArea

if TYPE_CHECKING:
    pass


class PlanPanel(Widget):
    can_focus = True

    DEFAULT_CSS = """
    PlanPanel {
        display: none;
        height: auto;
        max-height: 40%;
        background: $surface 90%;
        border: round $warning 70%;
        padding: 1 2;
    }
    #plan-title {
        color: $warning;
        text-style: bold;
        margin-bottom: 1;
    }
    #plan-content {
        height: auto;
        max-height: 20;
        overflow-y: auto;
    }
    #plan-actions {
        height: 3;
        layout: horizontal;
        margin-top: 1;
    }
    #plan-feedback {
        display: none;
        height: 8;
        border: round $warning 50%;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("a", "approve", "Approve", show=True),
        Binding("r", "reject", "Reject", show=True),
        Binding("e", "edit", "Edit", show=True),
        Binding("p", "respond", "Respond", show=True),
        Binding("escape", "dismiss", "Dismiss", show=True),
        Binding("ctrl+s", "submit_feedback", "Submit", show=False),
    ]

    class Decision(Message):
        def __init__(self, run_id: str, decision: str, feedback: str = "") -> None:
            super().__init__()
            self.run_id = run_id
            self.decision = decision   # "approve" | "reject" | "edit" | "respond"
            self.feedback = feedback

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._run_id = ""
        self._agent = ""
        self._plan_text = ""
        self._mode = "idle"  # idle | reject | edit | respond

    def compose(self) -> ComposeResult:
        yield Static(
            "◈ [bold]PLAN READY[/bold]  "
            "[dim]\\[a]pprove  \\[r]eject  \\[e]dit  \\[p]respond  \\[esc] dismiss[/dim]",
            id="plan-title",
        )
        yield Markdown("", id="plan-content")
        yield TextArea("", id="plan-feedback", language="markdown")

    def show(self, plan_text: str, run_id: str, agent: str = "") -> None:
        self._run_id = run_id
        self._agent = agent
        self._plan_text = plan_text
        self._mode = "idle"
        try:
            self.query_one("#plan-content", Markdown).update(plan_text)
            self.query_one("#plan-feedback", TextArea).styles.display = "none"
        except Exception:
            pass
        self.styles.display = "block"
        self.focus()

    def hide(self) -> None:
        self._mode = "idle"
        try:
            self.query_one("#plan-content", Markdown).styles.display = "block"
            ta = self.query_one("#plan-feedback", TextArea)
            ta.styles.display = "none"
            ta.styles.height = "8"
            self.styles.max_height = "40%"
        except Exception:
            pass
        self.styles.display = "none"

    def action_approve(self) -> None:
        self.post_message(self.Decision(self._run_id, "approve"))
        self.hide()

    def action_reject(self) -> None:
        self._mode = "reject"
        try:
            ta = self.query_one("#plan-feedback", TextArea)
            ta.clear()
            ta.styles.display = "block"
            ta.focus()
        except Exception:
            pass
        try:
            self.query_one("#plan-title", Static).update(
                "◈ [bold orange1]PLAN REJECT[/bold orange1]  "
                "[dim]Enter feedback then \\[ctrl+s] to submit[/dim]"
            )
        except Exception:
            pass

    def action_edit(self) -> None:
        self._mode = "edit"
        try:
            self.query_one("#plan-content", Markdown).styles.display = "none"
            ta = self.query_one("#plan-feedback", TextArea)
            ta.load_text(self._plan_text)
            ta.styles.display = "block"
            ta.styles.height = "20"
            self.styles.max_height = "75%"
            ta.focus()
        except Exception:
            pass
        try:
            self.query_one("#plan-title", Static).update(
                "◈ [bold yellow]PLAN EDIT[/bold yellow]  "
                "[dim]Edit then \\[ctrl+s] to auto-approve and proceed[/dim]"
            )
        except Exception:
            pass

    def action_respond(self) -> None:
        self._mode = "respond"
        try:
            ta = self.query_one("#plan-feedback", TextArea)
            ta.clear()
            ta.styles.display = "block"
            ta.focus()
        except Exception:
            pass
        try:
            self.query_one("#plan-title", Static).update(
                "◈ [bold cyan]PLAN RESPONSE[/bold cyan]  "
                "[dim]Type guidance then \\[ctrl+s] to submit[/dim]"
            )
        except Exception:
            pass

    def action_submit_feedback(self) -> None:
        if self._mode not in ("reject", "edit", "respond"):
            return
        try:
            feedback = self.query_one("#plan-feedback", TextArea).text
        except Exception:
            feedback = ""
        self.post_message(self.Decision(self._run_id, self._mode, feedback))
        self.hide()

    def action_dismiss(self) -> None:
        self.hide()
