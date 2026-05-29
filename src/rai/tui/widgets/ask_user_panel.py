"""AskUserPanel — overlay panel that presents agent questions and collects answers."""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


class AskUserPanel(Widget):
    """Panel that slides up when the agent calls ask_user, presenting questions
    with text inputs.  The user fills in answers and presses ctrl+enter to submit
    or escape to cancel.
    """

    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+enter", "submit", "Submit", show=True),
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    DEFAULT_CSS = """
    AskUserPanel {
        height: 0;
        max-height: 20;
        overflow: hidden hidden;
        padding: 0 1;
    }
    AskUserPanel.panel-open {
        height: auto;
    }
    AskUserPanel #ask-user-header {
        text-style: bold;
        margin-bottom: 1;
    }
    AskUserPanel #ask-user-footer {
        margin-top: 0;
    }
    AskUserPanel .ask-user-q-label {
        margin-top: 1;
    }
    AskUserPanel Input {
        margin: 0 0 0 0;
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
        self._inputs: list[Input] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="ask-user-header")
        yield Container(id="ask-user-questions")
        yield Static(
            "[dim]ctrl+enter[/dim] submit  [dim]·[/dim]  [dim]esc[/dim] cancel  [dim]·[/dim]  [dim]tab[/dim] next",
            id="ask-user-footer",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(self, questions: list[dict], thread_id: str, run_id: str) -> None:
        self._questions = questions
        self._thread_id = thread_id
        self._run_id = run_id
        self._inputs = []
        self.add_class("panel-open")
        self.call_after_refresh(self._rebuild)

    def hide(self) -> None:
        self.remove_class("panel-open")
        self._questions = []
        self._inputs = []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        try:
            count = len(self._questions)
            noun = "question" if count == 1 else "questions"
            self.query_one("#ask-user-header", Static).update(
                f"[bold $accent]? Agent has {count} {noun}[/bold $accent]"
            )

            q_container = self.query_one("#ask-user-questions", Container)
            q_container.remove_children()
            self._inputs = []

            for i, q in enumerate(self._questions):
                q_text = q.get("question", f"Question {i + 1}")
                label = Static(
                    f"[dim]{i + 1}.[/dim] {_escape(q_text)}",
                    classes="ask-user-q-label",
                )
                inp = Input(placeholder="Type your answer…", classes="ask-user-input")
                self._inputs.append(inp)
                q_container.mount(label)
                q_container.mount(inp)

            if self._inputs:
                self.call_after_refresh(self._inputs[0].focus)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_submit(self) -> None:
        answers = [inp.value for inp in self._inputs]
        thread_id = self._thread_id
        run_id = self._run_id
        self.hide()
        self.post_message(self.Answered(thread_id, run_id, answers))

    def action_cancel(self) -> None:
        thread_id = self._thread_id
        run_id = self._run_id
        self.hide()
        self.post_message(self.Cancelled(thread_id, run_id))
