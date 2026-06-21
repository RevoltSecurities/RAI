"""StatusBar — bottom-docked status line matching Claude Code UX."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget


class StatusBar(Widget):
    """Unified bottom status bar showing plan mode, agent count, and hints.

    When plan mode is active, the bar uses a bold amber/yellow background
    that stands out prominently — matching Claude Code's visual treatment.
    """

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 2;
        display: none;
    }

    StatusBar.-visible {
        display: block;
    }

    StatusBar.-plan-active {
        background: #b8860b;
        color: #ffffff;
    }
    """

    plan_mode = reactive(False)
    agent_count = reactive(0)
    is_streaming = reactive(False)
    cost = reactive(0.0)
    context_pct = reactive(0.0)

    def render(self) -> str:
        parts: list[str] = []
        if self.plan_mode:
            parts.append("[bold]‖  Plan Mode[/bold]  [dim]RAI is exploring and designing — read-only[/dim]")
        if self.cost > 0:
            parts.append(f"[bold]${self.cost:.4f}[/bold]")
        if self.context_pct > 0:
            parts.append(f"{self.context_pct:.0f}% ctx")
        if self.agent_count > 0:
            n = self.agent_count
            parts.append(f"{n} agent{'s' if n != 1 else ''} running")
        if self.is_streaming:
            parts.append("[dim]esc to interrupt[/dim]")
        if self.agent_count > 0:
            parts.append("[dim]ctrl+a to manage[/dim]")
        return " · ".join(parts) if parts else ""

    def watch_plan_mode(self, _val: bool) -> None:
        self._update_visibility()
        self.set_class(_val, "-plan-active")

    def watch_agent_count(self, _val: int) -> None:
        self._update_visibility()

    def watch_is_streaming(self, _val: bool) -> None:
        self._update_visibility()

    def watch_cost(self, _val: float) -> None:
        self._update_visibility()

    def watch_context_pct(self, _val: float) -> None:
        self._update_visibility()

    def _update_visibility(self) -> None:
        visible = (
            self.plan_mode or self.agent_count > 0 or self.is_streaming
            or self.cost > 0 or self.context_pct > 0
        )
        self.set_class(visible, "-visible")
