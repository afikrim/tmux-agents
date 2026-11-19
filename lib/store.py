"""Atomic JSON state store + locked JSONL event log (stdlib only).

- Files created mode 0600 from the first byte (os.open O_CREAT).
- append+rotate serialized with fcntl LOCK_EX: no stray temp files, no races.
- Only metadata is stored (pane, kind, agent pid/start, socket, ts, action,
  optional filename basename). Never
  any pane content or conversation text.
"""
import fcntl
import json
import os
import time
from .activity import ACTIONS, safe_target

from .tmuxutil import state_paths


def ensure_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)


def _open_exclusive(path):
    ensure_dir(os.path.dirname(path))
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)


def atomic_write(path, data):
    ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp-%d" % os.getpid()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.write("\n")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read_state(paths):
    try:
        with open(paths["state"]) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"panes": {}, "updated": 0}


def append_event(paths, pane_id, kind, agent="", agent_pid=0,
                 agent_start=0, socket_id="", ts=None, action="", completed=False, origin="", target=""):
    rec = {"t": ts if ts is not None else time.time(), "pane": pane_id,
           "kind": kind, "agent": agent or "", "apid": agent_pid or 0,
           "astart": agent_start or 0, "sock": socket_id or ""}
    if action in ACTIONS:
        rec["action"] = action
        target=safe_target(target)
        if target:rec['target']=target
    if kind == "needs-input":
        rec["input_required"] = True
    if completed:
        rec["completed"] = True
    if origin in ('hook','manual'):
        rec['origin'] = origin
    ensure_dir(paths["dir"])
    guard = os.open(paths["events"] + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(guard, fcntl.LOCK_EX)
    fd = os.open(paths["events"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        try:
            size = os.fstat(fd).st_size
        except OSError:
            size = 0
        if size > 512 * 1024:
            # Rotate under the same lock: compact in place, no stray files.
            with open(paths["events"]) as f:
                lines = f.readlines()[-2000:]
            with os.fdopen(os.open(paths["events"] + ".new",
                                   os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                                   0o600), "w") as f:
                f.writelines(lines)
            os.replace(paths["events"] + ".new", paths["events"])
            os.chmod(paths["events"], 0o600)
            # Reopen append fd after replace.
            os.close(fd)
            fd = os.open(paths["events"], os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                         0o600)
        os.write(fd, (json.dumps(rec) + "\n").encode())
    finally:
        try:
            fcntl.flock(guard, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)
        os.close(guard)
    try:
        os.chmod(paths["events"], 0o600)
    except OSError:
        pass
    return rec


def read_events(paths, max_age=7200, limit=2000):
    out = []
    try:
        with open(paths["events"]) as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return out
    now = time.time()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        try:
            age = now - float(rec.get("t", 0))
        except (TypeError, ValueError):
            continue
        if 0 <= age <= max_age:
            out.append(rec)
    return out
