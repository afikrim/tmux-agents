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
from . import theme, activity

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


def primary_pane(window):
    panes = window.get("panes", [])
    return next((p for p in panes if p.get("agent") and p.get("current")),
                next((p for p in panes if p.get("agent")),
                     next((p for p in panes if p.get("current")), next(iter(panes), {}))))


def project_summary(session):
    windows = [w for w in session.get("windows", []) if w.get("panes")]
    statuses = [primary_pane(w).get("status", "unknown") for w in windows]
    first = next(((w, p) for w in windows for p in w["panes"] if p.get("unread")), None)
    target = None
    if first:
        window, pane = first
        target = {"kind":"pane", "ref":pane["id"], "sess_id":session["id"],
                  "win_id":window["id"], "target":("pane",pane["id"])}
    return {"running":statuses.count("running"),
            "idle":sum(s in ("idle","needs-input","needs-approval","question","done") for s in statuses),
            "unread":sum(any(p.get("unread") for p in w["panes"]) for w in windows),
            "unread_target":target}


def build_rows(sessions, project_id=None):
    """Projects first, then one active-terminal entry per selected project window."""
    project = next((s for s in sessions if s["id"] == project_id),
                   next((s for s in sessions if s.get("attached")), next(iter(sessions), None)))
    rows = [{"kind": "session", "depth": 0, "ref": s["id"], "sess_id": s["id"],
             "name": s["name"], "label": san(s["name"], 0),
             "current": s.get("attached", False), "project_selected": s is project,
             "target": ("session", s["id"]), **project_summary(s)} for s in sessions]
    rows.append({"kind": "section", "depth": 0, "ref": "section:agents", "label": "Agents",
                 "name": project["name"] if project else "", "target": ("section", "")})
    for window in project.get("windows", []) if project else []:
        panes = window.get("panes", [])
        if not panes:
            continue
        pane = primary_pane(window)
        name = pane.get("display_name") or pane.get("agent") or "shell"
        if name == "shell" and not pane.get("custom_name"):
            title = window.get("name", "")
            name = title if title.lower() not in ("", "sh", "bash", "zsh", "fish", "dash") else "Terminal"
        tag, _ = STATUS_STYLE.get(pane.get("status", "unknown"), STATUS_STYLE["unknown"])
        source = SOURCE_MARK.get(pane.get("source", ""), "?")
        rows.append({"kind": "pane", "depth": 1, "ref": pane["id"],
                     "sess_id": project["id"], "win_id": window["id"],
                     "window_name": window.get("name", ""), "window_index": window.get("index", ""),
                     "focus_pane": pane["id"], "target": ("pane", pane["id"]),
                     "name": name, "label": san("%s [%s%s]" % (name, tag, source), 0),
                     "current": project.get("attached", False) and window.get("active", False),
                     "custom_name": pane.get("custom_name", ""), "agent": pane.get("agent", ""),
                     "caption": pane.get("caption") or activity.caption(pane.get("status", "unknown")),
                     "unread": any(p.get("unread") for p in panes),
                     "status": pane.get("status", "unknown"), "source": pane.get("source", "")})
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


def row_line(row, selected, width, focused=True, scroll_elapsed=0.0,
             hovered=False, hover_elapsed=0.0):
    kind = row["kind"]
    background = ("selected" if selected and focused else "hover" if hovered
                  else "panel" if row.get("current") else "bg")
    marker = ">" if selected and (focused or row.get("current")) else " "
    def name_text(name):
        space = row_name_width(row, width)
        if hovered:
            return scroll_text(name, space, hover_elapsed)
        return scroll_text(name, space, scroll_elapsed) if selected and focused else fit(name, space)
    if kind in ("spacer", "section"):
        return styled(" " + row.get("label", ""), width, "accent" if kind == "section" else "fg")
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
    return (RESET + theme.bg(background) + theme.fg("muted" if not row.get("agent") and name in ("shell", "Terminal") else "fg")
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


def selection_index(rows, previous_ref=None, focused=True):
    """Keep keyboard browsing stable; otherwise follow the live active window."""
    if focused and previous_ref is not None:
        found = next((i for i, row in enumerate(rows)
                      if row.get("ref") == previous_ref and row["kind"] in ("session", "pane")), None)
        if found is not None:
            return found
    return next((i for i, row in enumerate(rows) if row["kind"] == "pane" and row.get("current")),
                next((i for i, row in enumerate(rows) if row["kind"] == "session" and row.get("current")),
                     next((i for i, row in enumerate(rows) if row["kind"] in ("session", "pane")), 0)))


def hover_target(rows, hits, width, key):
    """Only unpressed pointer motion can hover a rendered, selectable row."""
    if not isinstance(key, tuple) or len(key) != 5:
        return None
    kind, button, x, y, released = key
    if kind != "mouse" or released or (button & ~28) != 35 or not 1 <= x <= width:
        return None
    index = hits.get(y)
    if index is not None and 0 <= index < len(rows):
        row = rows[index]
        if row["kind"] in ("session", "pane"):
            return row["ref"]
    return None


class HitMap(dict):
    """Rendered row indices plus cell ranges for the unread count action."""
    def __init__(self):
        super().__init__()
        self.unread = {}


def click_target(rows, hits, x, y):
    index = hits.get(y)
    if index is None or not 0 <= index < len(rows):
        return None
    unread = getattr(hits, "unread", {}).get(y)
    if unread and unread[0] <= x <= unread[1]:
        return rows[index].get("unread_target")
    return rows[index]


def summary_line(row, width, background):
    """Colour each count and return the exact visible unread click range."""
    count = lambda value: str(value) if value < 100 else "99+"
    segments = [(count(row.get("running",0))+" run", "accent"),
                (count(row.get("idle",0))+" idle", "muted"),
                (count(row.get("unread",0))+" unread", "amber" if row.get("unread") else "muted")]
    text, used, target = RESET + theme.bg(background) + "  ", 2, None
    for i, (label, color) in enumerate(segments):
        if i:
            space = min(2,max(0,width-used))
            text += " "*space
            used += space
        visible = fit(label, max(0, width-used), False)
        if i == 2 and visible:
            target = (used+1, used+cell_width(visible))
        text += theme.fg(color) + ("\x1b[4m" if i == 2 and row.get("unread") else "") + visible + "\x1b[24m"
        used += cell_width(visible)
    # fit the whole plain layout first on very narrow terminals.
    if width < 8:
        return styled("  %d unread" % row.get("unread",0),width,"amber",background), (3,width) if width>=3 else None
    return text + " " * max(0,width-used) + RESET + BASE, target


def frame(rows, sessions, cursor, top, width, height, focused=True, scroll_elapsed=0.0, return_hits=False,
          hover_ref=None, hover_elapsed=0.0):
    """Two independently scrolled sections with an explicit mouse hit map."""
    width, height = max(1, width), max(1, height)
    offsets = dict(top) if isinstance(top, dict) else {"projects": 0, "agents": 0}
    projects = [i for i, row in enumerate(rows) if row["kind"] == "session"]
    agents = [i for i, row in enumerate(rows) if row["kind"] == "pane"]
    section = next((row for row in rows if row["kind"] == "section"), {})
    lines, hits = [], HitMap()
    def add(text):
        if len(lines) < height:
            lines.append(text)
    def group(indices, count, key, stride):
        count = max(0, count)
        start = offsets.get(key, 0)
        selected = next((pos for pos, index in enumerate(indices) if index == cursor), None)
        if selected is None and key == "projects":
            selected = next((pos for pos, index in enumerate(indices) if rows[index].get("project_selected")), None)
        if selected is not None:
            if selected < start:
                start = selected
            elif selected >= start + count:
                start = max(0, selected - count + 1)
        start = max(0, min(start, max(0, len(indices) - count)))
        offsets[key] = start
        for index in indices[start:start + count]:
            hits[len(lines) + 1] = index
            row = rows[index]
            background = ("selected" if index == cursor and focused else "hover" if row["ref"] == hover_ref
                          else "panel" if row.get("current") else "bg")
            add(row_line(rows[index], index == cursor, width, focused, scroll_elapsed,
                         rows[index]["ref"] == hover_ref, hover_elapsed))
            if stride >= 2:
                hits[len(lines)+1] = index
                if key == "projects":
                    summary, span = summary_line(row,width,background)
                    if span:
                        hits.unread[len(lines)+1] = span
                    add(summary)
                else:
                    status = row.get("status", "unknown")
                    color = "amber" if status in ("question","needs-input") else "rose" if status=="needs-approval" else "muted"
                    add(styled("     "+row.get("caption", ""),width,color,background))
            if stride >= 3:
                add(styled("",width))
    # Keep a compact agents block against the bottom. Its heading/divider
    # count toward the half-height cap; tiny terminals retain one useful row.
    agent_limit = min(max(0, height - 1), max(3, height // 2))
    agent_stride = 2 if height >= 8 else 1
    agent_count = min(max(1, len(agents)), max(0, (agent_limit - 2)//agent_stride))
    agent_height = 2 + agent_count*agent_stride if agent_count else 0
    add(styled(" Projects / %d" % len(projects), width, "accent", bold=True))
    available = max(0,height-agent_height-1)
    project_stride = 3 if available>=3 else 2 if available>=2 else 1
    project_count = min(len(projects), available//project_stride)
    group(projects, project_count, "projects", project_stride)
    while len(lines) < height - agent_height:
        add(styled("", width))
    if agent_height:
        add(styled(" " + "─" * max(0, width - 2), width, "line"))
        add(styled(" Agents / " + section.get("name", ""), width, "accent", bold=True))
        if agents:
            group(agents, agent_count, "agents", agent_stride)
        else:
            add(styled(" No terminals in this project", width, "muted"))
    lines.extend(styled("", width) for _ in range(height - len(lines)))
    result = (lines, offsets, 0, height)
    return result + (hits,) if return_hits else result


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


class InputDecoder:
    """Keep partial terminal escape packets out of keyboard shortcuts."""
    def __init__(self):
        self.pending = ""

    def feed(self, data="", flush=False):
        data, self.pending = self.pending + data, ""
        fragment = re.search(r"\x1b(?:\[(?:<[0-9;]*|[0-9;]*))?$", data)
        if fragment:
            tail, data = data[fragment.start():], data[:fragment.start()]
            if not flush:
                self.pending = tail
            elif tail == "\x1b":
                data += tail
            # Drop timed-out partial CSI, never interpret its payload as keys.
        return parse_input(data)


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
