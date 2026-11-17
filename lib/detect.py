"""Agent process identity: pid + start time, so events bind to the CURRENT
agent invocation and never leak across restarts in the same shell."""
import os
import re
import shlex
import subprocess

AGENT_PROGRAMS = ("codex", "claude", "opencode")
WRAPPER_BASENAMES = {"fish", "bash", "zsh", "sh", "rtk", "bun", "node",
                     "python", "python3", "env", "sudo"}
AGENT_PATH_RES = [
    re.compile(r"(^|/)codex(\s|$|-)"),
    re.compile(r"(^|/)claude(\s|$|-)"),
    re.compile(r"(^|/)opencode(\s|$)"),
    re.compile(r"codex-app-server|codex-code-mode-host"),
]
INFRA_RES = [
    re.compile(r"app-server daemon"),
    re.compile(r"code-mode-host"),
    re.compile(r"exec-server"),
    re.compile(r"opencode-mcp/server\.mjs"),
]


def _basename(prog):
    return os.path.basename(prog).lower() if prog else ""


def process_table():
    try:
        p = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="],
                           capture_output=True, text=True, timeout=10, env=dict(os.environ, LC_ALL="C"))
    except Exception:
        return {}
    table = {}
    if p.returncode != 0:
        return table
    for line in p.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        table[pid] = (ppid, parts[2] if len(parts) > 2 else "")
    return table


def start_times(pids):
    """{pid: start-epoch} via one ps call; missing pids omitted."""
    pids = [p for p in pids if p]
    if not pids:
        return {}
    try:
        p = subprocess.run(["ps", "-o", "pid=,lstart=", "-p", ",".join(map(str, pids))],
                           capture_output=True, text=True, timeout=10, env=dict(os.environ, LC_ALL="C"))
    except Exception:
        return {}
    import datetime
    out = {}
    if p.returncode != 0:
        return out
    for line in p.stdout.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*\S)\s*$", line)
        if not m:
            continue
        try:
            # macOS lstart: 'Mon Nov 16 21:30:00 2026'
            dt = datetime.datetime.strptime(m.group(2), "%a %b %d %H:%M:%S %Y")
            out[int(m.group(1))] = dt.timestamp()
        except (ValueError, OverflowError):
            continue
    return out


def classify_command(cmd):
    if not cmd or any(rx.search(cmd.lower()) for rx in INFRA_RES):
        return None
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return None
    if not argv:
        return None
    base = _basename(argv[0])
    name = base.split(".")[0]
    if name in AGENT_PROGRAMS:
        if len(argv) > 1 and argv[1] in ("app-server", "exec-server", "mcp", "features",
                                        "serve", "service", "plugin", "debug", "--help", "--version"):
            return None
        return name
    if base == "rtk":
        rest = argv[2:] if len(argv) > 1 and argv[1] in ("proxy", "run") else argv[1:]
        return classify_command(shlex.join(rest)) if rest else None
    if base in ("env", "sudo"):
        rest = argv[1:]
        while rest and ("=" in rest[0] or rest[0].startswith("-")):
            rest.pop(0)
        return classify_command(shlex.join(rest)) if rest else None
    if base in ("sh", "bash", "zsh", "fish", "dash"):
        if "-c" in argv:
            idx = argv.index("-c")
            return classify_command(argv[idx + 1]) if len(argv) > idx + 1 else None
        # Linux ps retains the shebang interpreter for executable scripts.
        # Only the immediate script operand is executable, never later args.
        if len(argv) > 1 and not argv[1].startswith("-"):
            return classify_command(shlex.join(argv[1:]))
        return None
    if base in ("node", "bun", "python", "python3"):
        script = next((a for a in argv[1:] if not a.startswith("-")), "")
        script_name = _basename(script).split(".")[0]
        return script_name if script_name in AGENT_PROGRAMS else None
    for name in AGENT_PROGRAMS:
        if base.startswith(name + "-"):
            return name
    return None


def descendants(root_pid, table):
    kids = {}
    for pid, (ppid, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    out, queue, seen = [], [root_pid], {root_pid}
    while queue and len(out) < 512:
        cur = queue.pop(0)
        for child in kids.get(cur, []):
            if child in seen:
                continue
            seen.add(child)
            out.append(child)
            queue.append(child)
    return out


def find_agent(root_pid, table):
    own = table.get(root_pid)
    if own:
        name = classify_command(own[1])
        if name:
            return name, root_pid, own[1]
    for pid in descendants(root_pid, table):
        cmd = table.get(pid, (0, ""))[1]
        name = classify_command(cmd)
        if name:
            return name, pid, cmd
    return None


def is_sidebar_tree(root_pid, table):
    own = table.get(root_pid, (0, ""))[1]
    if "tmux-agents" in own and "sidebar" in own:
        return True
    return any("tmux-agents" in table.get(p, (0, ""))[1] and
               "sidebar" in table.get(p, (0, ""))[1]
               for p in descendants(root_pid, table))


def agent_identity(root_pid, table, starts=None):
    """(name, agent_pid, agent_start) or None."""
    hit = find_agent(root_pid, table)
    if not hit:
        return None
    name, apid, _cmd = hit
    if starts is None:
        starts = start_times([apid])
    return name, apid, starts.get(apid, 0)
