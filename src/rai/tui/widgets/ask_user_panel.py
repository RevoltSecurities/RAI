"""AskUserPanel — inline chat panel with radio/checkbox/text for agent questions."""

from __future__ import annotations

from typing import Any, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, RadioButton, RadioSet, Static


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


class AskUserPanel(Widget):
    """Inline panel for ask_user questions — supports radio, checkbox, and text.

    Renders in the chat area (same position as HITLPanel) with clickable
    Submit/Cancel buttons and keyboard shortcuts.
    """

    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+s", "submit", "Submit", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    AskUserPanel {
        height: 0;
        overflow: hidden hidden;
        background: $surface 92%;
        border: round $accent 80%;
        padding: 1 2;
    }
    AskUserPanel:focus-within {
        border: round $accent;
    }
    AskUserPanel.panel-open {
        height: auto;
        max-height: 24;
    }
    #ask-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
        text-align: center;
        height: 1;
    }
    #ask-scroll {
        height: auto;
        max-height: 14;
        margin-bottom: 0;
    }
    .ask-q-label {
        text-style: bold;
        color: $text;
        margin-bottom: 0;
        margin-top: 1;
    }
    .ask-q-header {
        color: $primary;
        text-style: bold;
    }
    .ask-option-desc {
        color: $text-muted;
        padding: 0 0 0 4;
        height: auto;
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
        margin: 0;
    }
    Checkbox:focus {
        background: $primary 15%;
    }
    #ask-buttons {
        height: 3;
        align: center middle;
    }
    #ask-buttons Button {
        margin: 0 1;
        min-width: 12;
    }
    #ask-bindings {
        color: $text-muted;
        text-align: center;
        height: 1;
    }
    """

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class Answered(Message):
        def __init__(self, thread_id: str, run_id: str, answers: list[str]) -> None:
            super().__init__()
            self.thread_id = thread_id
            self.run_id = run_id
            self.answers = answers

    class Cancelled(Message):
        def __init__(self, thread_id: str, run_id: str) -> None:
            super().__init__()
            self.thread_id = thread_id
            self.run_id = run_id

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._questions: list[dict] = []
        self._thread_id = ""
        self._run_id = ""
        self._inputs: list[Input | None] = []
        self._radio_sets: list[RadioSet | None] = []
        self._checkbox_groups: list[list[Checkbox]] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="ask-title")
        yield VerticalScroll(id="ask-scroll")
        with Horizontal(id="ask-buttons"):
            yield Button("✓ Submit", id="ask-btn-submit", variant="success")
            yield Button("✗ Cancel", id="ask-btn-cancel", variant="error")
        yield Static(
            "[dim]ctrl+s submit  ·  esc cancel  ·  tab next[/dim]",
            id="ask-bindings",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self, questions: list[dict], thread_id: str, run_id: str) -> None:
        self._questions = questions
        self._thread_id = thread_id
        self._run_id = run_id
        self._inputs = []
        self._radio_sets = []
        self._checkbox_groups = []
        self.add_class("panel-open")
        self.call_after_refresh(self._rebuild)

    def hide(self) -> None:
        self.remove_class("panel-open")
        self._questions = []
        self._inputs = []
        self._radio_sets = []
        self._checkbox_groups = []

    # ------------------------------------------------------------------
    # Internal — build question widgets
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        try:
            count = len(self._questions)
            noun = "question" if count == 1 else "questions"
            self.query_one("#ask-title", Static).update(
                f"? Agent has {count} {noun}"
            )

            scroll = self.query_one("#ask-scroll", VerticalScroll)
            scroll.remove_children()
            self._inputs = []
            self._radio_sets = []
            self._checkbox_groups = []

            for i, q in enumerate(self._questions):
                question_text = q.get("question", f"Question {i + 1}")
                header_text = q.get("header", "")
                options = q.get("options") or []
                multi = bool(q.get("multiSelect", False))

                if header_text:
                    label = Static(
                        f"[bold $primary]{_escape(header_text)}[/bold $primary]  "
                        f"{_escape(question_text)}",
                        classes="ask-q-label",
                    )
                else:
                    label = Static(
                        f"[dim]{i + 1}.[/dim] {_escape(question_text)}",
                        classes="ask-q-label",
                    )
                scroll.mount(label)

                if options and not multi:
                    radios = [RadioButton(_escape(o.get("label", ""))) for o in options]
                    rs = RadioSet(*radios)
                    self._radio_sets.append(rs)
                    self._inputs.append(None)
                    self._checkbox_groups.append([])
                    scroll.mount(rs)
                    for o in options:
                        desc = o.get("description", "")
                        if desc:
                            scroll.mount(Static(
                                f"  [dim]{_escape(desc)}[/dim]",
                                classes="ask-option-desc",
                            ))

                elif options and multi:
                    boxes: list[Checkbox] = []
                    self._radio_sets.append(None)
                    self._inputs.append(None)
                    for o in options:
                        ol = o.get("label", "")
                        desc = o.get("description", "")
                        cb = Checkbox(
                            _escape(ol) + (f"  [dim]{_escape(desc)}[/dim]" if desc else "")
                        )
                        boxes.append(cb)
                        scroll.mount(cb)
                    self._checkbox_groups.append(boxes)

                else:
                    inp = Input(placeholder="Type your answer…")
                    self._inputs.append(inp)
                    self._radio_sets.append(None)
                    self._checkbox_groups.append([])
                    scroll.mount(inp)

            # Focus first interactive widget
            for inp in self._inputs:
                if inp is not None:
                    self.call_after_refresh(inp.focus)
                    return
            for rs in self._radio_sets:
                if rs is not None:
                    self.call_after_refresh(rs.focus)
                    return
            self.focus()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Collect answers
    # ------------------------------------------------------------------

    def _collect_answers(self) -> list[str]:
        answers: list[str] = []
        for i, q in enumerate(self._questions):
            options = q.get("options") or []
            multi = bool(q.get("multiSelect", False))

            if options and not multi:
                rs = self._radio_sets[i] if i < len(self._radio_sets) else None
                if rs is not None and rs.pressed_index is not None:
                    idx = rs.pressed_index
                    label = options[idx].get("label", str(idx)) if idx < len(options) else ""
                    answers.append(label)
                else:
                    answers.append("")

            elif options and multi:
                boxes = self._checkbox_groups[i] if i < len(self._checkbox_groups) else []
                selected = [
                    options[j].get("label", "")
                    for j, cb in enumerate(boxes)
                    if cb.value and j < len(options)
                ]
                answers.append(", ".join(selected))

            else:
                inp = self._inputs[i] if i < len(self._inputs) else None
                answers.append(inp.value if inp is not None else "")

        return answers

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    @on(Button.Pressed, "#ask-btn-submit")
    def _btn_submit(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_submit()

    @on(Button.Pressed, "#ask-btn-cancel")
    def _btn_cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    def action_submit(self) -> None:
        answers = self._collect_answers()
        thread_id = self._thread_id
        run_id = self._run_id
        self.hide()
        self.post_message(self.Answered(thread_id, run_id, answers))

    def action_cancel(self) -> None:
        thread_id = self._thread_id
        run_id = self._run_id
        self.hide()
        self.post_message(self.Cancelled(thread_id, run_id))
