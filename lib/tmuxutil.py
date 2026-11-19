"""Socket-aware tmux helpers + namespaced state paths (stdlib only).

Every tmux invocation inherits the user's socket identity: explicit
--socket/--socket-path flags win, else $TMUX (pane's socket), else default.
All on-disk state is namespaced by socket id so an isolated test server can
never touch production state. $TMUX_AGENTS_STATE_DIR overrides the root
(tests use an isolated HOME).

Namespace rule: every spelling of the SAME server (-L name, -S path, or an
inherited $TMUX value) canonicalizes to the SAME sid via one
`display-message -p '#{socket_path}'` query (cached per process). Bare `tmux`
calls elsewhere must use the resolved prefix so hooks/daemons/sidebars never
touch the wrong server.
"""
import hashlib
import os
import re
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_sid_cache = {}


def state_root():
    root = os.environ.get("TMUX_AGENTS_STATE_DIR")
    if root:
        return os.path.abspath(os.path.expanduser(root))
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "tmux-agents")


def tmux_conf_path():
    if os.environ.get("TMUX_AGENTS_TMUX_CONF"):
        return os.environ["TMUX_AGENTS_TMUX_CONF"]
    classic = os.path.expanduser("~/.tmux.conf")
    xdg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "tmux/tmux.conf")
    return xdg if os.path.exists(xdg) and not os.path.exists(classic) else classic


def claude_settings_path():
    return os.environ.get("TMUX_AGENTS_CLAUDE_SETTINGS") or os.path.expanduser("~/.claude/settings.json")


def codex_hooks_path():
    return os.environ.get("TMUX_AGENTS_CODEX_HOOKS") or os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "hooks.json")


def opencode_config_path():
    return os.environ.get("TMUX_AGENTS_OPENCODE_JSONC") or os.path.join(os.environ.get("OPENCODE_CONFIG_DIR") or os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "opencode"), "cli.json")


def _raw_prefix(explicit_name="", explicit_path=""):
    if explicit_path:
        return ["-S", explicit_path]
    if explicit_name:
        return ["-L", explicit_name]
    return []


def canonical_socket_path(prefix):
    """Ask the server for its own socket path; '' when unreachable."""
    try:
        p = subprocess.run(["tmux", *prefix, "display-message", "-p",
                            "#{socket_path}"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    if p.returncode != 0:
        return ""
    return p.stdout.strip().splitlines()[0].strip() if p.stdout.strip() else ""


def resolve_socket(explicit_name="", explicit_path=""):
    """Return (tmux_argv_prefix, socket_id).

    $TMUX looks like '/private/tmp/tmux-502/default,123,0': the FIRST field
    is the socket path (never the trailing pane index).
    """
    prefix = _raw_prefix(explicit_name, explicit_path)
    if not prefix:
        tmux_env = os.environ.get("TMUX", "")
        if "," in tmux_env:
            path = tmux_env.split(",", 1)[0].strip()
            if path:
                prefix = ["-S", path]
    key = tuple(prefix)
    if key in _sid_cache:
        return list(prefix), _sid_cache[key]
    canon = canonical_socket_path(prefix)
    if canon:
        sid = "S-" + hashlib.sha1(canon.encode()).hexdigest()[:16]
    elif len(prefix) == 2 and prefix[0] == "-L":
        sid = "L-" + re.sub(r"[^A-Za-z0-9_.-]+", "_", prefix[1])[:48]
    elif len(prefix) == 2 and prefix[0] == "-S":
        sid = "S-" + hashlib.sha1(prefix[1].encode()).hexdigest()[:16]
    else:
        sid = "default"
    _sid_cache[key] = sid
    return list(prefix), sid


def socket_id_of(prefix):
    _, sid = resolve_socket(
        explicit_name=prefix[1] if len(prefix) == 2 and prefix[0] == "-L" else "",
        explicit_path=prefix[1] if len(prefix) == 2 and prefix[0] == '-S' else "")
    return sid


def cli_socket_flags(prefix):
    """Rebuild our own CLI flags from a tmux prefix (for run-shell strings)."""
    if len(prefix) == 2 and prefix[0] == "-L":
        return ["--socket", prefix[1]]
    if len(prefix) == 2 and prefix[0] == "-S":
        return ["--socket-path", prefix[1]]
    return []


def state_paths(prefix):
    sid = socket_id_of(prefix)
    root = os.path.join(state_root(), sid)
    return {
        "dir": root,
        "sid": sid,
        "state": os.path.join(root, "state.json"),
        "events": os.path.join(root, "events.jsonl"),
        "daemon_pid": os.path.join(root, "daemon.pid"),
        "daemon_lock": os.path.join(root, "daemon.lock"),
        "ensure_lock": os.path.join(root, "ensure.lock"),
        "install": os.path.join(root, "install.json"),
    }


def run_tmux(prefix, *args, timeout=10):
    p = subprocess.run(["tmux", *prefix, *args], capture_output=True,
                       text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def tmux_lines(prefix, *args, timeout=10):
    rc, out, _ = run_tmux(prefix, *args, timeout=timeout)
    if rc != 0:
        return []
    return [l for l in out.splitlines() if l.strip() != ""]
