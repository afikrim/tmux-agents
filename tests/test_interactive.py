"""Interactive sidebar over a PTY on an isolated server.

Spawns `sidebar` attached to a pty, drives keys/mouse, asserts real
navigation without screenshots: active pane/window/session on the test server
must equal the EXACT row targeted (parsed from the rendered tree), never a
weak membership check. Control-mode clients verify cross-session switching.
"""
import fcntl
import termios
import struct
import importlib.util
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(BASE, "bin", "tmux-agents")
SOCK = "ta-pty-%d" % os.getpid()

import importlib.machinery
_loader = importlib.machinery.SourceFileLoader("tmux_agents_bin", BIN)
SPEC = importlib.util.spec_from_loader("tmux_agents_bin", _loader)
ta = importlib.util.module_from_spec(SPEC)
_loader.exec_module(ta)

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-Z]")
PANE_RE = re.compile(r"^   [ >] .*?\s+(?:Running|Input|Approval|Question|Done|Idle|Unknown)[=#~?]?\s*$")


def T(*args, timeout=10):
    return subprocess.run(["tmux", "-L", SOCK, "-f", "/dev/null", *args],
                          capture_output=True, text=True, timeout=timeout)


def drain(fd, seconds=1.5):
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                out += os.read(fd, 65536)
            except OSError:
                break
        elif out:
            break
    return out


def visible_rows(raw):
    """Map the last rendered frame to independent real-server pane order.

    Pane IDs are intentionally hidden. The fixture's visible shell rows must
    correspond to tmux's real pane order, which supplies exact click targets.
    """
    text = ANSI_RE.sub("", raw.decode("utf-8", "replace").rsplit("\x1b[H", 1)[-1])
    real = T("list-panes", "-s", "-t", "pty1", "-F",
             "#{pane_id}|#{@tmux_agents_test_agent}").stdout.splitlines()
    targets = iter(line.split("|")[0] for line in real if line.endswith("|1"))
    rows = []
    for y, line in enumerate(text.splitlines(), start=1):
        if PANE_RE.match(line):
            rows.append((y, "pane", next(targets)))
        elif line.strip():
            rows.append((y, "other", line.strip()[:24]))
    return rows


def visible_pane_rows(raw):
    return [ref for _y, kind, ref in visible_rows(raw) if kind == "pane"]


class SidebarPTYTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="ta-pty-home-")
        cls.agent=os.path.join(cls.home,'codex')
        with open(cls.agent,'w') as f:
            f.write('#!/bin/sh\necho "› "\nwhile true; do sleep 1; done\n')
        os.chmod(cls.agent,0o755)
        T("kill-server")
        time.sleep(0.2)
        T("new-session", "-d", "-s", "pty1", "-x", "200", "-y", "50",cls.agent)
        T("new-window", "-t", "pty1", "-n", "other",cls.agent)
        for pane in T('list-panes','-s','-t','pty1','-F','#{pane_id}').stdout.splitlines():
            T('set-option','-p','-t',pane,'@tmux_agents_test_agent','1')
        T('set-option','-p','-t','pty1:1','@tmux_agents_name','other')
        env = dict(os.environ)
        env["TMUX_AGENTS_STATE_DIR"] = os.path.join(cls.home, "state")
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        subprocess.run([sys.executable, BIN, "--socket", SOCK, "ensure"],
                       env=env, timeout=30)
        time.sleep(2.0)
        subprocess.run([sys.executable, BIN, "--socket", SOCK, "daemon",
                        "--once"], env=env, timeout=30)

    @classmethod
    def tearDownClass(cls):
        T("kill-server")
        shutil.rmtree(cls.home, ignore_errors=True)

    def spawn_sidebar(self, observing_pane=None):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 80, 0, 0))
        env = dict(os.environ)
        env["TMUX_AGENTS_STATE_DIR"] = os.path.join(self.home, "state")
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env["TERM"] = "xterm-256color"
        if observing_pane:
            env["TMUX_PANE"] = observing_pane
        proc = subprocess.Popen(
            [sys.executable, BIN, "--socket", SOCK, "sidebar"],
            stdin=slave, stdout=slave, stderr=subprocess.DEVNULL,
            env=env, close_fds=True)
        os.close(slave)
        return proc, master

    def close(self, proc, master):
        try:
            os.close(master)
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    def test_q_returns_focus_keeps_sidebar_alive(self):
        proc, master = self.spawn_sidebar()
        try:
            output, deadline = b'', time.monotonic()+4
            before=[]
            while time.monotonic()<deadline and not before:
                output+=drain(master,.3)
                before=visible_pane_rows(output)
            self.assertTrue(before, "sidebar must render pane rows")
            os.write(master, b"q")
            time.sleep(1.5)
            # Sidebar process stays alive; focus moved back to a user pane.
            self.assertIsNone(proc.poll(), "q must not kill the sidebar UI")
            active = T("display-message", "-p", "-t", "pty1",
                       "#{pane_id}").stdout.strip()
            self.assertTrue(active.startswith("%"), active)
        finally:
            self.close(proc, master)

    def test_keyboard_G_enter_activates_exact_last_row(self):
        proc, master = self.spawn_sidebar()
        try:
            # Startup escape sequences can arrive before the first frame,
            # especially on Linux under load. Wait for actual navigable rows.
            out, rows, deadline = b'', [], time.monotonic()+5
            while len(rows)<2 and time.monotonic()<deadline:
                out+=drain(master,.25)
                rows=visible_pane_rows(out)
            self.assertGreaterEqual(len(rows), 2, rows)
            target = rows[-1]  # G jumps to the last row
            os.write(master, b"G")
            # A terminal continuously reads its PTY. Drain the highlighted
            # full-color frame before Enter so stdout cannot fill the small
            # test PTY buffer and prevent the UI reading the next key.
            drain(master, .6)
            os.write(master, b"\r")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                drain(master, .1)
                active = T("display-message", "-p", "-t", "pty1",
                           "#{pane_id}").stdout.strip()
                if active == target:
                    break
            self.assertEqual(active, target,
                             "Enter must activate exactly the cursor row")
        finally:
            self.close(proc, master)

    def test_down_moves_from_session_directly_to_agent(self):
        proc, master = self.spawn_sidebar()
        try:
            out = drain(master, 2.5)
            panes = visible_pane_rows(out)
            self.assertGreaterEqual(len(panes), 2)
            # Keep window-name rows visible, but one down movement from the
            # session must select its first real terminal, not a window row.
            os.write(master, b"gj\r")
            time.sleep(.8)
            active = T("display-message", "-p", "-t", "pty1", "#{pane_id}").stdout.strip()
            self.assertEqual(active, panes[0])
        finally:
            self.close(proc, master)

    def test_mouse_click_activates_exact_row(self):
        proc, master = self.spawn_sidebar()
        try:
            out = drain(master, 2.5)
            rows = visible_rows(out)
            panes = [(y, ref) for y, kind, ref in rows if kind == "pane"]
            self.assertGreaterEqual(len(panes), 2, rows)
            y, target = panes[1]
            os.write(master, ("\x1b[<0;2;%dM" % y).encode())
            time.sleep(1.5)
            active = T("display-message", "-p", "-t", "pty1",
                       "#{pane_id}").stdout.strip()
            self.assertEqual(active, target,
                             "mouse click must navigate to the exact row")
        finally:
            self.close(proc, master)

    def test_rename_conversation_save_cancel_and_reset(self):
        proc, master = self.spawn_sidebar()
        try:
            out = drain(master, 2.5)
            pane = visible_pane_rows(out)[0]
            os.write(master, b"gjr\x15Fix login flow\r")
            drain(master, 1.0)
            self.assertEqual(T("show-options", "-p", "-t", pane, "-v", "@tmux_agents_name").stdout.strip(), "Fix login flow")
            rendered = ANSI_RE.sub("", drain(master, 1.1).decode())
            self.assertIn("Fix login flow", rendered)
            os.write(master, b"r\x15Do not save")
            drain(master, .4)
            os.write(master, b"\x1b")
            drain(master, .5)
            self.assertEqual(T("show-options", "-p", "-t", pane, "-v", "@tmux_agents_name").stdout.strip(), "Fix login flow")
            os.write(master, b"r\x15\r")
            drain(master, .6)
            self.assertEqual(T("show-options", "-p", "-t", pane, "-v", "@tmux_agents_name").stdout.strip(), "")
            os.write(master, b"R\x15Renamed workspace\r")
            drain(master, .6)
            self.assertEqual(T("display-message", "-p", "-t", pane, "#{window_name}").stdout.strip(), "Renamed workspace")
        finally:
            self.close(proc, master)

    def test_focused_long_title_animates_without_keypress(self):
        real = [line.split("|")[0] for line in T("list-panes", "-s", "-t", "pty1", "-F",
                "#{pane_id}|#{@tmux_agents_sidebar}").stdout.splitlines() if not line.endswith("|1")]
        pane = real[0]
        T("set-option", "-p", "-t", pane, "@tmux_agents_name",
          "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 4)
        proc, master = self.spawn_sidebar()
        try:
            drain(master, 1)
            os.write(master, b"gj")
            early = ANSI_RE.sub("", drain(master, .5).decode())
            late = ANSI_RE.sub("", drain(master, 3).decode())
            self.assertIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ", early)
            self.assertNotEqual(early.rsplit("tmux agents", 1)[-1],
                                late.rsplit("tmux agents", 1)[-1])
            self.assertIsNone(proc.poll())
        finally:
            self.close(proc, master)
            T("set-option", "-pu", "-t", pane, "@tmux_agents_name")

    def test_project_keyboard_preview_changes_agents_without_switching_session(self):
        T("new-session", "-d", "-s", "zz-project", "-n", "review", "sleep", "30")
        proc, master = self.spawn_sidebar()
        try:
            drain(master, 1.5)
            os.write(master, b"g")
            first = ANSI_RE.sub("", drain(master, .8).decode())
            self.assertIn("Agents / pty1", first)
            self.assertIn("other", first)
            before = T("display-message", "-p", "#{session_id}").stdout.strip()
            os.write(master, b"j")
            second = ANSI_RE.sub("", drain(master, .8).decode())
            self.assertIn("Agents / zz-project", second)
            self.assertNotIn("other", second)
            self.assertEqual(T("display-message", "-p", "#{session_id}").stdout.strip(), before)
        finally:
            self.close(proc, master)
            T("kill-session", "-t", "zz-project")

    def test_selection_follows_external_clicks_and_keeps_focused_keyboard_preview(self):
        import signal
        topology = [line.split('|') for line in T('list-panes','-s','-t','pty1','-F',
                     '#{pane_id}|#{window_id}|#{@tmux_agents_sidebar}').stdout.splitlines()]
        real = [row for row in topology if row[2] != '1']
        first, second = real[:2]
        observer = next(row[0] for row in topology if row[1] == second[1] and row[2] == '1')
        T('select-pane','-t',second[0])
        T('select-window','-t',first[1])
        T('select-pane','-t',first[0])
        proc, master = self.spawn_sidebar(observer)
        def selected_ref(raw):
            text = ANSI_RE.sub('', raw.decode('utf-8','replace').rsplit('\x1b[H',1)[-1]).splitlines()
            return next((ref for y,kind,ref in visible_rows(raw)
                         if kind == 'pane' and text[y-1].startswith('   >')), None)
        try:
            output, deadline = b'', time.monotonic() + 4
            while time.monotonic() < deadline:
                output += drain(master,.3)
                if selected_ref(output) == first[0]:
                    break
            self.assertEqual(selected_ref(output),first[0],output.decode('utf-8','replace'))
            T('select-window','-t',second[1])
            T('select-pane','-t',second[0])

            os.kill(proc.pid,signal.SIGWINCH)
            self.assertEqual(selected_ref(drain(master,.6)),second[0])
            T('select-pane','-t',observer)
            os.kill(proc.pid,signal.SIGWINCH)
            drain(master,.4)
            os.write(master,b'g')
            preview=ANSI_RE.sub('',drain(master,.5).decode())
            self.assertIn('> pty1',preview)
            os.kill(proc.pid,signal.SIGWINCH)
            self.assertIn('> pty1',ANSI_RE.sub('',drain(master,.4).decode()))
            # This sidebar's pane remains active within its old window, but
            # another window is now visible: pane_active alone is insufficient.
            T('select-window','-t',first[1])
            T('select-pane','-t',first[0])
            os.kill(proc.pid,signal.SIGWINCH)
            self.assertEqual(selected_ref(drain(master,.6)),first[0])
        finally:
            self.close(proc,master)
            T('select-pane','-t',second[0])

    def test_split_agents_have_separate_click_targets_and_shell_window_is_hidden(self):
        first=T('list-panes','-t','pty1:0','-F','#{pane_id}|#{@tmux_agents_test_agent}').stdout.splitlines()
        anchor=next(line.split('|')[0] for line in first if line.endswith('|1'))
        split=T('split-window','-h','-d','-P','-F','#{pane_id}','-t',anchor,self.agent).stdout.strip()
        T('set-option','-p','-t',split,'@tmux_agents_test_agent','1')
        T('set-option','-p','-t',split,'@tmux_agents_name','Split agent')
        ordinary=T('new-window','-d','-P','-F','#{window_id}','-t','pty1','-n','Plain editor','sleep','60').stdout.strip()
        env=dict(os.environ,TMUX_AGENTS_STATE_DIR=os.path.join(self.home,'state'))
        env.pop('TMUX',None); env.pop('TMUX_PANE',None)
        subprocess.run([sys.executable,BIN,'--socket',SOCK,'daemon','--once'],env=env,
                       check=True,stdout=subprocess.DEVNULL)
        proc,master=self.spawn_sidebar()
        try:
            raw=drain(master,2)
            entries=[(y,ref) for y,kind,ref in visible_rows(raw) if kind=='pane']
            self.assertEqual(len(entries),3)
            self.assertIn(split,[ref for _,ref in entries])
            self.assertEqual([entries[i+1][0]-entries[i][0] for i in range(2)],[3,3])
            self.assertNotIn('Plain editor',ANSI_RE.sub('',raw.decode()))
            y=next(y for y,ref in entries if ref==split)
            os.write(master,('\x1b[<0;5;%dM'%y).encode())
            drain(master,.6)
            self.assertEqual(T('display-message','-p','-t','pty1','#{pane_id}').stdout.strip(),split)
        finally:
            self.close(proc,master)
            T('kill-pane','-t',split)
            T('kill-window','-t',ordinary)

    def test_activation_plan_cross_session_exact(self):
        # Pure plan: exact argv including origin client, then executed for real
        # against a control-mode client attached to session A.
        T("new-session", "-d", "-s", "ptyB", "-x", "200", "-y", "50")
        ctl_out = open(os.path.join(self.home, "ctl.out"), "wb")
        self.addCleanup(lambda: ctl_out.close())
        ctl = subprocess.Popen(
            ["tmux", "-L", SOCK, "-f", "/dev/null", "-C", "attach-session",
             "-t", "pty1"],
            stdin=subprocess.PIPE, stdout=ctl_out,
            stderr=subprocess.DEVNULL)
        try:
            ctl_names = []
            for _ in range(20):
                time.sleep(0.5)
                clients = T("list-clients", "-F",
                            "#{client_name} #{client_control_mode}").stdout.splitlines()
                ctl_names = [l.split()[0] for l in clients if l.strip().endswith(" 1")]
                if ctl_names or ctl.poll() is not None:
                    break
            self.assertTrue(ctl_names, clients)
            cname = ctl_names[0]
            ids = dict(l.split() for l in
                       T("list-sessions", "-F", "#{session_name} #{session_id}")
                       .stdout.splitlines())
            row = {"kind": "session", "target": ("session", ids["ptyB"]),
                   "sess_id": ids["ptyB"], "ref": ids["ptyB"]}
            plan = ta.activation_plan(row, origin_session=ids["pty1"],
                                      client=cname)
            self.assertEqual(plan, [["switch-client", "-c", cname,
                                     "-t", ids["ptyB"]]])
            prefix = ["-L", SOCK]
            for argv in plan:
                rc, _, err = ta.run_tmux(prefix, *argv)
                self.assertEqual(rc, 0, err)
            time.sleep(0.5)
            cur = T("display-message", "-c", cname, "-p",
                    "#{session_name}").stdout.strip()
            self.assertEqual(cur, "ptyB")
            # And back, proving the client context is honored both ways.
            for argv in ta.activation_plan(
                    {"kind": "session", "target": ("session", ids["pty1"]),
                     "sess_id": ids["pty1"], "ref": ids["pty1"]},
                    origin_session=ids["ptyB"], client=cname):
                ta.run_tmux(prefix, *argv)
            time.sleep(0.5)
            cur = T("display-message", "-c", cname, "-p",
                    "#{session_name}").stdout.strip()
            self.assertEqual(cur, "pty1")
        finally:
            ctl.terminate()
            ctl.wait(timeout=5)
            ctl.stdin.close()
            T("kill-session", "-t", "ptyB")


if __name__ == "__main__":
    unittest.main()
