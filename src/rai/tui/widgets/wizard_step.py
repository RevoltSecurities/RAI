"""WizardStepWidget — inline wizard step widget for /create-agent and similar TUI wizards."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, Input, Static


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


class WizardStepWidget(Widget):
    """Inline wizard step — mounts in #messages, resolves a Future on submit/cancel.

    question_type="text"   → shows an Input, Enter submits
    question_type="choice" → shows a Button per choice in `choices`

    The widget removes itself after resolving the Future.
    """

    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel_step", "Cancel", show=True),
    ]

    DEFAULT_CSS = """
    WizardStepWidget {
        border: round $accent;
        padding: 1 2;
        margin: 0 0 1 0;
        height: auto;
    }
    WizardStepWidget #wiz-question {
        text-style: bold;
        padding-bottom: 1;
    }
    WizardStepWidget #wiz-hint {
        color: $text-muted;
        padding-top: 1;
    }
    WizardStepWidget #wiz-buttons {
        height: auto;
        margin-top: 0;
    }
    WizardStepWidget .wiz-choice {
        margin: 0 1 0 0;
        min-width: 18;
    }
    """

    def __init__(
        self,
        question: str,
        question_type: str = "text",
        choices: list[str] | None = None,
        placeholder: str = "Type here…",
        *,
        answer_future: asyncio.Future,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._question = question
        self._question_type = question_type
        self._choices = choices or []
        self._placeholder = placeholder
        self._future = answer_future
        self._resolved = False

    def compose(self) -> ComposeResult:
        yield Static(_escape(self._question), id="wiz-question")
        if self._question_type == "text":
            yield Input(placeholder=self._placeholder, id="wiz-input")
            yield Static(
                "[dim]Enter[/dim] to confirm  ·  [dim]Esc[/dim] to cancel",
                id="wiz-hint",
            )
        else:
            with Horizontal(id="wiz-buttons"):
                for i, choice in enumerate(self._choices):
                    variant = "primary" if i == 0 else "default"
                    yield Button(
                        _escape(choice),
                        id=f"wiz-choice-{i}",
                        variant=variant,
                        classes="wiz-choice",
                    )
            yield Static(
                "[dim]Tab[/dim] to move  ·  [dim]Enter[/dim] to select  ·  [dim]Esc[/dim] to cancel",
                id="wiz-hint",
            )

    def on_mount(self) -> None:
        if self._question_type == "text":
            try:
                self.query_one("#wiz-input", Input).focus()
            except Exception:
                pass
        else:
            try:
                self.query_one("Button").focus()
            except Exception:
                pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self._resolve(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("wiz-choice-"):
            idx = int(btn_id.removeprefix("wiz-choice-"))
            if 0 <= idx < len(self._choices):
                self._resolve(self._choices[idx])

    def on_unmount(self) -> None:
        # Resolve with None if removed externally (e.g. /clear) so the wizard coroutine unblocks.
        if not self._resolved:
            self._resolved = True
            if not self._future.done():
                self._future.set_result(None)

    def action_cancel_step(self) -> None:
        self._resolve(None)

    def _resolve(self, value: str | None) -> None:
        if self._resolved:
            return
        self._resolved = True
        if not self._future.done():
            self._future.set_result(value)
        self.call_after_refresh(self.remove)
