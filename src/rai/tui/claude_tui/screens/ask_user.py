"""AskUserScreen — modern interactive modal for AskUserQuestion tool calls."""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, RadioButton, RadioSet, Static


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


class AskUserScreen(ModalScreen[dict[str, str] | None]):
    """Interactive questions modal for AskUserQuestion tool calls.

    Dismisses with None on cancel (→ deny), or dict[question_text, answer] on submit.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    CSS = """
    AskUserScreen {
        align: center middle;
        background: $background 65%;
    }

    #ask-container {
        width: 82%;
        max-width: 92;
        height: auto;
        max-height: 85%;
        border: round $accent 55%;
        background: $surface;
        padding: 0 0 1 0;
    }

    #ask-header {
        height: 3;
        background: $panel;
        border-bottom: solid $accent 20%;
        padding: 0 2;
        align: left middle;
    }

    #ask-title {
        width: 1fr;
        color: $accent;
        text-style: bold;
    }

    #ask-close {
        width: 3;
        text-align: right;
        color: $error;
        text-style: bold;
    }

    #ask-scroll {
        height: auto;
        max-height: 28;
        padding: 1 2;
    }

    .ask-q-block {
        margin-bottom: 1;
        height: auto;
    }

    .ask-q-label {
        text-style: bold;
        color: $text;
        margin-bottom: 0;
    }

    .ask-q-header-chip {
        color: $primary;
        margin-bottom: 0;
    }

    RadioSet {
        background: $panel;
        border: round $primary 35%;
        padding: 0 1;
        margin-top: 0;
        height: auto;
    }

    RadioButton {
        background: transparent;
    }

    RadioButton:focus {
        background: $primary 15%;
    }

    Checkbox {
        background: $panel;
        border: round $primary 25%;
        padding: 0 1;
        margin: 0 0 0 0;
    }

    Checkbox:focus {
        background: $primary 15%;
        border: round $primary 60%;
    }

    .ask-option-desc {
        color: $text-muted;
        padding: 0 0 0 4;
        margin: 0 0 0 2;
        height: auto;
    }

    Input {
        border: round $primary 40%;
        background: $panel;
        margin-top: 0;
    }

    Input:focus {
        border: round $accent;
    }

    #ask-footer {
        height: 3;
        padding: 0 2;
        margin-top: 0;
        align: right middle;
        border-top: solid $accent 20%;
    }

    #btn-submit {
        margin-left: 1;
        min-width: 12;
    }

    #btn-cancel {
        margin-left: 1;
        min-width: 10;
    }
    """

    def __init__(self, questions: list[dict[str, Any]]) -> None:
        super().__init__()
        self._questions = questions
        self._inputs: list[Input | None] = []
        self._radio_sets: list[RadioSet | None] = []
        self._checkbox_groups: list[list[Checkbox]] = []

    def compose(self) -> ComposeResult:
        count = len(self._questions)
        noun = "question" if count == 1 else "questions"

        with Container(id="ask-container"):
            with Horizontal(id="ask-header"):
                yield Static(
                    f"[bold]? Agent has {count} {noun}[/bold]",
                    id="ask-title",
                )
                yield Static("[bold red]\\[x][/bold red]", id="ask-close")

            with VerticalScroll(id="ask-scroll"):
                for i, q in enumerate(self._questions):
                    yield from self._question_widgets(i, q)

            with Horizontal(id="ask-footer"):
                yield Button("Submit", id="btn-submit", variant="primary")
                yield Button("Cancel", id="btn-cancel", variant="default")

    def _question_widgets(self, idx: int, q: dict[str, Any]):
        question_text = q.get("question", "")
        header_text   = q.get("header", "")
        options       = q.get("options") or []
        multi         = bool(q.get("multiSelect", False))

        with Container(classes="ask-q-block"):
            if header_text:
                yield Static(
                    f"[bold $accent]{_escape(header_text)}[/bold $accent]  "
                    f"[dim]{_escape(question_text)}[/dim]",
                    classes="ask-q-label",
                )
            else:
                yield Static(_escape(question_text), classes="ask-q-label")

            if options and not multi:
                radios = [RadioButton(_escape(o.get("label", ""))) for o in options]
                rs = RadioSet(*radios)
                self._radio_sets.append(rs)
                self._inputs.append(None)
                self._checkbox_groups.append([])
                yield rs
                for o in options:
                    desc = o.get("description", "")
                    if desc:
                        yield Static(
                            f"[dim]{_escape(desc)}[/dim]",
                            classes="ask-option-desc",
                        )

            elif options and multi:
                boxes: list[Checkbox] = []
                self._radio_sets.append(None)
                self._inputs.append(None)
                for o in options:
                    label = o.get("label", "")
                    desc  = o.get("description", "")
                    cb = Checkbox(
                        _escape(label) + (f"  [dim]{_escape(desc)}[/dim]" if desc else "")
                    )
                    boxes.append(cb)
                    yield cb
                self._checkbox_groups.append(boxes)

            else:
                inp = Input(placeholder="Type your answer…")
                self._inputs.append(inp)
                self._radio_sets.append(None)
                self._checkbox_groups.append([])
                yield inp

    def on_mount(self) -> None:
        for inp in self._inputs:
            if inp is not None:
                self.call_after_refresh(inp.focus)
                return
        for rs in self._radio_sets:
            if rs is not None:
                self.call_after_refresh(rs.focus)
                return

    def on_click(self, event) -> None:
        if getattr(event.widget, "id", None) == "ask-close":
            self.action_cancel()

    @on(Button.Pressed, "#btn-submit")
    def on_submit_pressed(self) -> None:
        self.action_submit()

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel_pressed(self) -> None:
        self.action_cancel()

    def _collect_answers(self) -> dict[str, str]:
        answers: dict[str, str] = {}
        for i, q in enumerate(self._questions):
            question_text = q.get("question", f"Question {i + 1}")
            options       = q.get("options") or []
            multi         = bool(q.get("multiSelect", False))

            if options and not multi:
                rs = self._radio_sets[i]
                if rs is not None and rs.pressed_index is not None:
                    idx = rs.pressed_index
                    label = options[idx].get("label", str(idx)) if idx < len(options) else ""
                    answers[question_text] = label
                else:
                    answers[question_text] = ""

            elif options and multi:
                boxes = self._checkbox_groups[i]
                selected = [
                    options[j].get("label", "")
                    for j, cb in enumerate(boxes)
                    if cb.value and j < len(options)
                ]
                answers[question_text] = ", ".join(selected)

            else:
                inp = self._inputs[i]
                answers[question_text] = inp.value if inp is not None else ""

        return answers

    def action_submit(self) -> None:
        self.dismiss(self._collect_answers())

    def action_cancel(self) -> None:
        self.dismiss(None)
