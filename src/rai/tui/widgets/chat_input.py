"""InputBar — mode indicator + fuzzy slash-command completion."""

from __future__ import annotations

from typing import Callable

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static, TextArea


# ---------------------------------------------------------------------------
# Fuzzy scoring (mirrors existing ChatInput algorithm)
# ---------------------------------------------------------------------------

def _score(query: str, candidate: str, description: str = "") -> int:
    q = query.lower()
    c = candidate.lower()
    if not q:
        return 1
    if q == c:
        return 250
    if c.startswith(q):
        return 200
    if q in c:
        return 150
    # boundary match: query chars appear at word boundaries
    boundary_score = 0
    parts = c.split("_") + c.split("-") + c.split(" ")
    initials = "".join(p[0] for p in parts if p)
    if q in initials:
        boundary_score = 110
    # description match
    desc_score = 90 if q in description.lower() else 0
    # sequence match
    seq_score = 0
    ci = 0
    for ch in q:
        idx = c.find(ch, ci)
        if idx == -1:
            seq_score = 0
            break
        seq_score += 10 + max(0, 70 - idx * 5)
        ci = idx + 1
    return max(boundary_score, desc_score, seq_score)


_SLASH_COMMANDS = [
    ("/help",      "Show help"),
    ("/clear",     "Clear messages"),
    ("/agents",    "List agents"),
    ("/threads",   "Thread browser"),
    ("/runs",      "Runs browser"),
    ("/bg",        "Toggle background runs panel"),
    ("/theme",     "Theme picker"),
    ("/compact",   "Compact context · /compact status for token estimate"),
    ("/new",       "New thread"),
    ("/auto",      "Toggle auto-approve"),
    ("/quit",      "Quit"),
    ("/debug",     "Show TUI debug state"),
    ("/model",     "Show / override model for next run"),
    ("/mcp",       "Show configured MCP servers"),
    ("/skills",    "List available skills"),
    ("/editor",    "Open prompt in $EDITOR (Ctrl+X)"),
    ("/changelog", "Open changelog in browser"),
    ("/issue",     "Open GitHub issues in browser"),
    ("/tokens",    "Show token usage"),
    ("/findings",  "Show security findings panel"),
    ("/create-agent", "Create a new RAI subagent via guided wizard"),
]

_MODE_GLYPHS = {
    "normal": "❯",
    "shell": "!",
    "command": "/",
    "delegate": "@",
}

# Paste threshold: above either limit triggers the badge display
_PASTE_MAX_CHARS = 500
_PASTE_MAX_LINES = 5


class _ChatInputField(Input):
    """Input subclass with large-paste badge and Screen-selection copy fix."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pending_paste: str = ""
        self._pending_prefix: str = ""

    # ------------------------------------------------------------------
    # Paste handling
    # ------------------------------------------------------------------

    def _on_paste(self, event: events.Paste) -> None:
        # prevent_default stops the MRO walk so Input._on_paste doesn't also insert
        event.prevent_default()
        text = event.text
        if not text:
            return
        lines = text.splitlines()
        is_large = len(text) > _PASTE_MAX_CHARS or len(lines) > _PASTE_MAX_LINES
        if not is_large:
            normalized = (
                " ".join(ln for ln in lines if ln.strip())
                or text.replace("\n", " ")
            )
            self.insert(normalized.strip(), self.cursor_position)
            event.stop()
            return
        # Large paste: preserve typed prefix, insert badge at cursor
        before = self.value[:self.cursor_position]
        after = self.value[self.cursor_position:]
        self._pending_paste = text
        self._pending_prefix = before
        kb = len(text.encode()) / 1024
        badge = f"[⎘ {len(lines)} lines · {kb:.1f} KB]"
        self.value = before + badge + after
        self.cursor_position = len(before) + len(badge)
        event.stop()

    def watch_value(self, value: str) -> None:
        if not value:
            self._pending_paste = ""
            self._pending_prefix = ""

    @property
    def effective_value(self) -> str:
        """Return prefix + pending paste if large paste is pending, else visible value."""
        if self._pending_paste:
            return self._pending_prefix + self._pending_paste
        return self.value

    # ------------------------------------------------------------------
    # Copy: prefer Screen-level mouse selection over Input's own selection
    # ------------------------------------------------------------------

    def action_copy(self) -> None:
        try:
            selected = self.screen.get_selected_text()
            if selected:
                self.app.copy_to_clipboard(selected)
                return
        except Exception:
            pass
        super().action_copy()


class InputBar(Widget):
    """Chat input bar with mode detection and fuzzy slash completion."""

    DEFAULT_CSS = """
    InputBar {
        height: auto;
        min-height: 3;
        max-height: 20;
        border: round $accent 40%;
        background: $surface 30%;
        padding: 0 1;
    }
    InputBar:focus-within {
        border: round $accent;
    }
    #mode-indicator {
        width: 3;
        color: $accent;
        content-align: center middle;
    }
    #chat-input {
        border: none;
        background: transparent;
        width: 1fr;
    }
    #completion-popup {
        layer: autocomplete;
        display: none;
        height: auto;
        max-height: 10;
        background: $surface 95%;
        border: round $accent;
        margin: 0 0 0 4;
    }
    #paste-preview {
        display: none;
        height: auto;
        max-height: 12;
        border: dashed $accent 40%;
        margin: 1 0 0 4;
        padding: 0;
        background: $surface 20%;
    }
    """

    BINDINGS = [
        Binding("tab", "complete", "Complete", show=False),
        Binding("escape", "dismiss_completion", "Dismiss", show=False),
        Binding("up", "completion_up", "Up", show=False),
        Binding("down", "completion_down", "Down", show=False),
    ]

    class Submitted(Message):
        def __init__(self, value: str, mode: str) -> None:
            super().__init__()
            self.value = value
            self.mode = mode

    def __init__(
        self,
        on_submit: Callable[[str, str], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._mode = "normal"
        self._completions: list[str] = []
        self._completions_with_desc: list[tuple[str, str]] = []
        self._comp_idx = 0
        self._on_submit = on_submit
        self._disabled = False
        self._plan_mode = False
        self._skill_commands: list[tuple[str, str]] = []
        # Prompt history (shell-style Up/Down navigation)
        self._history: list[str] = []       # newest-first; lazy-loaded
        self._history_loaded: bool = False
        self._history_idx: int = -1          # -1 = not navigating
        self._history_current: str = ""      # text saved when navigation starts
        self._history_agent: str = "rai"     # tag written to history.jsonl

    def compose(self) -> ComposeResult:
        yield Static("❯", id="mode-indicator")
        yield _ChatInputField(placeholder="Type a message… (/help for commands)", id="chat-input")
        yield Static("", id="completion-popup")
        yield TextArea("", id="paste-preview", read_only=True)

    def _detect_mode(self, value: str) -> str:
        if value.startswith("!"):
            return "shell"
        if value.startswith("/"):
            return "command"
        if value.startswith("@"):
            return "delegate"
        return "normal"

    def _update_mode(self, mode: str) -> None:
        self._mode = mode
        if self._plan_mode:
            markup = "[bold yellow]◈[/bold yellow]"
        else:
            glyph = _MODE_GLYPHS.get(mode, "❯")
            colors = {"normal": "green", "shell": "red", "command": "cyan", "delegate": "magenta"}
            color = colors.get(mode, "green")
            markup = f"[{color}]{glyph}[/{color}]"
        try:
            self.query_one("#mode-indicator", Static).update(markup)
        except Exception:
            pass

    def set_plan_mode(self, active: bool) -> None:
        self._plan_mode = active
        self._update_mode(self._mode)
        try:
            inp = self.query_one("#chat-input", _ChatInputField)
            inp.placeholder = (
                "◈ Plan mode active — type to send approval/guidance…"
                if active else
                ("Waiting for response…" if self._disabled else "Type a message… (/help for commands)")
            )
        except Exception:
            pass
        if active:
            self.add_class("plan-mode")
        else:
            self.remove_class("plan-mode")

    def _render_popup(self) -> None:
        if not self._completions_with_desc:
            return
        n = len(self._completions_with_desc)
        idx = self._comp_idx % n
        lines = []
        for i, (cmd, desc) in enumerate(self._completions_with_desc):
            cmd_part = f"[bold][cyan]{cmd}[/cyan][/bold]"
            desc_part = f"  [dim]{desc}[/dim]" if desc else ""
            row = f"{cmd_part}{desc_part}"
            if i == idx:
                row = f"[reverse] {row} [/reverse]"
            else:
                row = f" {row} "
            lines.append(row)
        try:
            self.query_one("#completion-popup", Static).update("\n".join(lines))
        except Exception:
            pass

    def set_skill_commands(self, skills: list[dict]) -> None:
        self._skill_commands = [
            (f"/skill:{s['name']}", s.get("description", "")[:80])
            for s in skills
        ]

    def _show_completions(self, value: str) -> None:
        if not value.startswith("/"):
            self._hide_completions()
            return
        all_commands = _SLASH_COMMANDS + list(self._skill_commands)
        query = value[1:]
        scored = [
            (cmd, desc, _score(query, cmd[1:], desc))
            for cmd, desc in all_commands
        ]
        filtered = [(cmd, desc) for cmd, desc, sc in scored if sc > 0]
        filtered.sort(key=lambda x: -_score(query, x[0][1:], x[1]))
        self._completions_with_desc = filtered[:8]
        self._completions = [cmd for cmd, _ in self._completions_with_desc]
        if not self._completions:
            self._hide_completions()
            return
        self._comp_idx = 0
        self._render_popup()
        try:
            self.query_one("#completion-popup", Static).styles.display = "block"
        except Exception:
            pass

    def _hide_completions(self) -> None:
        self._completions = []
        self._completions_with_desc = []
        self._comp_idx = 0
        try:
            self.query_one("#completion-popup", Static).styles.display = "none"
        except Exception:
            pass

    def _show_paste_preview(self, text: str) -> None:
        try:
            ta = self.query_one("#paste-preview", TextArea)
            ta.load_text(text)
            ta.styles.display = "block"
        except Exception:
            pass

    def _hide_paste_preview(self) -> None:
        try:
            self.query_one("#paste-preview", TextArea).styles.display = "none"
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        value = event.value
        self._update_mode(self._detect_mode(value))
        self._show_completions(value)
        try:
            inp = self.query_one("#chat-input", _ChatInputField)
            if inp._pending_paste:
                self._show_paste_preview(inp._pending_paste)
            else:
                self._hide_paste_preview()
        except Exception:
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        inp = self.query_one("#chat-input", _ChatInputField)
        value = inp.effective_value.strip()
        if not value:
            return
        # If the completion popup is open, resolve to the selected completion
        if self._completions and value.startswith("/"):
            value = self._completions[self._comp_idx % len(self._completions)]
        # Slash commands always pass through even during an active run
        if self._disabled and not value.startswith("/"):
            return
        self._hide_completions()
        self._hide_paste_preview()
        # Persist real prompts (not slash commands) to ~/.rai/history.jsonl
        if value and not value.startswith("/"):
            try:
                from rai.tui.history import append_history
                append_history(value, agent=self._history_agent)
                self._history_loaded = False  # reload on next Up (picks up new entry)
            except Exception:
                pass
        # Reset history navigation state on every submission
        self._history_idx = -1
        self._history_current = ""
        inp._pending_paste = ""
        inp._pending_prefix = ""
        inp.clear()
        self._update_mode("normal")
        self.post_message(self.Submitted(value, self._mode))
        if self._on_submit:
            self._on_submit(value, self._mode)

    def action_complete(self) -> None:
        if self._completions:
            cmd = self._completions[self._comp_idx % len(self._completions)]
            try:
                self.query_one("#chat-input", _ChatInputField).value = cmd + " "
            except Exception:
                pass

    def action_dismiss_completion(self) -> None:
        self._hide_completions()

    def action_completion_up(self) -> None:
        if self._completions:
            self._comp_idx = (self._comp_idx - 1) % len(self._completions)
            self._render_popup()
        else:
            self._navigate_history(-1)

    def action_completion_down(self) -> None:
        if self._completions:
            self._comp_idx = (self._comp_idx + 1) % len(self._completions)
            self._render_popup()
        else:
            self._navigate_history(1)

    def _navigate_history(self, direction: int) -> None:
        """Navigate prompt history. direction=-1 → older (Up), +1 → newer (Down)."""
        if not self._history_loaded:
            try:
                from rai.tui.history import load_history
                self._history = load_history(agent=self._history_agent)
            except Exception:
                self._history = []
            self._history_loaded = True

        if not self._history:
            return

        try:
            inp = self.query_one("#chat-input", _ChatInputField)
        except Exception:
            return

        if self._history_idx == -1:
            if direction == -1:
                # First Up press — save current text, jump to most recent entry
                self._history_current = inp.value
                self._history_idx = 0
            else:
                return  # already at current, Down does nothing
        else:
            new_idx = self._history_idx + direction
            if new_idx < 0:
                # Down past most recent → restore what user was typing
                self._history_idx = -1
                inp.value = self._history_current
                inp.cursor_position = len(self._history_current)
                return
            if new_idx >= len(self._history):
                return  # Up past oldest → do nothing
            self._history_idx = new_idx

        new_value = self._history[self._history_idx]
        inp.value = new_value
        inp.cursor_position = len(new_value)

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = disabled
        try:
            inp = self.query_one("#chat-input", _ChatInputField)
            if disabled:
                inp.placeholder = "Waiting for response… (esc to cancel)"
            elif not self._plan_mode:
                inp.placeholder = "Type a message… (/help for commands)"
        except Exception:
            pass
