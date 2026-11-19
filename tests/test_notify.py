"""Isolated desktop-notification tests. No real alerts, models, or production state.

Every tmux interaction uses a private server (-L, -f /dev/null) and an
isolated HOME/state root. Both notifier overrides always point at fake
executables (or explicit nonexistent paths), so a real desktop notifier is
never invoked on any host.
"""
import contextlib
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import warnings

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(BASE, "bin", "tmux-agents")
sys.path.insert(0, BASE)
from lib import notify as N
from lib import store as _store
from lib.tmuxutil import state_paths as _state_paths


def T(sock, *args, timeout=10):
    return subprocess.run(["tmux", "-L", sock, "-f", "/dev/null", *args],
                          capture_output=True, text=True, timeout=timeout)


def write_exe(path, body):
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)


class NotifyUnitTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ta-nu-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.paths = {"dir": self.d}
        self._old = dict(os.environ)
        self.addCleanup(self._restore_env)
        N.terminate_all()
        self.addCleanup(N.terminate_all)

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._old)

    def _iso_env(self, send="#missing#", tn="#missing#"):
        if send == "#missing#":
            send = os.path.join(self.d, "no-notify-send")
        if tn == "#missing#":
            tn = os.path.join(self.d, "no-terminal-notifier")
        os.environ["TMUX_AGENTS_NOTIFY_SEND"] = send
        os.environ["TMUX_AGENTS_TERMINAL_NOTIFIER"] = tn

    def test_gate_matrix(self):
        A = N.should_alert
        # Actionable native bound usable events alert.
        for st in ("question", "needs-input", "needs-approval"):
            self.assertTrue(A(st, "event", True, False, "hook", True, True))
        # Stale/unusable, manual, screen, unbound, acked never alert.
        self.assertFalse(A("question", "event", True, False, "hook", True, False))
        self.assertFalse(A("needs-approval", "event", True, False, "manual", True, True))
        self.assertFalse(A("question", "screen", True, False, "hook", True, True))
        self.assertFalse(A("question", "event", True, False, "hook", False, True))
        self.assertFalse(A("question", "event", False, False, "hook", True, True))
        self.assertFalse(A("needs-input", "event", True, False, "", True, True))
        # Completion: only fresh native bound completed idle events.
        self.assertTrue(A("idle", "event", True, True, "hook", True, True))
        self.assertFalse(A("idle", "event", True, False, "hook", True, True))
        self.assertFalse(A("idle", "event", True, True, "hook", True, False))  # stale
        self.assertFalse(A("idle", "event", True, True, "manual", True, True))
        self.assertFalse(A("idle", "process", True, True, "hook", False, True))
        self.assertFalse(A("idle", "process", True, True, "", False, True))
        self.assertFalse(A("idle", "screen", True, True, "hook", True, True))
        # done/unknown/running never ping (sidebar/inbox still track done).
        for st in ("done", "unknown", "running"):
            self.assertFalse(A(st, "event", True, True, "hook", True, True))
        self.assertFalse(A("done", "process", True, False, "", False, True))

    def test_settings_fail_closed(self):
        self.assertFalse(N.is_enabled(self.paths))
        for raw in ("[]", "{}", "null", "not json{", '{"enabled": "yes"}'):
            with open(os.path.join(self.d, "notify.json"), "w") as f:
                f.write(raw)
            s = N.get_settings(self.paths)
            self.assertFalse(s["enabled"], raw)
        with open(os.path.join(self.d, "notify.json"), "w") as f:
            f.write('{"enabled": true, "watermark": "bogus"}')
        s = N.get_settings(self.paths)
        self.assertTrue(s["enabled"])
        self.assertIsNone(s["watermark"])

    def test_claim_watermark_and_eviction(self):
        self._iso_env()
        N.set_enabled(self.paths, True)
        wm = N.get_settings(self.paths)["watermark"]
        self.assertGreater(wm, 0)
        rec = {"pane": "%1", "generation": "g", "notice_key": "n",
               "status": "question", "socket": "S-x", "state_dir": self.d,
               "session_id": "$1", "session_name": "s",
               "client": {"name": "c", "pid": 1, "created": "2", "session": "$1"}}
        # Old event at/below the watermark never claims.
        self.assertEqual(N.claim_alert(self.paths, "k|g|n", rec, wm), "")
        self.assertEqual(N.claim_alert(self.paths, "k|g|n", rec, wm - 1), "")
        nid = N.claim_alert(self.paths, "k|g|n", rec, wm + 10)
        self.assertTrue(nid)
        self.assertEqual(N.claim_alert(self.paths, "k|g|n", rec, wm + 11), "")
        # Watermark advanced past the event: eviction cannot reflood it.
        self.assertGreater(N.get_settings(self.paths)["watermark"], wm + 9)
        data = N._read_valid_map(self.paths, "notified")
        del data["k|g|n"]
        from lib import store as _st
        _st.atomic_write(os.path.join(self.d, "notified.json"), data)
        self.assertEqual(N.claim_alert(self.paths, "k|g|n", rec, wm + 10), "")
        # But a distinct later native event still alerts.
        rec2 = dict(rec, notice_key="n2")
        nid2 = N.claim_alert(self.paths, "k|g|n2", rec2, wm + 20)
        self.assertTrue(nid2 and nid2 != nid)
        # Saved route round-trips opaquely.
        self.assertEqual(N.get_route(self.paths, nid2)["generation"], "g")
        found = N.find_route_by_pane_gen(self.paths, "%1", "g")
        self.assertEqual(found[1]["notice_key"], "n2")
        # Bounded.
        for i in range(300):
            r = dict(rec, pane="%%%d" % i, notice_key="k%d" % i)
            N.claim_alert(self.paths, "kk%d" % i, r, wm + 30 + i)
        self.assertLessEqual(len(N._read_valid_map(self.paths, "notified")), N.MAX_DEDUPE)
        self.assertLessEqual(len(N._read_valid_map(self.paths, "routes")), N.MAX_ROUTES)

    def test_linux_argv_action_wait_privacy(self):
        self._iso_env()
        fake = os.path.join(self.d, "notify-send")
        write_exe(fake, "#!/bin/sh\nexit 0\n")
        os.environ["TMUX_AGENTS_NOTIFY_SEND"] = fake
        name, argv, _ = N.build_argv("question", "nid1", [], "/s", "/s/root", True)
        self.assertEqual(name, "notify-send")
        self.assertIn("--action=open=Open", argv)
        self.assertIn("--wait", argv)
        joined = " ".join(argv)
        for bad in ("secret", "/home/user/code", "prompt text"):
            self.assertNotIn(bad, joined)
        # Non-actionable (no client) carries no action/wait flag.
        _, argv2, _ = N.build_argv("question", "nid1", [], "/s", "/s/root", False)
        self.assertNotIn("--action=open=Open", argv2)

    def test_macos_execute_shell_roundtrip(self):
        self._iso_env()
        rec_log = os.path.join(self.d, "mac-args.log")
        rec = os.path.join(self.d, "recorder.py")
        write_exe(rec, "#!/usr/bin/env python3\nimport os,sys\n"
                       "open(%r,'w').write(os.environ.get('TMUX_AGENTS_STATE_DIR','')+'\\n'+'\\n'.join(sys.argv[1:]))\n" % rec_log)
        os.environ["TMUX_AGENTS_TERMINAL_NOTIFIER"] = os.path.join(self.d, "no-tn-real")
        # Force terminal-notifier path via override to our fake executor below.
        tn = os.path.join(self.d, "terminal-notifier")
        write_exe(tn, "#!/bin/sh\nexec \"$@\"\n")
        os.environ["TMUX_AGENTS_TERMINAL_NOTIFIER"] = tn
        os.environ["TMUX_AGENTS_NOTIFY_SEND"] = os.path.join(self.d, "no-ns")
        state_dir = "/tmp/state path/with 'quote';$x"
        script = rec
        cmd = N.macos_execute_command(script, ["--socket-path", "/tmp/sock path"],
                                      state_dir, "nidABC")
        self.assertIn("--id", cmd)
        self.assertIn("nidABC", cmd)
        for bad in ("secret title", "prompt text", "/home/user/code"):
            self.assertNotIn(bad, cmd)
        p = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(rec_log) as f:
            got = f.read().splitlines()
        self.assertEqual(got[0], os.path.abspath(state_dir))
        self.assertIn("notify", got)
        self.assertIn("--open", got)
        self.assertIn("--id", got)
        self.assertIn("nidABC", got)
        self.assertIn("/tmp/sock path", got)

    def test_worker_cap_timeout_cleanup(self):
        self._iso_env()
        marker = "ta-sleep-marker-xyz-%d" % os.getpid()
        fake = os.path.join(self.d, "notify-send")
        write_exe(fake, "#!/bin/sh\n# %s\nsleep 300\n" % marker)
        os.environ["TMUX_AGENTS_NOTIFY_SEND"] = fake
        os.environ["TMUX_AGENTS_NOTIFY_TIMEOUT"] = "1"
        N.set_enabled(self.paths, True)
        wm = N.get_settings(self.paths)["watermark"]
        cands = [{"key": "wk%d|g|n" % i, "pane": "%%%d" % i, "generation": "g",
                  "status": "question", "notice_key": "n", "ev_t": wm + 10 + i,
                  "session_id": "$1", "session_name": "s",
                  "client": {"name": "c", "pid": 1, "created": "2", "session": "$1"}}
                 for i in range(5)]
        t0 = time.monotonic()
        sent = N.dispatch([], self.paths, cands, socket_flags=[],
                          script="/s/bin/tmux-agents", state_dir=self.d)
        dt = time.monotonic() - t0
        self.assertEqual(len(sent), N.MAX_WORKERS)
        self.assertLess(dt, 8, "dispatch must not block on notifiers")
        procs = [w["proc"] for w in N._WORKERS]
        self.assertEqual(len(procs), N.MAX_WORKERS)
        # Global cap: further distinct events wait for a free slot.
        more = [dict(cands[0], key="late|g|n", ev_t=wm + 999)]
        self.assertEqual(N.dispatch([], self.paths, more, socket_flags=[],
                                    script="/s", state_dir=self.d), [])
        # Timeout reaps and kills the whole process group (no leftover sleep).
        time.sleep(1.6)
        N.reap_workers()
        self.assertEqual(N._WORKERS, [])
        for p in procs:
            self.assertIsNotNone(p.poll())
            self.assertTrue(p.stdout.closed)
        time.sleep(0.5)
        ps = subprocess.run(["ps", "-A", "-o", "command="], capture_output=True,
                            text=True, timeout=10).stdout
        self.assertNotIn(marker, ps)
        N.terminate_all()

    def test_open_roundtrip_handler_and_failure(self):
        self._iso_env()
        fake = os.path.join(self.d, "notify-send")
        log = os.path.join(self.d, "calls.log")
        write_exe(fake, "#!/bin/sh\necho \"$@\" >> '%s'\necho open\nexit 0\n" % log)
        os.environ["TMUX_AGENTS_NOTIFY_SEND"] = fake
        N.set_enabled(self.paths, True)
        wm = N.get_settings(self.paths)["watermark"]
        seen = []
        cand = {"key": "o|g|n", "pane": "%9", "generation": "g", "status": "question",
                "notice_key": "n", "ev_t": wm + 5, "session_id": "$1", "session_name": "s",
                "client": {"name": "c", "pid": 7, "created": "8", "session": "$1"}}
        sent = N.dispatch([], self.paths, [cand], socket_flags=[],
                          script="/s", state_dir=self.d, on_open=seen.append)
        self.assertEqual(len(sent), 1)
        self.assertEqual(N.drain(10), 0)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["pane"], "%9")
        # Route persisted for --open --id.
        nid = sent[0]
        self.assertEqual(N.get_route(self.paths, nid)["client"]["name"], "c")
        # Failing backend: claimed, no handler, no raise, no hang.
        bad = os.path.join(self.d, "fail-send")
        write_exe(bad, "#!/bin/sh\nexit 1\n")
        os.environ["TMUX_AGENTS_NOTIFY_SEND"] = bad
        seen2 = []
        cand2 = dict(cand, key="f|g|n", notice_key="n2", ev_t=wm + 50)
        self.assertEqual(len(N.dispatch([], self.paths, [cand2], socket_flags=[],
                                        script="/s", state_dir=self.d,
                                        on_open=seen2.append)), 1)
        self.assertEqual(N.drain(10), 0)
        self.assertEqual(seen2, [])
        # Missing backend: claimed, nothing spawned.
        os.environ["TMUX_AGENTS_NOTIFY_SEND"] = os.path.join(self.d, "nope")
        os.environ["TMUX_AGENTS_TERMINAL_NOTIFIER"] = os.path.join(self.d, "nope2")
        cand3 = dict(cand, key="m|g|n", notice_key="n3", ev_t=wm + 60)
        self.assertEqual(N.dispatch([], self.paths, [cand3], socket_flags=[],
                                    script="/s", state_dir=self.d), [])
        N.terminate_all()

    def test_validate_route_no_tmux(self):
        self._iso_env()
        os.environ["TMUX_AGENTS_STATE_DIR"] = self.d
        rec = {"pane": "%1", "generation": "1:2", "notice_key": "n", "status": "q",
               "socket": "S-other", "state_dir": self.d, "session_id": "$9",
               "client": {"name": "c", "pid": 1, "created": "2", "session": "$9"}}
        ok, reason, _, _ = N.validate_route([], self.paths, rec)
        self.assertFalse(ok)
        self.assertIn("server", reason)
        rec2 = dict(rec, socket=None, client={})
        from lib.tmuxutil import socket_id_of
        rec2["socket"] = socket_id_of([])
        ok, reason, _, _ = N.validate_route([], self.paths, rec2)
        self.assertFalse(ok)
        self.assertIn("return path", reason)

    def test_burst_orders_events_and_keeps_equal_timestamps(self):
        self._iso_env()
        fake=os.path.join(self.d,'notify-send')
        write_exe(fake,'#!/bin/sh\nexit 0\n')
        os.environ['TMUX_AGENTS_NOTIFY_SEND']=fake
        N.set_enabled(self.paths,True)
        t=N.get_settings(self.paths)['watermark']+1
        candidates=[dict(key=str(i),pane='%%%d'%i,generation='g',notice_key=str(i),
                         status='question',ev_t=t+(i//4),
                         client=dict(name='c',pid=1,created='2')) for i in range(8)]
        sent=[]
        for _ in range(4):
            sent+=N.dispatch([],self.paths,list(reversed(candidates)),socket_flags=[],
                             script=BIN,state_dir=self.d)
            self.assertEqual(N.drain(3),0)
        self.assertEqual(len(sent),8)
        self.assertEqual(len(set(sent)),8)
        # Even after the short dedupe history is gone, the timestamp boundary
        # retains all keys at its edge rather than replaying tied events.
        _store.atomic_write(os.path.join(self.d,'notified.json'),{})
        self.assertEqual(N.dispatch([],self.paths,candidates,socket_flags=[],
                                   script=BIN,state_dir=self.d),[])

    def test_startup_and_corrupt_settings_fail_closed(self):
        N.set_enabled(self.paths,True)
        before=N.get_settings(self.paths)['watermark']
        time.sleep(.01)
        self.assertTrue(N.seed_startup(self.paths,{},force=True))
        self.assertGreater(N.get_settings(self.paths)['watermark'],before)
        rec=dict(pane='%1',generation='g',notice_key='n')
        self.assertEqual(N.claim_alert(self.paths,'old',rec,before),'')
        for raw in ('[]','{"enabled":true,"watermark":NaN}',
                    '{"enabled":true,"watermark":Infinity}'):
            with open(os.path.join(self.d,'notify.json'),'w') as f:f.write(raw)
            self.assertEqual(N.claim_alert(self.paths,'bad',rec,time.time()),'')

    def test_pipe_output_is_bounded_and_not_a_shell_command(self):
        self._iso_env()
        fake=os.path.join(self.d,'notify-send')
        write_exe(fake,'#!'+sys.executable+'\nimport sys\nsys.stdout.write("open; ignored\\n"+"x"*100000)\n')
        os.environ['TMUX_AGENTS_NOTIFY_SEND']=fake
        N.set_enabled(self.paths,True)
        cand=dict(key='pipe',pane='%1',generation='g',notice_key='p',status='question',
                  ev_t=time.time()+1,client=dict(name='c',pid=1,created='2'))
        opened=[]
        N.dispatch([],self.paths,[cand],socket_flags=[],script=BIN,state_dir=self.d,on_open=opened.append)
        self.assertEqual(N.drain(5),0)
        self.assertEqual(opened,[])


@contextlib.contextmanager
def attached_client(sock, target):
    sleep_p = subprocess.Popen(["sleep", "60"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    before = set(T(sock, "list-clients", "-F", "#{client_name}").stdout.split())
    p = subprocess.Popen(["tmux", "-L", sock, "-f", "/dev/null", "-C",
                          "attach-session", "-t", target],
                         stdin=sleep_p.stdout, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        sleep_p.stdout.close()
    except Exception:
        pass
    name = None
    try:
        for _ in range(100):
            names = set(T(sock, "list-clients", "-F", "#{client_name}").stdout.split())
            new = names - before
            if new:
                name = sorted(new)[0]
                break
            time.sleep(0.1)
        assert name, "control client did not attach"
        yield name
    finally:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
        try:
            sleep_p.terminate()
            sleep_p.wait(timeout=5)
        except Exception:
            try:
                sleep_p.kill()
            except Exception:
                pass


class NotifyIsoTest(unittest.TestCase):
    SOCK = "ta-notify"
    home = None
    mod = None

    @classmethod
    def setUpClass(cls):
        cls.mod = runpy.run_path(BIN)

    @classmethod
    def tearDownClass(cls):
        N.terminate_all()

    def setUp(self):
        N.terminate_all()
        self.addCleanup(N.terminate_all)
        self._old = dict(os.environ)
        self.addCleanup(self._restore)
        self.home = tempfile.mkdtemp(prefix='ta-nhome-')
        self.addCleanup(shutil.rmtree,self.home,True)
        self.SOCK = 'ta-notify-'+os.path.basename(self.home)
        r = T(self.SOCK,'new-session','-d','-s','note','-x','200','-y','50')
        self.assertEqual(r.returncode,0,r.stderr)
        self.addCleanup(T,self.SOCK,'kill-server')
        self.fbin = tempfile.mkdtemp(prefix="ta-nbin-")
        self.addCleanup(shutil.rmtree, self.fbin, True)
        os.environ.update(self.env())
        os.environ['TMUX_AGENTS_NOTIFY_SEND']=os.path.join(self.fbin,'missing-send')
        os.environ['TMUX_AGENTS_TERMINAL_NOTIFIER']=os.path.join(self.fbin,'missing-tn')

    def _restore(self):
        os.environ.clear()
        os.environ.update(self._old)

    def env(self):
        e = dict(os.environ)
        e["HOME"] = self.home
        e["TMUX_AGENTS_STATE_DIR"] = os.path.join(self.home, "state")
        e.pop("TMUX", None)
        e.pop("TMUX_PANE", None)
        return e

    def fake_open_send(self):
        log = os.path.join(self.fbin, "calls.log")
        try:
            os.unlink(log)
        except OSError:
            pass
        write_exe(os.path.join(self.fbin, "notify-send"),
                  "#!/bin/sh\necho \"$@\" >> '%s'\necho open\nexit 0\n" % log)
        write_exe(os.path.join(self.fbin, "terminal-notifier"),
                  "#!/bin/sh\nexit 0\n")
        e = self.env()
        e["TMUX_AGENTS_NOTIFY_SEND"] = os.path.join(self.fbin, "notify-send")
        e["TMUX_AGENTS_TERMINAL_NOTIFIER"] = os.path.join(self.fbin, "terminal-notifier")
        os.environ.update(e)
        return e, log

    def cli(self, *args, env, timeout=30):
        return subprocess.run([sys.executable, BIN, "--socket", self.SOCK, *args],
                              capture_output=True, text=True, timeout=timeout, env=env)

    def prefix_paths(self):
        from lib.tmuxutil import state_paths
        prefix = ["-L", self.SOCK]
        return prefix, state_paths(prefix)

    def start_agent(self, window):
        d = tempfile.mkdtemp(prefix="ta-nagent-")
        self.addCleanup(shutil.rmtree, d, True)
        agent = os.path.join(d, "codex")
        write_exe(agent, "#!/bin/sh\nwhile true; do echo tick; sleep 1; done\n")
        T(self.SOCK, "new-window", "-d", "-t", "note", "-n", window, agent)
        time.sleep(1.0)
        wid = T(self.SOCK, "display-message", "-p", "-t", "note:" + window,
                "#{window_id}").stdout.strip()
        pane = T(self.SOCK, "display-message", "-p", "-t", wid,
                 "#{pane_id}").stdout.strip()
        return wid, pane

    def daemon(self, env):
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            import lib.tmuxutil as tu
            tu._sid_cache.clear()
            from lib.tmuxutil import state_paths
            prefix = ["-L", self.SOCK]
            paths = state_paths(prefix)
            return self.mod["daemon_once"](prefix, paths), prefix, paths

    def test_cli_modes_and_status(self):
        import unittest.mock
        e, _ = self.fake_open_send()
        r = self.cli("notify", "--enable", "--open", env=e)
        self.assertNotEqual(r.returncode, 0)
        r = self.cli("notify", "--status", env=e)
        self.assertIn("disabled", r.stdout)
        r = self.cli("notify", "--enable", env=e)
        self.assertIn("enabled", r.stdout)
        r = self.cli("notify", "--status", "--json", env=e)
        body = json.loads(r.stdout)
        self.assertIs(body["enabled"], True)
        self.assertEqual(body["backend"], "notify-send")
        r = self.cli("notify", "--disable", env=e)
        self.assertIn("disabled", r.stdout)

    def test_full_open_roundtrip_two_clients(self):
        import unittest.mock
        e, log = self.fake_open_send()
        self.cli("notify", "--enable", env=e)
        wid, pane = self.start_agent("nround")
        T(self.SOCK, "new-window", "-d", "-t", "note", "-n", "nother", "sleep 60")
        wid_other = T(self.SOCK, "display-message", "-p", "-t", "note:nother",
                      "#{window_id}").stdout.strip()
        T(self.SOCK, "new-session", "-d", "-s", "aux", "-x", "200", "-y", "50")
        wid_aux = T(self.SOCK, "display-message", "-p", "-t", "aux",
                    "#{window_id}").stdout.strip()
        # c1 watches another window of the same session (agent pane unseen);
        # c2 lives in a different session and must stay untouched.
        with attached_client(self.SOCK, "note:nother") as c1, \
                attached_client(self.SOCK, "aux") as c2:
            with unittest.mock.patch.dict(os.environ, e, clear=False):
                import lib.tmuxutil as tu
                tu._sid_cache.clear()
            sb, prefix, paths = self.daemon(e)
            gen = sb["panes"][pane]["generation"]
            from lib import store as st
            from lib.tmuxutil import socket_id_of
            sid = socket_id_of(prefix)
            ident = gen.split(":")
            st.append_event(paths, pane, "question", agent="codex",
                            agent_pid=int(ident[0]),
                            agent_start=float(ident[1]), socket_id=sid,
                            action="Thinking", origin="hook")
            sb, _, _ = self.daemon(e)
            self.assertEqual(sb["panes"][pane]["status"], "question")
            routes = N.recent_routes(paths)
            self.assertEqual(len(routes), 1)
            nid = routes[0]["id"]
            self.assertEqual(routes[0]["client"], c1)
            self.assertEqual(N.drain(15), 0)
            views=dict(line.split('\t') for line in T(self.SOCK,'list-clients','-F',
                       '#{client_name}\t#{window_id}').stdout.splitlines())
            got = views[c1]
            self.assertEqual(got, wid)
            got2 = views[c2]
            self.assertEqual(got2, wid_aux)
            # CLI --open --id works too and rejects unknown IDs.
            r = self.cli("notify", "--open", "--id", "deadbeef0000", env=e)
            self.assertNotEqual(r.returncode, 0)
            r = self.cli("notify", "--open", "--pane", pane, "--generation", gen,
                         env=e)
            self.assertEqual(r.returncode, 0, r.stderr)
            T(self.SOCK, "kill-session", "-t", "aux")
            T(self.SOCK, "kill-window", "-t", wid)
            T(self.SOCK, "kill-window", "-t", wid_other)

    def test_stale_replacement_and_ack(self):
        import unittest.mock
        e, _ = self.fake_open_send()
        self.cli("notify", "--enable", env=e)
        wid, pane = self.start_agent("nstale")
        with unittest.mock.patch.dict(os.environ, e, clear=False):
            import lib.tmuxutil as tu
            tu._sid_cache.clear()
        sb, prefix, paths = self.daemon(e)
        gen = sb["panes"][pane]["generation"]
        from lib import store as st
        from lib.tmuxutil import socket_id_of
        sid = socket_id_of(prefix)
        apid, astart = gen.split(":")[0], gen.split(":")[1]
        # Stale bound event (expired TTL) never alerts.
        st.append_event(paths, pane, "needs-approval", agent="codex",
                        agent_pid=int(apid), agent_start=float(astart),
                        socket_id=sid, ts=time.time() - 8000, origin="hook")
        sb, _, _ = self.daemon(e)
        self.assertEqual(N.recent_routes(paths), [])
        self.assertEqual(N.drain(3), 0)
        # Replacement generation without a new event never alerts.
        T(self.SOCK, "respawn-pane", "-t", pane, "-k", "sleep 60")
        time.sleep(1.0)
        sb, _, _ = self.daemon(e)
        self.assertFalse(sb["panes"][pane].get("agent"))
        self.assertEqual(N.recent_routes(paths), [])
        T(self.SOCK, "kill-window", "-t", wid)

    def test_split_decision_display_and_focus_ack(self):
        e,_=self.fake_open_send()
        self.cli('notify','--enable',env=e)
        wid,first=self.start_agent('split-one')
        _,second=self.start_agent('split-two')
        self.assertEqual(T(self.SOCK,'join-pane','-d','-h','-s',second,'-t',first).returncode,0)
        with attached_client(self.SOCK,'note:0') as client:
            state,prefix,paths=self.daemon(e)
            gen=state['panes'][second]['generation']
            pid,start=gen.split(':')
            _store.append_event(paths,second,'needs-input',agent='codex',
                                agent_pid=int(pid),agent_start=float(start),
                                socket_id=paths['sid'],origin='hook')
            state,_,_=self.daemon(e)
            self.assertTrue(state['panes'][second]['unread'])
            self.assertEqual(state['panes'][second]['status'],'needs-input')
            routes=N.recent_routes(paths)
            self.assertEqual(len(routes),1)
            self.assertEqual(N.drain(5),0)
            views=dict(line.split('\t') for line in T(self.SOCK,'list-clients','-F',
                       '#{client_name}\t#{pane_id}').stdout.splitlines())
            self.assertEqual(views[client],second)
            state,_,_=self.daemon(e)
            self.assertFalse(state['panes'][second]['unread'])
            self.assertEqual(len(N.recent_routes(paths)),1)

    def test_saved_client_reuse_detach_and_replaced_pane(self):
        e,_=self.fake_open_send()
        # Do not click automatically; validate the saved identity later.
        write_exe(e['TMUX_AGENTS_NOTIFY_SEND'],'#!/bin/sh\nexit 0\n')
        self.cli('notify','--enable',env=e)
        _,pane=self.start_agent('identity')
        with attached_client(self.SOCK,'note:0'):
            state,prefix,paths=self.daemon(e)
            gen=state['panes'][pane]['generation']; pid,start=gen.split(':')
            _store.append_event(paths,pane,'question',agent='codex',agent_pid=int(pid),
                                agent_start=float(start),socket_id=paths['sid'],origin='hook')
            self.daemon(e)
            nid=N.recent_routes(paths)[0]['id']; record=N.get_route(paths,nid)
            self.assertEqual(N.drain(5),0)
            self.assertTrue(N.validate_route(prefix,paths,record)[0])
            for replacement in (dict(record['client'],pid=record['client']['pid']+1),
                                dict(record['client'],created='reused')):
                with mock.patch.object(N,'list_clients',return_value=[replacement]):
                    self.assertFalse(N.validate_route(prefix,paths,record)[0])
            self.assertFalse(N.validate_route(prefix,paths,dict(record,socket='other'))[0])
            self.assertFalse(N.validate_route(prefix,paths,dict(record,state_dir=self.home))[0])
            # Live pane replaced before the daemon updates its old snapshot.
            T(self.SOCK,'respawn-pane','-k','-t',pane,'sleep 60')
            time.sleep(.2)
            self.assertFalse(N.validate_route(prefix,paths,record)[0])
        r=self.cli('notify','--open','--id',nid,env=e)
        self.assertNotEqual(r.returncode,0)

    def test_existing_or_acked_or_manual_events_do_not_alert(self):
        e,_=self.fake_open_send()
        _,pane=self.start_agent('old')
        state,prefix,paths=self.daemon(e)
        gen=state['panes'][pane]['generation']; pid,start=gen.split(':')
        def emit(kind,origin='hook',**kw):
            return _store.append_event(paths,pane,kind,agent='codex',agent_pid=int(pid),
                agent_start=float(start),socket_id=paths['sid'],origin=origin,**kw)
        with attached_client(self.SOCK,'note:0'):
            emit('question')  # queued before enable, even without a snapshot
            self.cli('notify','--enable',env=e)
            self.daemon(e)
            self.assertEqual(N.recent_routes(paths),[])
            emit('needs-approval',origin='manual')
            self.daemon(e)
            self.assertEqual(N.recent_routes(paths),[])
            # Initial idle/startup hook is not a completed turn.
            emit('running'); self.daemon(e)
            emit('idle'); self.daemon(e)
            self.assertEqual(N.recent_routes(paths),[])
            # A real visit acknowledges attention; display alone never does.
            T(self.SOCK,'select-pane','-t',pane)
            T(self.SOCK,'select-window','-t',pane)
            emit('needs-approval'); self.daemon(e)
            self.assertEqual(N.recent_routes(paths),[])

    def test_completion_and_disable_enable_watermark(self):
        import unittest.mock
        e, _ = self.fake_open_send()
        self.cli("notify", "--enable", env=e)
        wid, pane = self.start_agent("ncomp")
        with attached_client(self.SOCK, "note") as client:
            # Watch the shell, leaving the agent's new window unseen.
            T(self.SOCK,'select-window','-t','note:0')
            with unittest.mock.patch.dict(os.environ, e, clear=False):
                import lib.tmuxutil as tu
                tu._sid_cache.clear()
            sb, prefix, paths = self.daemon(e)
            gen = sb["panes"][pane]["generation"]
            from lib import store as st
            from lib.tmuxutil import socket_id_of
            sid = socket_id_of(prefix)
            apid, astart = gen.split(":")
            st.append_event(paths, pane, "running", agent="codex",
                            agent_pid=int(apid), agent_start=float(astart),
                            socket_id=sid, action="Thinking", origin="hook")
            sb, _, _ = self.daemon(e)
            self.assertEqual(sb["panes"][pane]["status"], "running")
            st.append_event(paths, pane, "idle", agent="codex",
                            agent_pid=int(apid), agent_start=float(astart),
                            socket_id=sid, completed=True, origin="hook")
            sb, _, _ = self.daemon(e)
            self.assertEqual(sb["panes"][pane]["status"], "idle")
            self.assertTrue(sb["panes"][pane]["unread"])
            self.assertEqual(len(N.recent_routes(paths)), 1)
            self.assertEqual(N.drain(15), 0)
            T(self.SOCK,'select-window','-t','note:0')
            # Restart: same snapshot, no new event -> silent.
            self.cli("daemon", "--once", env=e)
            self.assertEqual(len(N.recent_routes(paths)), 1)
            # Disable: new native event stays silent.
            self.cli("notify", "--disable", env=e)
            st.append_event(paths, pane, "question", agent="codex",
                            agent_pid=int(apid), agent_start=float(astart),
                            socket_id=sid, action="Thinking", origin="hook")
            sb, _, _ = self.daemon(e)
            self.assertEqual(len(N.recent_routes(paths)), 1)
            # Re-enable without a new event stays silent (watermark=now)...
            self.cli("notify", "--enable", env=e)
            sb, _, _ = self.daemon(e)
            self.assertEqual(len(N.recent_routes(paths)), 1)
            # ...but a distinct later native event alerts.
            st.append_event(paths, pane, "needs-approval", agent="codex",
                            agent_pid=int(apid), agent_start=float(astart),
                            socket_id=sid, origin="hook")
            sb, _, _ = self.daemon(e)
            self.assertEqual(len(N.recent_routes(paths)), 2)
            self.assertEqual(N.drain(15), 0)
            T(self.SOCK, "kill-window", "-t", wid)

    def test_no_client_no_actionable(self):
        import unittest.mock
        e, _ = self.fake_open_send()
        self.cli("notify", "--enable", env=e)
        wid, pane = self.start_agent("nlonely")
        self.assertEqual(T(self.SOCK, "list-clients").stdout.strip(), "")
        with unittest.mock.patch.dict(os.environ, e, clear=False):
            import lib.tmuxutil as tu
            tu._sid_cache.clear()
        sb, prefix, paths = self.daemon(e)
        gen = sb["panes"][pane]["generation"]
        from lib import store as st
        from lib.tmuxutil import socket_id_of
        sid = socket_id_of(prefix)
        apid, astart = gen.split(":")
        st.append_event(paths, pane, "question", agent="codex",
                        agent_pid=int(apid), agent_start=float(astart),
                        socket_id=sid, action="Thinking", origin="hook")
        sb, _, _ = self.daemon(e)
        self.assertEqual(N.recent_routes(paths), [])
        self.assertEqual(N.drain(3), 0)
        # Arbitrary --open without a saved route fails.
        r = self.cli("notify", "--open", "--pane", pane, "--generation", gen, env=e)
        self.assertNotEqual(r.returncode, 0)
        T(self.SOCK, "kill-window", "-t", wid)

    def test_daemon_fast_with_long_workers(self):
        import unittest.mock
        marker = "ta-long-n-%d" % os.getpid()
        slow = os.path.join(self.fbin, "notify-send")
        write_exe(slow, "#!/bin/sh\n# %s\nsleep 300\n" % marker)
        write_exe(os.path.join(self.fbin, "terminal-notifier"), "#!/bin/sh\nexit 0\n")
        e = self.env()
        e["TMUX_AGENTS_NOTIFY_SEND"] = slow
        e["TMUX_AGENTS_TERMINAL_NOTIFIER"] = os.path.join(self.fbin, "terminal-notifier")
        e["TMUX_AGENTS_NOTIFY_TIMEOUT"] = "30"
        os.environ.update(e)
        self.cli("notify", "--enable", env=e)
        with unittest.mock.patch.dict(os.environ, e, clear=False):
            import lib.tmuxutil as tu
            tu._sid_cache.clear()
        _, prefix, paths = self.daemon(e)
        wm = N.get_settings(paths)["watermark"]
        cands = [{"key": "slow%d|g|n" % i, "pane": "%%%d" % i, "generation": "g",
                  "status": "question", "notice_key": "n", "ev_t": wm + 10 + i,
                  "session_id": "$1", "session_name": "s",
                  "client": {"name": "c", "pid": 1, "created": "2", "session": "$1"}}
                 for i in range(5)]
        t0 = time.monotonic()
        sent = N.dispatch(prefix, paths, cands, socket_flags=[],
                          script=BIN, state_dir=paths["dir"])
        dt = time.monotonic() - t0
        self.assertEqual(len(sent), N.MAX_WORKERS)
        self.assertLess(dt, 8)
        t0 = time.monotonic()
        with unittest.mock.patch.dict(os.environ, e, clear=False):
            self.mod["daemon_once"](prefix, paths)
        self.assertLess(time.monotonic() - t0, 8)
        N.terminate_all()
        time.sleep(0.5)
        ps = subprocess.run(["ps", "-A", "-o", "command="], capture_output=True,
                            text=True, timeout=10).stdout
        self.assertNotIn(marker, ps)


if __name__ == "__main__":
    unittest.main()
