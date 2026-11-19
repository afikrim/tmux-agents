"""Optional desktop notifications for agents needing attention (stdlib only).

Disabled by default. Explicit opt-in via `tmux-agents notify --enable`.
No notifier subprocess is spawned while disabled.

What alerts (strict, fresh native only):
- live-generation native (origin=hook) explicit question / typed input
  decision / approval with source=event, bound to the current agent
  identity, and a usable (fresh, sane) event timestamp, or
- an unseen completion: idle with a fresh native completed event bound to
  the current identity (e.g. Stop while alive).
Exited processes, screen-only widgets, inferred activity, manual/test
events, stale/expired events, repeated polls, focus/visited
acknowledgments, and replacement generations without a new native bound
event never alert. `done` never pings (the agent is gone); the sidebar and
inbox still track it as unread per existing semantics.

Replay protection uses a durable timestamp boundary with all claimed keys at
its edge, plus bounded notice history. Candidates are processed in timestamp
order; equal timestamps remain distinct. Enable and daemon startup seed the
boundary to now, so old events cannot flood after restarts or history eviction.

Activation carries an opaque saved notification ID. Callbacks embed only
opaque routing IDs (socket, state dir via env, notification ID); the saved
route pins the exact pane, generation, target session, and the particular
original client (name + pid/creation). Opening revalidates all of it and
fails closed. Notification display never acknowledges; only a real terminal
visit marks unread read.
"""
import fcntl
import hashlib
import json
import math
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time

MAX_DEDUPE = 200
MAX_ROUTES = 50
MAX_DISPATCH_PER_POLL = 3
MAX_WORKERS = 3
DEFAULT_WORKER_TIMEOUT = 120.0
PIPE_LIMIT = 4096
STATE_FILE_LIMIT = 1024 * 1024

LABELS = {
    "needs-approval": "Approval needed",
    "question": "Question",
    "needs-input": "Input needed",
    "done": "Completed",
    "idle": "Completed",
}

ACTIONABLE = ("question", "needs-input", "needs-approval")


def _files(paths):
    d = paths["dir"]
    return {
        "notify": os.path.join(d, "notify.json"),
        "notified": os.path.join(d, "notified.json"),
        "routes": os.path.join(d, "notify-routes.json"),
        "lock": os.path.join(d, "notify.lock"),
    }


def _worker_timeout():
    try:
        v = float(os.environ.get("TMUX_AGENTS_NOTIFY_TIMEOUT", ""))
        return min(600.0, max(1.0, v))
    except (TypeError, ValueError):
        return DEFAULT_WORKER_TIMEOUT


_WORKERS = []


def _lock(paths):
    from . import store
    store.ensure_dir(paths["dir"])
    fd = os.open(_files(paths)["lock"], os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _unlock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(fd)


def _read_json(path, limit=STATE_FILE_LIMIT):
    try:
        with open(path) as f:
            raw=f.read(limit+1)
        return json.loads(raw) if len(raw)<=limit else None
    except (OSError, ValueError, RecursionError):
        return None


def get_settings(paths):
    """Fail-closed settings: malformed/missing means disabled, watermark kept."""
    d = _read_json(_files(paths)["notify"])
    if not isinstance(d, dict):
        return {"enabled": False, "watermark": None}
    enabled = d.get("enabled") is True
    wm = d.get("watermark", None)
    try:
        wm = float(wm) if wm is not None else None
        if wm is not None and not math.isfinite(wm): wm = None
    except (TypeError, ValueError):
        wm = None
    return {"enabled": enabled, "watermark": wm}


def is_enabled(paths):
    return bool(get_settings(paths)["enabled"])


def _read_valid_map(paths, name):
    d = _read_json(_files(paths)[name])
    if not isinstance(d, dict) or len(d) > 10000:
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict)
            and isinstance(v.get('t',0),(int,float)) and math.isfinite(v.get('t',0))}


def _prune_map(data, limit):
    if len(data) <= limit:
        return data
    items = sorted(data.items(), key=lambda kv: kv[1].get("t", 0))
    return dict(items[-limit:])


def dedupe_key(pane, generation, notice_key):
    return "%s|%s|%s" % (pane, generation, notice_key)


def notification_id(pane, generation, notice_key):
    return hashlib.sha1(("%s|%s|%s" % (pane, generation, notice_key)).encode()
                        ).hexdigest()[:12]


def set_enabled(paths, enabled, seed_state=None):
    """Atomic enable+seed (watermark=now blocks all backlog) or disable.

    Enabling and seeding happen under one lock so no daemon poll can slip
    between them and dispatch backlog. Disabling keeps watermark/dedupe so
    re-enable without new events stays silent; it also drops local workers.
    """
    from . import store
    now = time.time()
    lock = _lock(paths)
    try:
        if enabled:
            data = _read_valid_map(paths, "notified")
            panes = seed_state.get("panes", {}) if isinstance(seed_state, dict) else {}
            for pane_id, info in panes.items():
                if not isinstance(info, dict):
                    continue
                key, gen = info.get("notice_key", ""), info.get("generation", "")
                if not key or not gen:
                    continue
                if info.get("unread") or info.get("status") in ACTIONABLE:
                    data.setdefault(dedupe_key(pane_id, gen, key),
                                    {"pane": pane_id, "generation": gen,
                                     "notice_key": key, "t": now})
            data = _prune_map(data, MAX_DEDUPE)
            store.atomic_write(_files(paths)["notified"], data)
            store.atomic_write(_files(paths)["notify"],
                               {"enabled": True, "updated": now, "watermark": now})
        else:
            cur = _read_json(_files(paths)["notify"])
            wm = None
            if isinstance(cur, dict):
                try:
                    wm = float(cur.get("watermark")) if cur.get("watermark") is not None else None
                except (TypeError, ValueError):
                    wm = None
            store.atomic_write(_files(paths)["notify"],
                               {"enabled": False, "updated": now, "watermark": wm})
    finally:
        _unlock(lock)
    if not enabled:
        terminate_all()


def seed_startup(paths, state, max_event_t=None, force=False):
    """Seed backlog at startup (force), or recover a missing boundary.

    The enabling preference is preserved; dispatch must skip the recovery
    poll. A real daemon startup always seeds, even if its snapshot was lost.
    """
    from . import store
    now = time.time()
    lock = _lock(paths)
    try:
        cur = _read_json(_files(paths)["notify"])
        if not isinstance(cur, dict) or cur.get("enabled") is not True:
            return False
        if not force and get_settings(paths)['watermark'] is not None:
            return False
        wm = now
        data = _read_valid_map(paths, "notified")
        panes = state.get("panes", {}) if isinstance(state, dict) else {}
        for pane_id, info in panes.items():
            if not isinstance(info, dict):
                continue
            key, gen = info.get("notice_key", ""), info.get("generation", "")
            if not key or not gen:
                continue
            if info.get("unread") or info.get("status") in ACTIONABLE:
                data.setdefault(dedupe_key(pane_id, gen, key),
                                {"pane": pane_id, "generation": gen,
                                 "notice_key": key, "t": now})
        store.atomic_write(_files(paths)["notified"], _prune_map(data, MAX_DEDUPE))
        store.atomic_write(_files(paths)["notify"],
                           {"enabled": True, "updated": now, "watermark": wm})
        return True
    finally:
        _unlock(lock)


def claim_alert(paths, key, record, ev_t):
    """Atomic check+claim under one lock. Returns notif_id or ''.

    Rejects already-claimed keys and events at/below the durable watermark,
    advances the watermark past the claimed event, and persists the saved
    route for later `--open --id`. Fails closed on malformed state.
    """
    from . import store
    try:
        ev_t = float(ev_t)
    except (TypeError, ValueError):
        return ""
    if not key or not isinstance(record, dict):
        return ""
    now = time.time()
    lock = _lock(paths)
    try:
        cur = _read_json(_files(paths)["notify"])
        if not isinstance(cur, dict) or cur.get("enabled") is not True:
            return ""
        try:
            wm = float(cur.get("watermark")) if cur.get("watermark") is not None else None
        except (TypeError, ValueError):
            wm = None
        if wm is None or not math.isfinite(wm) or not math.isfinite(ev_t):
            return ""
        ties = cur.get('watermark_keys',[])
        if not isinstance(ties,list): return ''
        if ev_t < wm or (ev_t == wm and (not ties or key in ties)):
            return ''
        data = _read_valid_map(paths, "notified")
        if key in data:
            return ""
        pane, gen = record.get("pane", ""), record.get("generation", "")
        nkey = record.get("notice_key", "")
        if not pane or not gen or not nkey:
            return ""
        nid = notification_id(pane, gen, nkey)
        entry = dict(record, t=now)
        data[key] = entry
        store.atomic_write(_files(paths)["notified"], _prune_map(data, MAX_DEDUPE))
        if isinstance(record.get('client'),dict) and record['client'].get('name'):
            routes = _read_valid_map(paths, "routes")
            routes[nid] = entry
            store.atomic_write(_files(paths)["routes"], _prune_map(routes, MAX_ROUTES))
        store.atomic_write(_files(paths)["notify"],
                           {"enabled": True, "updated": now,
                            "watermark": max(wm, ev_t),
                            "watermark_keys": ((ties+[key]) if ev_t == wm else [key])[-2000:]})
        return nid
    finally:
        _unlock(lock)


def get_route(paths, notif_id):
    routes = _read_valid_map(paths, "routes")
    rec = routes.get(notif_id)
    return rec if isinstance(rec, dict) else None


def find_route_by_pane_gen(paths, pane, generation):
    routes = _read_valid_map(paths, "routes")
    best, best_t = None, -1.0
    for nid, rec in routes.items():
        if rec.get("pane") == pane and rec.get("generation") == generation:
            try:
                t = float(rec.get("t", 0))
            except (TypeError, ValueError):
                t = 0.0
            if t >= best_t:
                best, best_t = (nid, rec), t
    return best


def recent_routes(paths, limit=20):
    routes = _read_valid_map(paths, "routes")
    items = []
    for nid, rec in routes.items():
        try:
            t = float(rec.get("t", 0))
        except (TypeError, ValueError):
            t = 0.0
        items.append({"id": nid, "pane": rec.get("pane", ""),
                      "generation": rec.get("generation", ""),
                      "status": rec.get("status", ""),
                      "session": rec.get("session_name", ""),
                      "client": (rec.get("client") or {}).get("name", ""),
                      "time": t})
    items.sort(key=lambda r: r["time"])
    return items[-limit:]


def _which(name, env_override=""):
    if env_override and os.environ.get(env_override):
        cand = os.environ[env_override]
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
        return None
    return shutil.which(name)


def backend():
    """Return (name, path) of usable backend or (None, None)."""
    options=[('notify-send','TMUX_AGENTS_NOTIFY_SEND'),
             ('terminal-notifier','TMUX_AGENTS_TERMINAL_NOTIFIER')]
    if not any(os.environ.get(env) for _,env in options):
        options=options[1:] if sys.platform=='darwin' else options[:1] if sys.platform.startswith('linux') else []
    for name,env in options:
        path=_which(name,env)
        if path:return name,path
    return None, None


def label_for(status):
    return LABELS.get(status, "Attention needed")


def generic_body(status):
    if status in ACTIONABLE:
        return "An agent needs your input. Open it to respond."
    return "An agent finished. Open it to review."


def summary_for(status):
    return "tmux-agents: " + label_for(status)


def should_alert(status, source, unread, completed, ev_origin, bound_ok,
                 ev_usable):
    """Strict fresh-native gate (no I/O). Done/unknown/running never ping."""
    if not unread:
        return False
    if not bound_ok or ev_origin != "hook" or not ev_usable:
        return False
    if status in ACTIONABLE:
        return source == "event"
    if status == "idle":
        # Fresh native completed event bound to the current identity only.
        return bool(source == "event" and completed)
    return False


def macos_execute_command(script, socket_flags, state_dir, notif_id):
    parts = ["/usr/bin/env", "TMUX_AGENTS_STATE_DIR=" + os.path.abspath(state_dir),
             sys.executable, script, *socket_flags, "notify", "--open",
             "--id", notif_id]
    return shlex.join(parts)


def build_argv(status, notif_id, socket_flags, script, state_dir, actionable):
    """Return (backend_name, argv, execute_str). execute_str only for macOS."""
    name, path = backend()
    summary = summary_for(status)
    body = generic_body(status)
    if name == "notify-send":
        argv = [path, "--app-name=tmux-agents"]
        if actionable:
            # --action implies --wait: the daemon reads the chosen action
            # ("open") from stdout and runs the validated saved callback.
            argv += ["--action=open=Open", "--wait"]
        argv += [summary, body]
        return name, argv, ""
    if name == "terminal-notifier":
        argv = [path, "-title", summary, "-message", body,
                "-group", "tmux-agents-" + notif_id]
        cmd = ""
        if actionable:
            # terminal-notifier executes a shell string: quote everything.
            cmd = macos_execute_command(script, socket_flags, state_dir,
                                        notif_id)
            argv += ["-execute", cmd]
        return name, argv, cmd
    return None, [], ""


def list_clients(prefix):
    """Attached clients with identity (name/pid/created/session)."""
    from .tmuxutil import run_tmux
    out = []
    rc, raw, _ = run_tmux(prefix, "list-clients", "-F",
                           "#{client_name}\t#{client_pid}\t#{client_created}\t#{session_id}\t#{client_activity}")
    if rc == 0 and raw.strip():
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) != 5 or not parts[0]:
                continue
            try:
                pid = int(parts[1])
                activity = float(parts[4] or 0)
            except (TypeError, ValueError):
                continue
            if pid <= 0 or not parts[2]: continue
            out.append({"name": parts[0], "pid": pid,
                        "created": parts[2], "session": parts[3] or "",
                        "activity": activity})
    return out


def validate_route(prefix, paths, record):
    """Revalidate a saved route. Returns (ok, reason, pane_info, client)."""
    from .tmuxutil import socket_id_of, state_root
    if not isinstance(record, dict):
        return False, "saved route is invalid", None, ""
    try:
        if os.path.abspath(record.get("state_dir", "")) != os.path.abspath(state_root()):
            return False, "saved route is for another state directory", None, ""
    except (TypeError, ValueError):
        return False, "saved route is invalid", None, ""
    if not record.get("socket") or record["socket"] != socket_id_of(prefix):
        return False, "saved route is for another tmux server", None, ""
    client = record.get("client") or {}
    if not isinstance(client, dict) or not client.get("name"):
        return False, "no validated return path for this alert", None, ""
    live = [c for c in list_clients(prefix) if c["name"] == client["name"]]
    if not live:
        return False, "that terminal is no longer attached; nothing to open", None, ""
    cur = live[0]
    if (not client.get('pid') or not client.get('created')
            or cur.get('pid') != client.get('pid')
            or cur.get('created') != client.get('created')):
        return False, "that terminal session changed; nothing to open", None, ""
    pane_id, gen = record.get("pane", ""), record.get("generation", "")
    if not pane_id or not gen:
        return False, "saved route is invalid", None, ""
    from . import store, detect
    from .tmuxutil import run_tmux, state_paths
    info = store.read_state(state_paths(prefix)).get("panes", {}).get(pane_id, {})
    if not isinstance(info, dict) or info.get("generation") != gen or not info.get("agent"):
        return False, "this agent is no longer available; refresh the inbox", None, ""
    if info.get('session_id') != record.get('session_id'):
        return False, "this agent moved to another project; refresh the inbox", None, ""
    rc, out, _ = run_tmux(prefix, "display-message", "-p", "-t", pane_id, "#{pane_pid}")
    try:
        pane_pid = int(out.strip()) if rc == 0 else 0
    except ValueError:
        pane_pid = 0
    if not pane_pid:
        return False, "this agent is no longer available; refresh the inbox", None, ""
    ident = detect.agent_identity(pane_pid, detect.process_table())
    if (not ident or ident[0] != info.get("agent") or
            "%s:%s" % (ident[1], ident[2]) != gen):
        return False, "this agent has exited or been replaced; refresh the inbox", None, ""
    return True, "", info, client["name"]


def open_record(prefix, paths, record, visit):
    """Validate a saved route, then run the exact-generation visit path."""
    if not is_enabled(paths):
        return 1, 'Desktop notifications are disabled.'
    ok, reason, _info, client = validate_route(prefix, paths, record)
    if not ok:
        return 1, reason
    try:
        return visit(prefix, record["pane"], record["generation"], client)
    except Exception as exc:
        return 1, str(exc)


def _spawn(argv, env):
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE if argv else subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True, close_fds=True, env=env)
    os.set_blocking(proc.stdout.fileno(), False)
    return proc


def _close_proc(proc):
    try:
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _kill_worker(w):
    proc = w["proc"]
    try:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    finally:
        _close_proc(proc)


def _reap_once():
    """Poll owned workers only (never waitpid(-1)); handle timeouts/actions."""
    global _WORKERS
    if not _WORKERS:
        return
    now = time.monotonic()
    pending = []
    for w in _WORKERS:
        proc = w["proc"]
        # Never wait for EOF: a notifier can leave descendants holding its
        # stdout open. Drain bounded chunks while it runs to avoid pipe stalls.
        try:
            chunk = os.read(proc.stdout.fileno(), PIPE_LIMIT)
            w['output'] = (w.get('output',b'') + chunk)[:PIPE_LIMIT]
            if len(w['output']) >= PIPE_LIMIT: w['overflow'] = True
        except (BlockingIOError,OSError,ValueError): pass
        try:
            rc = proc.poll()
        except Exception:
            rc = -1
        if rc is None:
            if now > w["deadline"]:
                _kill_worker(w)
            else:
                pending.append(w)
            continue
        out = w.get('output',b'')
        try:
            if proc.stdout:
                try:
                    out += os.read(proc.stdout.fileno(), PIPE_LIMIT) or b""
                except Exception:
                    pass
                try:
                    proc.stdout.close()
                except Exception:
                    pass
        finally:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        if (w["backend"] == "notify-send" and rc == 0 and
                not w.get('overflow') and len(out)<PIPE_LIMIT and out.strip() == b'open'):
            handler, record = w.get('on_open'), w.get("record")
            if handler is not None and isinstance(record, dict):
                try:
                    handler(record)
                except Exception:
                    pass
    _WORKERS = pending


def reap_workers():
    try:
        _reap_once()
    except Exception:
        pass


def terminate_all():
    global _WORKERS
    workers, _WORKERS = _WORKERS, []
    for w in workers:
        try:
            _kill_worker(w)
        except Exception:
            pass


def drain(timeout=10.0):
    """Blocking reap for tests only; the daemon never waits on notifiers."""
    end = time.monotonic() + max(0.0, timeout)
    while _WORKERS and time.monotonic() < end:
        _reap_once()
        if _WORKERS:
            time.sleep(0.05)
    return len(_WORKERS)


def active_count():
    reap_workers()
    return len(_WORKERS)


def dispatch(prefix, paths, candidates, socket_flags=None, script=None,
             state_dir=None, on_open=None):
    """Bounded dispatch of fresh native alerts. Never raises or blocks.

    At most MAX_WORKERS notifier children exist globally; each dies by
    deadline. Every candidate is atomically claimed (watermark + dedupe +
    saved route) exactly once. Missing backend or spawn failure degrades
    silently. Linux Open selections execute the validated saved callback.
    """
    try:
        settings = get_settings(paths)
        if not settings["enabled"]:
            terminate_all()
            return []
        _reap_once()
        if len(_WORKERS) >= MAX_WORKERS:
            return []
        name, _ = backend()
        if socket_flags is None:
            from .tmuxutil import cli_socket_flags
            socket_flags = cli_socket_flags(prefix)
        if script is None:
            script = os.path.join(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))),
                "bin", "tmux-agents")
        if state_dir is None:
            from .tmuxutil import state_root
            state_dir = state_root()
        from .tmuxutil import socket_id_of
        sid = socket_id_of(prefix)
        state_dir = os.path.abspath(state_dir)
        sent = []
        wm = settings['watermark']
        keys = _read_valid_map(paths,'notified')
        cur = _read_json(_files(paths)['notify']) or {}
        tied = cur.get('watermark_keys',[])
        eligible = [c for c in candidates if c.get('key') not in keys
                    and c.get('key') not in tied
                    and isinstance(c.get('ev_t'),(int,float))
                    and wm is not None and c['ev_t'] >= wm]
        for cand in sorted(eligible,key=lambda c:c['ev_t'])[:MAX_DISPATCH_PER_POLL]:
            if len(_WORKERS) >= MAX_WORKERS:
                break
            try:
                key = cand.get("key", "")
                ev_t = float(cand.get("ev_t", 0))
            except (TypeError, ValueError):
                continue
            if not key or not (ev_t > 0):
                continue
            client = cand.get("client")
            actionable = bool(isinstance(client, dict) and client.get("name"))
            record = {"pane": cand.get("pane", ""),
                      "generation": cand.get("generation", ""),
                      "notice_key": cand.get("notice_key", ""),
                      "status": cand.get("status", ""),
                      "ev_t": ev_t,
                      "socket": sid,
                      "sock_flags": list(socket_flags),
                      "state_dir": state_dir,
                      "session_id": cand.get("session_id", ""),
                      "session_name": cand.get("session_name", ""),
                      "client": dict(client) if actionable else {}}
            nid = claim_alert(paths, key, record, ev_t)
            if not nid:
                continue
            if not name:
                continue
            if not actionable:
                # No attached client: no actionable callback exists, so no
                # visual that implies one. The sidebar/inbox retain unread;
                # a later distinct native event can alert.
                continue
            _bname, argv, _cmd = build_argv(
                cand.get("status", ""), nid, socket_flags, script, state_dir,
                True)
            if not argv:
                continue
            env = dict(os.environ)
            env["TMUX_AGENTS_STATE_DIR"] = state_dir
            try:
                proc = _spawn(argv, env)
            except Exception:
                continue
            _WORKERS.append({"proc": proc, "deadline": time.monotonic() + _worker_timeout(),
                             "backend": name, "record": record, "notif_id": nid,
                             "on_open": on_open, "output": b''})
            sent.append(nid)
        return sent
    except Exception:
        return []
