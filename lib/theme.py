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


def tab_padding(tag):
    # Padding modifiers with /arguments/ support expanded widths on tmux 3.3.
    # Count the pane plus its divider; hidden/absent panels and zoom reserve zero.
    return ("#{?window_zoomed_flag,,#{P:#{?" + tag +
            ",#{p/#{e|+:#{pane_width},1}/: },}}}")


# (window-scoped, name, value). Exact prior values are saved by the installer.
TMUX_OPTIONS = [
    (False, "status", "on"),
    (False, "status-position", "top"),
    (False, "status-justify", "left"),
    (False, "status-interval", "1"),
    (False, "status-style", "fg=#96a6bd,bg=#11151f"),
    (False, "status-left-style", "fg=#96a6bd,bg=#11151f"),
    (False, "status-right-style", "fg=#96a6bd,bg=#11151f"),
    (False, "message-style", "fg=#e4eaf4,bg=#283b4a"),
    (False, "message-command-style", "fg=#e4eaf4,bg=#283b4a"),
    (False, "pane-border-style", "fg=#38465d"),
    (False, "pane-active-border-style", "fg=#9ce0c0"),
    (False, "status-left-length", "1000"),
    (False, "status-right-length", "1000"),
    (False, "status-left", tab_padding("@tmux_agents_sidebar")),
    (False, "status-right", tab_padding("@tmux_agents_diff")),
    (True, "window-status-format", "#[fg=#96a6bd,bg=#11151f,nobold] #I #W #[default]"),
    (True, "window-status-current-format", "#[fg=#9ce0c0,bg=#283b4a,bold] #I #W #[default]"),
    (True, "window-status-separator", " "),
]
