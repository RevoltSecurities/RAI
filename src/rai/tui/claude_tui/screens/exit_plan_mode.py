"""ExitPlanModeScreen — plan approval modal for ExitPlanMode tool calls.

Three outcomes:
  "approve"        → PermissionResultAllow  (execution starts)
  ("feedback", str)→ PermissionResultDeny(message=feedback)  (Claude revises)
  "reject"         → PermissionResultDeny(message="Plan rejected.")  (stops)
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Markdown, Rule, Static
from rai.client.claude import ClaudeHITLRequestEvent


def _esc(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


class ExitPlanModeScreen(ModalScreen[tuple[str, str] | str]):
    """Plan approval modal.

    Dismisses with:
      "approve"            → allow ExitPlanMode, execution starts
      ("feedback", text)   → deny with edit feedback, Claude revises
      "reject"             → deny outright, Claude stops
    """

    BINDINGS = [
        Binding("escape", "reject", "Reject", show=False, priority=True),
    ]

    CSS = """
    ExitPlanModeScreen {
        align: center middle;
        background: $background 65%;
    }

    #epm-container {
        width: 76;
        height: 40;
        border: round $accent 55%;
        background: $surface;
        padding: 0;
        layout: vertical;
    }

    #epm-header {
        height: 3;
        background: $panel;
        padding: 0 2;
        align: left middle;
    }

    .epm-rule {
        height: 1;
        margin: 0;
        color: $accent 20%;
    }

    #epm-header-title {
        width: 1fr;
        color: $accent;
        text-style: bold;
    }

    #epm-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #epm-scroll {
        height: 1fr;
        padding: 1 2;
        scrollbar-size: 1 1;
        scrollbar-color: $accent 25%;
    }

    #epm-plan-md {
        background: transparent;
        margin: 0;
        padding: 0;
    }

    #epm-feedback-row {
        height: 5;
        padding: 0 2;
    }

    #epm-feedback-label {
        height: 1;
        color: $text-muted;
        margin-top: 1;
    }

    #epm-feedback-input {
        border: round $warning 40%;
        background: $panel;
        height: 3;
    }

    #epm-feedback-input:focus {
        border: round $warning;
    }

    #epm-footer {
        height: 3;
        padding: 0 2;
    }

    #btn-approve  { width: 1fr; margin: 0 1; }
    #btn-feedback { width: 1fr; margin: 0 1; }
    #btn-reject   { width: 1fr; margin: 0 1; }
    """

    def __init__(self, event: ClaudeHITLRequestEvent) -> None:
        super().__init__()
        inp = event.input or {}
        # ExitPlanMode input has "plan" (markdown text) and optionally "planFilePath"
        self._plan_text: str = inp.get("plan", "") or inp.get("planFilePath", "")
        self._tool_use_id = event.tool_use_id

    def compose(self) -> ComposeResult:
        with Container(id="epm-container"):
            with Horizontal(id="epm-header"):
                yield Static("📋 Plan Ready — Review & Approve", id="epm-header-title")
                yield Static("[bold red]\\[x][/bold red]", id="epm-close")
            yield Rule(classes="epm-rule")

            with VerticalScroll(id="epm-scroll"):
                yield Markdown(self._plan_text or "_No plan content provided._", id="epm-plan-md")
            yield Rule(classes="epm-rule")

            with Container(id="epm-feedback-row"):
                yield Static("[dim]Edit feedback (optional — fill before clicking Edit & Resubmit):[/dim]",
                             id="epm-feedback-label")
                yield Input(placeholder="Describe what needs to change…", id="epm-feedback-input")
            yield Rule(classes="epm-rule")

            with Horizontal(id="epm-footer"):
                yield Button("✅ Approve",          id="btn-approve",  variant="success")
                yield Button("✏️  Edit & Resubmit", id="btn-feedback", variant="warning")
                yield Button("❌ Reject",            id="btn-reject",   variant="error")

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "epm-close":
            self.action_reject()

    @on(Button.Pressed, "#btn-approve")
    def on_approve(self) -> None:
        self.dismiss("approve")

    @on(Button.Pressed, "#btn-feedback")
    def on_feedback(self) -> None:
        inp = self.query_one("#epm-feedback-input", Input)
        feedback = inp.value.strip()
        if not feedback:
            # Highlight the input to signal feedback is required
            inp.focus()
            inp.add_class("-error")
            self.set_timer(1.5, lambda: inp.remove_class("-error"))
            return
        self.dismiss(("feedback", feedback))

    @on(Button.Pressed, "#btn-reject")
    def on_reject(self) -> None:
        self.action_reject()

    def action_reject(self) -> None:
        self.dismiss("reject")
