"""Interactive sidebar UI: pure row model + key/mouse parsing (testable) and a
raw-terminal runner (alternate screen, SGR mouse, resize-safe).

Rows: sessions -> windows -> agent panes, addressed by STABLE ids
(session_id $N, window_id @N, pane_id %N) so names with spaces/colons and
window renumbering never break navigation.
"""
import re
import os
import socket
import unicodedata
from . import theme

# ANSI: terminal-inheriting (default fg/bg) + standard theme colors.
RESET = "\x1b[0m"
BASE = theme.bg("bg") + theme.fg("fg")
REV = "\x1b[7m"
BOLD = "\x1b[1m"
DIM = theme.fg("muted")
RED = theme.fg("rose")
GREEN = theme.fg("accent")
YELLOW = theme.fg("amber")
CYAN = theme.fg("blue")

STATUS_STYLE = {
    "running": ("RUNNING", GREEN),
    "needs-input": ("INPUT", YELLOW),
    "needs-approval": ("APPROVAL", RED),
    "question": ("QUESTION", YELLOW),
    "done": ("DONE", CYAN),
    "idle": ("IDLE", ""),
    "unknown": ("UNKNOWN", DIM),
}
# Source marks: = explicit event, # strict screen widget, ~ inferred/observed.
SOURCE_MARK = {"event": "=", "screen": "#", "inferred": "~", "process": "",
               "": "?"}


def san(text, width):
    """Strip control chars (keep plain readable labels); never emit raw \r\n."""
    text = "".join(c for c in str(text) if c == "\t" or not unicodedata.category(c).startswith("C"))
    text = text.replace("\t", " ")
    return text


def pane_name(custom, title, agent, cwd="", program=""):
    """Prefer a local nickname, then the agent's live terminal title.

    Titles are metadata published by the CLI (OSC), never scraped chat text.
    Shell directory/host titles are deliberately not conversation names.
    """
    if san(custom, 0).strip():
        return san(custom, 0).strip()[:120]
    if agent:
        name = san(title, 0).strip()
        name = re.sub(r"^[\u2800-\u28ff]\s*", "", name).strip()
        suffix = " | " + os.path.basename(cwd.rstrip("/")) if cwd else ""
        if suffix and name.endswith(suffix):
            name = name[:-len(suffix)].strip()
        generic = {agent.lower(), "claude code", "codex cli", "opencode", "agents",
                   socket.gethostname().lower(), socket.gethostname().split(".")[0].lower()}
        if name and name.lower() not in generic and name not in (cwd, os.path.basename(cwd), os.path.expanduser("~")):
            return name[:120]
    if agent:
        return agent
    command = os.path.basename(san(program, 0).strip()).lstrip("-")
    if command and command.lower() not in {"sh", "bash", "zsh", "fish", "dash", "ash", "ksh", "csh", "tcsh", "nu", "nushell"}:
        return {"nvim": "Neovim", "vim": "Vim"}.get(command.lower(), command)[:120]
    return "shell"


def build_rows(sessions):
    """sessions: [{id,name,attached,windows:[{id,index,name,active,panes:[{id,agent,status,source,current}]}]}]
    Returns [Row...]; Row = dict(kind, label, target, depth, ref)."""
    rows = []
    for s in sessions:
        if rows:
            rows.append({"kind": "spacer", "depth": 0, "ref": "gap:" + s["id"],
                         "label": "", "target": ("spacer", "")})
        cur = "*" if s.get("attached") else " "
        rows.append({"kind": "session", "depth": 0, "ref": s["id"],
                     "sess_id": s["id"], "name": s["name"], "current": s.get("attached", False),
                     "label": san("%s%s" % (cur, s["name"]), 0),
                     "target": ("session", s["id"])})
        for w in s.get("windows", []):
            wcur = ">" if w.get("active") else " "
            rows.append({"kind": "window", "depth": 1, "ref": w["id"],
                         "sess_id": s["id"], "name": w.get("name", ""), "index": str(w.get("index", "")),
                         "current": s.get("attached", False) and w.get("active", False),
                         "focus_pane": next((p["id"] for p in w.get("panes", []) if p.get("current")),
                                            next(iter(w.get("panes", [])), {}).get("id", "")),
                         "label": san("%s%s:%s" % (wcur, w.get("index"), w.get("name")), 0),
                         "target": ("window", w["id"])})
            for p in w.get("panes", []):
                tag, _c = STATUS_STYLE.get(p.get("status", "unknown"),
                                           STATUS_STYLE["unknown"])
                sm = SOURCE_MARK.get(p.get("source", ""), "?")
                sel = "+" if p.get("selected") else " "
                cur2 = "<" if p.get("current") else " "
                rows.append({
                    "kind": "pane", "depth": 2, "ref": p["id"],
                    "win_id": w["id"], "sess_id": s["id"],
                    "label": san("%s%s [%s%s]%s" % (
                        sel, p.get("display_name") or p.get("agent") or "shell", tag, sm, cur2), 0),
                    "target": ("pane", p["id"]),
                    "name": p.get("display_name") or p.get("agent") or "shell",
                    "custom_name": p.get("custom_name", ""), "agent": p.get("agent", ""), "current": p.get("current") and s.get("attached", False),
                    "status": p.get("status", "unknown"),
                    "source": p.get("source", "")})
    return rows


def cell_width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in text)


def fit(text, width, pad=True):
    """Clip by terminal cells, including CJK and combining marks."""
    text = san(text, width)
    out, used = "", 0
    for c in text:
        n = cell_width(c)
        if used + n > max(0, width):
            break
        out += c
        used += n
    return out + (" " * max(0, width - used) if pad else "")


def styled(text, width, color="fg", background="bg", bold=False):
    return (RESET + theme.bg(background) + theme.fg(color) + (BOLD if bold else "")
            + fit(text, width) + RESET + BASE)


def scroll_text(text, width, elapsed):
    """A cell-aligned marquee with a pause at each end; no split wide glyphs."""
    text = san(text, 0)
    overflow = cell_width(text) - width
    if width <= 0 or overflow <= 0:
        return fit(text, max(0, width))
    travel = overflow / 5.0
    phase = max(0.0, elapsed) % (2 * travel + 2.5)
    if phase < 1.0:
        offset = 0
    elif phase < 1.0 + travel:
        offset = min(overflow, int((phase - 1.0) * 5))
    elif phase < 2.5 + travel:
        offset = overflow
    else:
        offset = max(0, overflow - int((phase - 2.5 - travel) * 5))
    # Group combining marks with their base character before taking a slice.
    clusters = []
    for char in text:
        if unicodedata.combining(char):
            if clusters:
                clusters[-1] += char
        else:
            clusters.append(char)
    position, out = 0, ""
    for cluster in clusters:
        size = cell_width(cluster)
        if position < offset < position + size:
            out += " " * (position + size - offset)
        elif position >= offset:
            out += cluster
        position += size
    return fit(out, width)


def row_name_width(row, width):
    if row["kind"] == "session":
        return max(0, width - (7 if row.get("current") else 0) - 5)
    if row["kind"] == "window":
        return max(0, width - cell_width("   " + row.get("index", "") + "  "))
    tag, _ = STATUS_STYLE.get(row.get("status"), STATUS_STYLE["unknown"])
    source = SOURCE_MARK.get(row.get("source", ""), "?")
    return max(0, width - cell_width(tag.title() + source) - 7)


def row_line(row, selected, width, focused=True, scroll_elapsed=0.0):
    kind = row["kind"]
    background = "selected" if selected and focused else "panel" if row.get("current") and kind == "window" else "bg"
    marker = ">" if selected and focused else " "
    def name_text(name):
        space = row_name_width(row, width)
        return scroll_text(name, space, scroll_elapsed) if selected and focused else fit(name, space)
    if kind == "spacer":
        return styled("", width)
    if kind == "session":
        name = row.get("name", row["label"]).strip()
        right = "current" if row.get("current") else ""
        prefix = marker + " "
        left = name_text(name)
        return styled(prefix + left + " " * max(1, width - 3 - cell_width(left) - len(right)) + right + " ",
                      width, "accent" if row.get("current") else "fg", background, True)
    if kind == "window":
        name = row.get("name", row["label"])
        return styled(" " + marker + " " + row.get("index", "") + "  " + name_text(name),
                      width, "fg" if row.get("current") else "muted", background)
    tag, color = STATUS_STYLE.get(row.get("status"), STATUS_STYLE["unknown"])
    source = SOURCE_MARK.get(row.get("source", ""), "?")
    name = row.get("name", "shell")
    # Pane IDs stay internal navigation targets, outside the visible label.
    left = "   " + marker + " " + name_text(name)
    right = tag.title() + source
    available = max(0, width - cell_width(right) - 2)
    return (RESET + theme.bg(background) + theme.fg("muted" if not row.get("agent") and name == "shell" else "fg")
            + fit(left, available) + color + fit(" " + right + " ", width - available)
            + RESET + BASE)


def render_rows(rows, cursor, top, height, width, focused=True, scroll_elapsed=0.0):
    height, width = max(0, height), max(1, width)
    cursor = max(0, min(cursor, len(rows) - 1)) if rows else 0
    if cursor < top:
        top = cursor
    if height and cursor >= top + height:
        top = cursor - height + 1
    return [row_line(rows[i], i == cursor, width, focused, scroll_elapsed)
            for i in range(top, min(top + height, len(rows)))], top


def header_lines(sessions, width, height=24):
    title = "tmux agents"
    agents = sum(bool(p.get("agent")) for s in sessions for w in s.get("windows", []) for p in w.get("panes", []))
    if height < 10:
        return [styled(" " + title, width, "accent", bold=True)]
    return [styled("", width), styled(" " + title, width, "accent", bold=True),
            styled(" %d sessions / %d agents" % (len(sessions), agents), width, "muted"),
            styled("", width)]


def footer_lines(width, n_rows=0):
    return [styled(" " + "─" * max(0, width - 2), width, "line"),
            styled(" enter open    x new agent", width, "muted"),
            styled(" r rename   j/k move   q back", width, "muted"),
            styled(" = native  # screen  ~ observed", width, "muted")]


def frame(rows, sessions, cursor, top, width, height, focused=True, scroll_elapsed=0.0):
    """A complete, fixed-height frame: footer remains anchored, never scrolls."""
    width, height = max(1, width), max(1, height)
    header = header_lines(sessions, width, height)
    footer = footer_lines(width, len(rows)) if height >= 10 else [styled(" enter open / q back", width, "muted")]
    body_height = max(0, height - len(header) - len(footer))
    body, top = render_rows(rows, cursor, top, body_height, width, focused, scroll_elapsed)
    body += [styled("", width)] * (body_height - len(body))
    lines = (header + body + footer)[:height]
    return lines, top, len(header), body_height


def move_cursor(n_rows, cursor, key):
    if key in ("j", "down"):
        return min(n_rows - 1, cursor + 1)
    if key in ("k", "up"):
        return max(0, cursor - 1)
    if key in ("g", "home"):
        return 0
    if key in ("G", "end"):
        return max(0, n_rows - 1)
    if key == "pagedown":
        return min(n_rows - 1, cursor + 10)
    if key == "pageup":
        return max(0, cursor - 10)
    return cursor


def move_selection(rows, cursor, key):
    """Window names are context labels, not keyboard navigation stops."""
    navigation = ("j", "k", "up", "down", "g", "G", "home", "end", "pageup", "pagedown")
    if not rows or key not in navigation:
        return cursor if rows else 0
    selectable = lambda i: rows[i]["kind"] in ("session", "pane")
    position = move_cursor(len(rows), cursor, key)
    direction = -1 if key in ("up", "k", "pageup", "G", "end") else 1
    while 0 <= position < len(rows) and not selectable(position):
        position += direction
    if 0 <= position < len(rows):
        return position
    if 0 <= cursor < len(rows) and selectable(cursor):
        return cursor
    return next((i for i in range(len(rows)) if selectable(i)), 0)


MOUSE_RE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([mM])")


def parse_input(data):
    """Parse raw bytes -> list of keys. Keys: chars, up/down/left/right,
    home/end/pagedown/pageup/enter/esc/tab, ('mouse', button, x, y, release)."""
    keys = []
    i, n = 0, len(data)
    while i < n:
        m = MOUSE_RE.match(data, i)
        if m:
            b, x, y, t = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
            keys.append(("mouse", b, x, y, t == "m"))
            i = m.end()
            continue
        c = data[i]
        if c == "\x1b":
            rest = data[i:]
            if rest.startswith("\x1b[A"):
                keys.append("up"); i += 3; continue
            if rest.startswith("\x1b[B"):
                keys.append("down"); i += 3; continue
            if rest.startswith("\x1b[C"):
                keys.append("right"); i += 3; continue
            if rest.startswith("\x1b[D"):
                keys.append("left"); i += 3; continue
            if rest.startswith("\x1b[H") or rest.startswith("\x1b[1~"):
                keys.append("home"); i += 3 if rest[2] == "H" else 4; continue
            if rest.startswith("\x1b[F") or rest.startswith("\x1b[4~"):
                keys.append("end"); i += 3 if rest[2] == "F" else 4; continue
            if rest.startswith("\x1b[5~"):
                keys.append("pageup"); i += 4; continue
            if rest.startswith("\x1b[6~"):
                keys.append("pagedown"); i += 4; continue
            if rest == "\x1b":
                keys.append("esc"); i += 1; continue
            i += 1
            continue
        if c == "\r" or c == "\n":
            keys.append("enter")
        elif c == "\t":
            keys.append("tab")
        elif c == "\x7f":
            keys.append("backspace")
        else:
            keys.append(c)
        i += 1
    return keys


def activation_tmux_args(row):
    """Row -> tmux argv suffix to jump there (stable IDs). Pure (testable)."""
    kind, ref = row["target"]
    if kind == "pane":
        return ["select-pane", "-t", ref]
    if kind == "window":
        return ["select-window", "-t", ref]
    return ["switch-client", "-t", ref]
