"""UserMessage — user prompt line with accent chevron and escaped text."""

from __future__ import annotations

from textual.widgets import Static


def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


class UserMessage(Static):
    """User prompt — bold accent ❯ prefix + escaped text (no Markdown parsing)."""

    DEFAULT_CSS = """
    UserMessage {
        padding: 0 1;
        margin: 0 0 1 0;
        color: $text;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(f"[bold $accent]❯[/] {_escape(text)}")
