"""Native lifecycle adapters: Claude hooks, Codex hooks, OpenCode plugin.

Mirrors Corgi's proven shape (shared {hooks:{Event:[{matcher,hooks:[...]}]}}
settings file for Claude and Codex; data.on TUI plugin for OpenCode v2),
reimplemented in stdlib Python. License notice retained in LICENSE-NOTICE.

Event mapping is conservative: unrecognized payloads are DROPPED, never
fabricated. Stop while the process lives means the turn ended and is idle;
done is derived from process exit, never from a hook alone. Only explicit
questions and decision requests require user input.
"""
import copy
import json
import os
import re

from .tmuxutil import (claude_settings_path, codex_hooks_path,
                       opencode_config_path)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_SCRIPT = os.path.join(BASE, "hooks", "agent-event")

CLAUDE_EVENTS = [
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PermissionRequest", "Stop", "SessionEnd", "Notification",
    'PostToolUseFailure','SubagentStart','SubagentStop','PreCompact','PostCompact',
]
CODEX_EVENTS = [
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PermissionRequest", "Stop", "SessionEnd", "PreCompact",
    "Interrupt", "PostCompact",
    'SubagentStart','SubagentStop','PreModelRequest','PostModelRequest',
]
# OpenCode v2 data.on event -> hook_event_name fed to hooks/agent-event.
OPENCODE_MAP = {
    "session.created": "SessionStart",
    "session.execution.started": "SessionStart",
    "session.execution.succeeded": "Stop",
    "session.execution.interrupted": "Interrupt",
    "session.execution.failed": "Stop",
    "session.idle": "Notification",
    "permission.asked": "PermissionRequest",
    "permission.replied": "PostToolUse",
    "form.created": "FormCreated",
    "form.replied": "PostToolUse",
}


def hook_command(provider):
    """Shell guard: only report from inside a tmux pane; stdin JSON passes through."""
    return ('if [ -n "${TMUX_PANE:-}" ]; then '
            'python3 "%s" --agent %s; fi' % (HOOK_SCRIPT, provider))


QUESTION_TOOLS_RE = re.compile(
    r"question|askuser|ask_user|request_user|elicit|requestinput|request_input|"
    r"prompt_user|query_user|user_prompt|confirmation|confirm_action", re.I)


def _auto_decided(payload):
    """True when the payload shows the decision was already made elsewhere
    (auto-approval): never assert needs-approval without a live prompt."""
    if not isinstance(payload, dict):
        return False
    if str(payload.get("decision", "")).lower() in ("allow", "approve", "approved"):
        return True
    if str(payload.get("mode", "")).lower() in ("auto", "accept", "allow"):
        return True
    if payload.get("auto_approved") is True or payload.get("autoApproved") is True:
        return True
    return False


def hook_kind(provider, event_name, payload):
    """Map (provider, hook_event_name, payload) -> status kind or '' to drop.

    payload is the parsed stdin JSON (dict). Never invents attention:
    unrecognized payloads are dropped. Generic waiting/completion means idle;
    only question tools and typed input/elicitation requests require input.
    """
    name = (event_name or "").strip()
    if not name:
        return ""
    low = name.lower()
    if not isinstance(payload, dict):
        payload = {}
    if low == "sessionstart":
        return "idle"
    if low in ("userpromptsubmit", "posttooluse",
               "precompact", "postcompact", "sessioncreated",'posttoolusefailure',
               'subagentstart','subagentstop','premodelrequest','postmodelrequest'):
        return "running"
    if low in ('responsestarted','thinkingstarted','retrying'):return 'running'
    if low == "pretooluse":
        tool = str(payload.get("tool_name", "") or payload.get("tool", ""))
        if tool == "ExitPlanMode":
            return "needs-approval"
        if tool and QUESTION_TOOLS_RE.search(tool):
            return "question"
        return "running"
    if low in ("permissionrequest", "permission.asked"):
        if _auto_decided(payload):
            return "running"
        return "needs-approval"
    if low == "formcreated":
        return "question"
    if low in ("stop", "session.idle"):
        return "idle"
    if low == "interrupt":
        return "idle"
    if low == "sessionend":
        return "done"
    if low == "notification":
        ntype = str(payload.get("notification_type", "") or "").lower()
        if ntype in ("permission_prompt", "permissionprompt", "permission"):
            return "needs-approval"
        if ntype in ("idle_prompt", "idleprompt", "idle"):
            return "idle"
        if ntype in ("input_needed", "elicitation", "elicitation_dialog", "question", "question_prompt"):
            return "needs-input"
        text = str(payload.get("message", "") or "")
        if re.search(r"permission|approv", text, re.I):
            return "needs-approval"
        if (not text.strip() or
                re.search(r"input needed|needs input|waiting|idle|elicitation", text, re.I)):
            return "idle"
        return ""
    return ""


# ---- settings-file merge (pure; preserves existing entries) ----

def _ensure_groups(root, event):
    hooks = root.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("InvalidHookSettings")
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise ValueError("InvalidHookSettings")
    return groups


def merge_settings(existing_text, events, command):
    """Return updated settings JSON text, or None if already present."""
    try:
        root = json.loads(existing_text) if existing_text.strip() else {}
    except ValueError:
        raise ValueError("InvalidHookSettings")
    if not isinstance(root, dict):
        raise ValueError("InvalidHookSettings")
    changed = False
    for event in events:
        groups = _ensure_groups(root, event)
        found = any(isinstance(g, dict) and isinstance(g.get("hooks"), list) and
                    any(isinstance(h, dict) and h.get("command") == command
                        for h in g["hooks"])
                    for g in groups)
        if not found:
            groups.append({"hooks": [{"type": "command", "command": command,
                                       "timeout": 5}]})
            changed = True
    if not changed:
        return None
    return json.dumps(root, indent=2) + "\n"


def remove_command(existing_text, command):
    """Return (updated_text, removed_count). Keeps all other handlers."""
    try:
        root = json.loads(existing_text)
    except ValueError:
        raise ValueError("InvalidHookSettings")
    if not isinstance(root, dict):
        return existing_text, 0
    hooks = root.get("hooks")
    if not isinstance(hooks, dict):
        return existing_text, 0
    removed = 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                kept.append(g)
                continue
            hs = [h for h in g["hooks"]
                  if not (isinstance(h, dict) and h.get("command") == command)]
            removed += len(g["hooks"]) - len(hs)
            if hs:
                g2 = dict(g)
                g2["hooks"] = hs
                kept.append(g2)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not removed:
        return existing_text, 0
    return json.dumps(root, indent=2) + "\n", removed


def strip_jsonc(text):
    """Minimal JSONC (// and /* */ comments) stripper for opencode.jsonc."""
    out, i, n = [], 0, len(text)
    instr, esc = False, False
    while i < n:
        c = text[i]
        if instr:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            i += 1
            continue
        if c == '"':
            instr = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


# ---- file locations + integrate/unintegrate ----

# NOTE: opencode.jsonc uses the plural "plugins" key (verified against the
# installed opencode v2 config). Older singular "plugin" stanzas are ALSO
# honored for backward compatibility, but new entries are written plural.


def claude_settings_path():
    from .tmuxutil import claude_settings_path as _p
    return _p()


def codex_hooks_path():
    from .tmuxutil import codex_hooks_path as _p
    return _p()


def opencode_config_path():
    from .tmuxutil import opencode_config_path as _p
    return _p()


def _plugin_list(root, create=False):
    """Return (key, list) for the plugin stanza, preferring 'plugins'."""
    for key in ("plugins", "plugin"):
        v = root.get(key)
        if isinstance(v, dict):
            v = root[key] = [v]
        if isinstance(v, list):
            return key, v
    if create:
        root["plugins"] = []
        return "plugins", root["plugins"]
    return None, None


def _backup(path):
    import shutil
    import time
    if not os.path.exists(path):
        return ""
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = path + ".pre-tmux-agents-" + ts + "-" + str(time.time_ns())[-9:]
    shutil.copy2(path, bak)
    os.chmod(bak, 0o600)
    return bak


def _write_config(path, text):
    """Replace configs atomically, preserving the previous mode."""
    import tempfile
    import stat
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    mode = stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else 0o600
    fd, tmp = tempfile.mkstemp(prefix=".tmux-agents-", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def integrate(provider):
    """Merge our hook into the provider config with backup. Returns (path, changed, backup)."""
    import time
    if provider == "claude":
        path, events = claude_settings_path(), CLAUDE_EVENTS
    elif provider == "codex":
        path, events = codex_hooks_path(), CODEX_EVENTS
    elif provider == "opencode":
        return integrate_opencode()
    else:
        raise ValueError("unknown provider: %s" % provider)
    cmd = hook_command(provider)
    existing = ""
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
    updated = merge_settings(existing, events, cmd)
    if updated is None:
        return path, False, ""
    bak = _backup(path)
    _write_config(path, updated)
    return path, True, bak


def unintegrate(provider):
    """Remove only our hook commands. Returns (path, removed_count)."""
    if provider == "opencode":
        return unintegrate_opencode()
    path = claude_settings_path() if provider == "claude" else codex_hooks_path()
    if not os.path.exists(path):
        return path, 0
    with open(path) as f:
        existing = f.read()
    cmd = hook_command(provider)
    updated, n = remove_command(existing, cmd)
    if n:
        _backup(path)
        _write_config(path, updated)
    return path, n


PLUGIN_DIR = os.path.join(BASE, "plugins", "opencode-tmux-agents")


def integrate_opencode():
    """Point opencode.jsonc at our local TUI plugin (backup first)."""
    path = opencode_config_path()
    raw = ""
    if os.path.exists(path):
        with open(path) as f:
            raw = f.read()
    try:
        root = json.loads(strip_jsonc(raw)) if raw.strip() else {}
    except ValueError:
        raise ValueError("cannot parse opencode.jsonc; refusing to modify")
    if not isinstance(root, dict):
        raise ValueError("cannot parse opencode.jsonc; refusing to modify")
    plugins_key, plugins = _plugin_list(root, create=True)
    if plugins_key is None or not isinstance(plugins, list):
        raise ValueError("unexpected plugin stanza; refusing to modify")
    entry = {"package": PLUGIN_DIR}
    if any(isinstance(p, dict) and p.get("package") == PLUGIN_DIR for p in plugins):
        return path, False, ""
    bak = _backup(path)
    plugins.append(entry)
    _write_config(path, json.dumps(root, indent=2) + "\n")
    return path, True, bak


def unintegrate_opencode():
    path = opencode_config_path()
    if not os.path.exists(path):
        return path, 0
    with open(path) as f:
        raw = f.read()
    try:
        root = json.loads(strip_jsonc(raw))
    except ValueError:
        return path, 0
    _key, plugins = _plugin_list(root)
    if not isinstance(plugins, list):
        return path, 0
    kept = [p for p in plugins
            if not (isinstance(p, dict) and p.get("package") == PLUGIN_DIR)]
    if len(kept) == len(plugins):
        return path, 0
    _backup(path)
    root[_key] = kept
    _write_config(path, json.dumps(root, indent=2) + "\n")
    return path, len(plugins) - len(kept)
