"""RAI HTTP TUI themes — rai (LangChain dark), github-dark, glass, claude."""

from __future__ import annotations

from textual.theme import Theme

# ---------------------------------------------------------------------------
# RAI — LangChain dark (Tokyo-Night inspired)
# ---------------------------------------------------------------------------

LC_DARK   = "#11121D"
LC_CARD   = "#1A1B2E"
LC_PANEL  = "#25283B"
LC_BODY   = "#C0CAF5"
LC_BLUE   = "#7AA2F7"
LC_PURPLE = "#BB9AF7"
LC_GREEN  = "#9ECE6A"
LC_AMBER  = "#EB8B46"
LC_PINK   = "#F7768E"
LC_MUTED  = "#545C7E"
LC_SKILL  = "#A78BFA"

LANGCHAIN_THEME = Theme(
    name="rai",
    primary=LC_BLUE,
    secondary=LC_PURPLE,
    accent=LC_GREEN,
    foreground=LC_BODY,
    background=LC_DARK,
    surface=LC_CARD,
    panel=LC_PANEL,
    warning=LC_AMBER,
    error=LC_PINK,
    success=LC_GREEN,
    dark=True,
    variables={
        "footer-key-foreground": LC_BLUE,
        "tool":          LC_AMBER,
        "skill":         LC_SKILL,
        "surface-alpha": "92%",
    },
)

# ---------------------------------------------------------------------------
# GitHub Dark
# ---------------------------------------------------------------------------

GH_BG      = "#0d1117"
GH_SURFACE = "#161b22"
GH_PANEL   = "#21262d"
GH_TEXT    = "#e6edf3"
GH_BLUE    = "#58a6ff"
GH_PURPLE  = "#bc8cff"
GH_GREEN   = "#3fb950"
GH_AMBER   = "#d29922"
GH_RED     = "#f85149"
GH_TEAL    = "#39c5cf"
GH_MUTED   = "#8b949e"

GITHUB_DARK_THEME = Theme(
    name="github-dark",
    primary=GH_BLUE,
    secondary=GH_PURPLE,
    accent=GH_TEAL,
    foreground=GH_TEXT,
    background=GH_BG,
    surface=GH_SURFACE,
    panel=GH_PANEL,
    warning=GH_AMBER,
    error=GH_RED,
    success=GH_GREEN,
    dark=True,
    variables={
        "footer-key-foreground": GH_BLUE,
        "tool":          GH_AMBER,
        "skill":         GH_PURPLE,
        "surface-alpha": "94%",
    },
)

# ---------------------------------------------------------------------------
# Glass — glassmorphism (deep navy base, translucent panels)
# ---------------------------------------------------------------------------

GL_BG      = "#0a0a14"
GL_SURFACE = "#1a1a2e"
GL_PANEL   = "#16213e"
GL_TEXT    = "#e2e8f0"
GL_BLUE    = "#60a5fa"
GL_PURPLE  = "#a78bfa"
GL_GREEN   = "#34d399"
GL_AMBER   = "#fbbf24"
GL_PINK    = "#fb7185"
GL_CYAN    = "#22d3ee"
GL_MUTED   = "#64748b"

GLASS_THEME = Theme(
    name="glass",
    primary=GL_BLUE,
    secondary=GL_PURPLE,
    accent=GL_CYAN,
    foreground=GL_TEXT,
    background=GL_BG,
    surface=GL_SURFACE,
    panel=GL_PANEL,
    warning=GL_AMBER,
    error=GL_PINK,
    success=GL_GREEN,
    dark=True,
    variables={
        "footer-key-foreground": GL_BLUE,
        "tool":          GL_AMBER,
        "skill":         GL_PURPLE,
        "surface-alpha": "72%",
    },
)

# ---------------------------------------------------------------------------
# Claude — matches Claude Code's burnt-orange-on-dark palette
# ---------------------------------------------------------------------------

CC_BG      = "#1a1108"   # near-black warm dark
CC_SURFACE = "#221a0c"   # dark warm surface
CC_PANEL   = "#2b200e"   # dark warm panel
CC_TEXT    = "#e8d5b7"   # warm cream foreground
CC_ORANGE  = "#D4622A"   # burnt orange — border, accent
CC_AMBER   = "#E07820"   # brighter amber — warnings / tips
CC_GREEN   = "#8BC060"   # muted green — success
CC_RED     = "#E05050"   # error red
CC_MUTED   = "#7d6652"   # muted brown

CLAUDE_THEME = Theme(
    name="claude",
    primary=CC_ORANGE,
    secondary=CC_AMBER,
    accent=CC_ORANGE,
    foreground=CC_TEXT,
    background=CC_BG,
    surface=CC_SURFACE,
    panel=CC_PANEL,
    warning=CC_AMBER,
    error=CC_RED,
    success=CC_GREEN,
    dark=True,
    variables={
        "footer-key-foreground": CC_AMBER,
        "tool":          CC_AMBER,
        "skill":         CC_ORANGE,
        "surface-alpha": "95%",
    },
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_THEMES = [LANGCHAIN_THEME, GITHUB_DARK_THEME, GLASS_THEME, CLAUDE_THEME]
