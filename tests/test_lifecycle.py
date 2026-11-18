"""Isolated tmux lifecycle tests. EVERYTHING namespaced:

- private server (-L sock, -f /dev/null: production hooks never load)
- isolated state root ($TMUX_AGENTS_STATE_DIR under a temp HOME)
so live production state is never touched.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(BASE, "bin", "tmux-agents")


def T(sock, *args, timeout=10):
    p = subprocess.run(["tmux", "-L", sock, "-f", "/dev/null", *args],
                       capture_output=True, text=True, timeout=timeout)
    return p


class Iso(unittest.TestCase):
    SOCK = "ta-life"
    home = None

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="ta-home-")
        T(cls.SOCK, "kill-server")
        time.sleep(0.2)
        r = T(cls.SOCK, "new-session", "-d", "-s", "life", "-x", "200", "-y", "50")
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls):
        T(cls.SOCK, "kill-server")
        shutil.rmtree(cls.home, ignore_errors=True)

    def env(self):
        e = dict(os.environ)
        e["HOME"] = self.home
        e["TMUX_AGENTS_STATE_DIR"] = os.path.join(self.home, "state")
        e.pop("TMUX", None)
        e.pop("TMUX_PANE", None)
        return e

    def cli(self, *args, timeout=30):
        return subprocess.run([sys.executable, BIN, "--socket", self.SOCK,
                               *args], capture_output=True, text=True,
                              timeout=timeout, env=self.env())

    def panes(self):
        return T(self.SOCK, "list-panes", "-a", "-F", "#{pane_id}").stdout.split()

    def test_01_state_isolated_from_production(self):
        # Model the default state location inside our temporary HOME. A real
        # user's daemon may legitimately update their state during this test.
        prod = os.path.join(self.home, ".local", "state", "tmux-agents", "sentinel")
        os.makedirs(prod)
        sentinel = os.path.join(prod, "state.json")
        with open(sentinel, "w") as f:
            f.write('{"sentinel":true}')
        before = os.stat(sentinel).st_mtime_ns
        self.cli("daemon", "--once")
        subs = os.listdir(os.path.join(self.home, "state"))
        self.assertEqual(len(subs), 1, subs)
        stfile = os.path.join(self.home, "state", subs[0], "state.json")
        self.assertTrue(os.path.exists(stfile), subs)
        self.cli("daemon", "--once")
        self.assertEqual(before, os.stat(sentinel).st_mtime_ns)
        with open(sentinel) as f:
            self.assertEqual(f.read(), '{"sentinel":true}')
        self.assertEqual(os.listdir(os.path.dirname(prod)), ["sentinel"])
        self.assertNotIn("default", os.listdir(os.path.join(self.home, "state")))

    def test_02_ensure_no_dup_no_steal(self):
        before = T(self.SOCK, "display-message", "-p", "-t", "life",
                   "#{pane_id}").stdout.strip()
        n0 = len(self.panes())
        self.assertEqual(self.cli("ensure").returncode, 0)
        time.sleep(2.0)
        self.assertEqual(self.cli("ensure").returncode, 0)
        time.sleep(1.0)
        after = T(self.SOCK, "display-message", "-p", "-t", "life",
                  "#{pane_id}").stdout.strip()
        self.assertEqual(before, after)
        self.assertEqual(len(self.panes()), n0 + 1)

    def test_03_parallel_ensure_single_sidebar(self):
        ths = [threading.Thread(target=self.cli, args=("ensure",)) for _ in range(6)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        time.sleep(2.0)
        self.assertEqual(len(self.panes()), 2, self.panes())

    def test_04_new_window_gets_sidebar(self):
        T(self.SOCK, "new-window", "-t", "life", "-n", "second")
        time.sleep(0.3)
        self.assertEqual(self.cli("ensure").returncode, 0)
        time.sleep(2.0)
        wins = T(self.SOCK, "list-windows", "-t", "life", "-F",
                 "#{window_index}").stdout.split()
        self.assertGreaterEqual(len(wins), 2, wins)
        for w in wins:
            ps = T(self.SOCK, "list-panes", "-t", "life:%s" % w,
                   "-F", "#{pane_id}").stdout.split()
            self.assertEqual(len(ps), 2, (w, ps))

    def test_05_toggle_genuine(self):
        # hide current window: sidebar killed and NOT recreated by ensure/daemon
        T(self.SOCK, "select-window", "-t", "life:1")
        self.assertIn("hidden", self.cli("hide").stdout)
        time.sleep(0.5)
        n = len(T(self.SOCK, "list-panes", "-t", "life:1", "-F", "#{pane_id}").stdout.split())
        self.assertEqual(n, 1)
        self.cli("ensure")
        self.cli("daemon", "--once")
        time.sleep(1.0)
        n = len(T(self.SOCK, "list-panes", "-t", "life:1", "-F", "#{pane_id}").stdout.split())
        self.assertEqual(n, 1, "hidden window must stay hidden")
        self.assertIn("shown", self.cli("toggle").stdout)
        time.sleep(2.0)
        n = len(T(self.SOCK, "list-panes", "-t", "life:1", "-F", "#{pane_id}").stdout.split())
        self.assertEqual(n, 2)

    def test_06_focus_exact_sidebar(self):
        self.assertEqual(self.cli("focus").returncode, 0)
        time.sleep(1.0)
        active = T(self.SOCK, "display-message", "-p", "-t", "life",
                   "#{pane_id}").stdout.strip()
        self.assertTrue(active.startswith("%"))
        # active pane must be a sidebar (its tree contains tmux-agents sidebar)
        panes = T(self.SOCK, "list-panes", "-a", "-F",
                  "#{pane_id} #{pane_pid}").stdout.splitlines()
        row = [l for l in panes if l.startswith(active + " ")][0]
        pid = int(row.split()[1])
        tree = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,command="],
                              capture_output=True, text=True).stdout
        # walk up: pane pid chain must mention tmux-agents sidebar
        found, cur = False, pid
        bypid = {}
        for line in tree.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) == 3:
                try:
                    bypid[int(parts[0])] = (int(parts[1]), parts[2])
                except ValueError:
                    pass
        for _ in range(8):
            if cur not in bypid:
                break
            ppid, cmd = bypid[cur]
            if "tmux-agents" in cmd and "sidebar" in cmd:
                found = True
                break
            cur = ppid
        self.assertTrue(found, "focused pane is not the sidebar")

    def test_07_launch_creates_window_with_argv_cwd(self):
        d = tempfile.mkdtemp(prefix="ta-cwd-")
        self.addCleanup(shutil.rmtree, d, True)
        agent = os.path.join(d, "fakeagent.sh")
        with open(agent, "w") as f:
            f.write("#!/bin/sh\n"
                    'echo "CWD=$PWD" >> "$TA_OUT"\n'
                    'i=0; for a in "$@"; do echo "ARG$i=$a" >> "$TA_OUT"; i=$((i+1)); done\n'
                    "sleep 30\n")
        os.chmod(agent, 0o755)
        out = os.path.join(d, "got.txt")
        e = self.env()
        e["TA_OUT"] = out
        # cd a real window's shell so launch inherits its cwd
        first = T(self.SOCK, "list-windows", "-t", "life", "-F",
                  "#{window_index}").stdout.split()[0]
        T(self.SOCK, "send-keys", "-t", "life:%s" % first, "cd %s" % d, "Enter")
        time.sleep(1.0)
        wid = T(self.SOCK, "display-message", "-p", "-t", "life:%s" % first,
                "#{window_id}").stdout.strip()
        self.assertTrue(wid.startswith("@"))
        before = set(self.panes())
        r = subprocess.run([sys.executable, BIN, "--socket", self.SOCK, "launch",
                            "--window", wid, "--exe", agent,
                            "--env", "TA_OUT=%s" % out,
                            "codex", "--",
                            "hello world", "a;b&c", "don't", "-n", "5"],
                           capture_output=True, text=True, timeout=30, env=e)
        self.assertEqual(r.returncode, 0, r.stderr)
        time.sleep(2.0)
        after = set(self.panes())
        self.assertTrue(after - before, "launch must create a new pane/window")
        with open(out) as f:
            got = f.read()
        # pane_current_path resolves the /var -> /private/var symlink.
        self.assertIn("CWD=%s" % os.path.realpath(d), got)
        self.assertIn("ARG0=hello world", got)
        self.assertIn("ARG1=a;b&c", got)
        self.assertIn("ARG2=don't", got)
        self.assertIn("ARG3=-n", got)
        self.assertIn("ARG4=5", got)

    def test_08_exit_marks_done_then_clears(self):
        # Fake agent binary named `codex`: prints (observed activity ->
        # running), then exits; remain-on-exit keeps the pane -> done.
        d = tempfile.mkdtemp(prefix="ta-agent-")
        self.addCleanup(shutil.rmtree, d, True)
        agent = os.path.join(d, "codex")
        with open(agent, "w") as f:
            f.write("#!/bin/sh\nfor i in 1 2 3 4 5 6; do echo tick $i; sleep 1; done\nexit 0\n")
        os.chmod(agent, 0o755)
        T(self.SOCK, "new-window", "-d", "-t", "life", "-n", "tmpagent")
        wid = T(self.SOCK, "display-message", "-p", "-t", "life:tmpagent",
                "#{window_id}").stdout.strip()
        self.assertTrue(wid.startswith("@"), wid)
        T(self.SOCK, "set-option", "-t", wid, "remain-on-exit", "on")
        T(self.SOCK, "respawn-pane", "-t", wid, "-k", agent)

        def poll():
            return json.loads(subprocess.run(
                [sys.executable, BIN, "--socket", self.SOCK, "daemon", "--once"],
                capture_output=True, text=True, timeout=30,
                env=self.env()).stdout)

        def pane_id():
            return T(self.SOCK, "display-message", "-p", "-t", wid,
                     "#{pane_id}").stdout.strip()

        target = pane_id()
        self.assertTrue(target.startswith("%"), target)
        seen_running = False
        for _ in range(10):
            time.sleep(1.0)
            sb = poll()
            st = sb["panes"].get(target, {}).get("status", "")
            if st == "running":
                seen_running = True
                break
        self.assertTrue(seen_running, "agent activity must read as running")
        self.assertEqual(poll()["panes"][target]["status"] in ("running", "done"), True)
        # wait for the agent to exit on its own; pane persists (remain-on-exit)
        done = False
        for _ in range(12):
            time.sleep(1.0)
            sb = poll()
            if sb["panes"].get(target, {}).get("status") == "done":
                done = True
                break
        self.assertTrue(done, "exited agent must read done, never stuck running")
        # Kill by stable window id (window auto-renames to the command).
        self.assertEqual(T(self.SOCK, "kill-window", "-t", wid).returncode, 0)
        time.sleep(0.5)
        sb = poll()
        self.assertNotIn(target, sb["panes"], "vanished panes clear from state")


    def test_09_namespace_equivalence_and_hook_e2e(self):
        # -L name, -S path, and inherited realistic $TMUX all resolve to ONE
        # namespace for the same server.
        import sys as _sys
        _sys.path.insert(0, BASE)
        from lib import tmuxutil as tu
        tu._sid_cache.clear()
        canon = T(self.SOCK, "display-message", "-p",
                  "#{socket_path}").stdout.strip()
        self.assertTrue(canon.startswith("/"), canon)
        _, sid_l = tu.resolve_socket(explicit_name=self.SOCK)
        tu._sid_cache.clear()
        _, sid_s = tu.resolve_socket(explicit_path=canon)
        old = os.environ.get("TMUX")
        try:
            os.environ["TMUX"] = "%s,7,0" % canon
            tu._sid_cache.clear()
            prefix, sid_t = tu.resolve_socket()
        finally:
            if old is None:
                os.environ.pop("TMUX", None)
            else:
                os.environ["TMUX"] = old
            tu._sid_cache.clear()
        self.assertEqual(prefix, ["-S", canon])
        self.assertEqual(sid_l, sid_s)
        self.assertEqual(sid_l, sid_t)
        # Hook script end-to-end: Stop payload + TMUX_PANE -> event log ->
        # daemon snapshot reads idle/event for the fake agent.
        d = tempfile.mkdtemp(prefix="ta-hooke2e-")
        self.addCleanup(shutil.rmtree, d, True)
        agent = os.path.join(d, "codex")
        with open(agent, "w") as f:
            f.write("#!/bin/sh\nwhile true; do echo tick; sleep 1; done\n")
        os.chmod(agent, 0o755)
        T(self.SOCK, "new-window", "-d", "-t", "life", "-n", "hooke2e", agent)
        time.sleep(1.0)
        hwid = T(self.SOCK, "display-message", "-p", "-t", "life:hooke2e",
                 "#{window_id}").stdout.strip()
        self.assertTrue(hwid.startswith("@"), hwid)
        pane = T(self.SOCK, "display-message", "-p", "-t", hwid,
                 "#{pane_id}").stdout.strip()
        hook = os.path.join(BASE, "hooks", "agent-event")
        e = self.env()
        e["TMUX"] = "%s,7,0" % canon
        payload = json.dumps({"hook_event_name": "Stop", "session_id": "s"})
        p = subprocess.run([sys.executable, hook, "--agent", "codex"],
                           input=payload, capture_output=True, text=True,
                           timeout=30, env={**e, "TMUX_PANE": pane})
        self.assertEqual(p.returncode, 0, p.stderr)
        sb = json.loads(subprocess.run(
            [sys.executable, BIN, "--socket", self.SOCK, "daemon", "--once"],
            capture_output=True, text=True, timeout=30,
            env=self.env()).stdout)
        info = sb["panes"].get(pane, {})
        self.assertEqual(info.get("status"), "idle", info)
        self.assertEqual(info.get("source"), "event", info)
        self.assertEqual(info.get("agent"), "codex", info)
        self.assertEqual(T(self.SOCK, "kill-window", "-t", hwid).returncode, 0)


if __name__ == "__main__":
    unittest.main()
