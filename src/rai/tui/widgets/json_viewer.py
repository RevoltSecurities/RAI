"""JSONLViewer — renders JSON / JSONL tool outputs with github-dark syntax highlighting."""

from __future__ import annotations

import json
from typing import Any

from rich.syntax import Syntax as RichSyntax
from textual.app import ComposeResult
from textual.widgets import Collapsible, Static
from textual.widget import Widget



def _escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")

_THEME = "github-dark"


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n… ({len(s) - n} more chars)"


def _json_widget(data: Any) -> Static:
    """Static with syntax-highlighted JSON (github-dark theme)."""
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(data)
    return Static(RichSyntax(text, "json", theme=_THEME, word_wrap=True))


class JSONLViewer(Widget):
    """Renders any tool output:
    - Single JSON object  → Syntax-highlighted JSON (github-dark)
    - JSONL (N lines)    → Collapsible with highlighted JSON per record (cap 50)
    - Plain text         → Static (truncated to 2000 chars)
    """

    DEFAULT_CSS = """
    JSONLViewer {
        height: auto;
    }
    """

    def __init__(self, raw: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._raw = raw

    def compose(self) -> ComposeResult:
        raw = self._raw
        if raw is None:
            yield Static("[dim](no output)[/dim]")
            return

        if isinstance(raw, (dict, list)):
            yield _json_widget(raw)
            return

        text = str(raw)
        lines = [ln for ln in text.strip().splitlines() if ln.strip()]

        if not lines:
            yield Static("[dim](empty)[/dim]")
            return

        if len(lines) == 1:
            try:
                yield _json_widget(json.loads(text))
            except json.JSONDecodeError:
                yield Static(_escape(_truncate(text, 2000)))
            return

        # Try JSONL: every non-empty line is a valid JSON value
        parsed: list[Any] = []
        for line in lines:
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                parsed = []
                break

        if parsed:
            with Collapsible(title=f"{len(parsed)} records", collapsed=True):
                for obj in parsed[:50]:
                    yield _json_widget(obj)
        else:
            # Multi-line plain text — try whole block as JSON first
            try:
                yield _json_widget(json.loads(text))
            except json.JSONDecodeError:
                yield Static(_escape(_truncate(text, 2000)))
