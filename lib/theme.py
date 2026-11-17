"""Ink / Mint: shared terminal palette and reversible tmux chrome."""
PALETTE = {
    "bg": "#11151f", "panel": "#1b2332", "selected": "#283b4a", "hover": "#223140",
    "fg": "#e4eaf4", "muted": "#96a6bd", "line": "#38465d",
    "accent": "#9ce0c0", "amber": "#f2c38b", "rose": "#f2a7b5", "blue": "#a8c9f5",
}


def fg(role):
    c = PALETTE.get(role, role).lstrip("#")
    return "\x1b[38;2;%d;%d;%dm" % tuple(int(c[i:i+2], 16) for i in (0, 2, 4))


def bg(role):
    c = PALETTE.get(role, role).lstrip("#")
    return "\x1b[48;2;%d;%d;%dm" % tuple(int(c[i:i+2], 16) for i in (0, 2, 4))


# (window-scoped, name, value). Exact prior values are saved by the installer.
TMUX_OPTIONS = [
    (False, "status-style", "fg=#96a6bd,bg=#11151f"),
    (False, "message-style", "fg=#e4eaf4,bg=#283b4a"),
    (False, "message-command-style", "fg=#e4eaf4,bg=#283b4a"),
    (False, "pane-border-style", "fg=#38465d"),
    (False, "pane-active-border-style", "fg=#9ce0c0"),
    (False, "status-left-length", "40"),
    (False, "status-right-length", "60"),
    (False, "status-left", "#[fg=#11151f,bg=#9ce0c0,bold] #S #[fg=#9ce0c0,bg=#11151f]#[default] "),
    (False, "status-right", "#[fg=#283b4a,bg=#11151f]#[fg=#e4eaf4,bg=#283b4a] %H:%M #[default]"),
    (True, "window-status-format", "#[fg=#96a6bd] #I #W "),
    (True, "window-status-current-format", "#[fg=#11151f,bg=#a8c9f5,bold] #I #W #[fg=#a8c9f5,bg=#11151f]#[default]"),
    (True, "window-status-separator", " "),
]
