"""Regression: closing the last real terminal must not leave a sidebar-only window."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

BIN = str(Path(__file__).resolve().parents[1] / "bin/tmux-agents")

class AgentCloseTest(unittest.TestCase):
    def setUp(self):
        self.socket = "ta-close-" + uuid.uuid4().hex[:12]
        self.home = tempfile.mkdtemp(prefix="ta-close-state-")
        self.env = dict(os.environ, TMUX_AGENTS_STATE_DIR=self.home)
        self.env.pop("TMUX", None)
        self.T("new-session", "-d", "-s", "keep", "-x", "180", "-y", "40", "/bin/sh")
        self.anchor = self.T("display-message", "-p", "#{pane_id}").strip()
        self.anchor_pid = self.T("display-message", "-p", "#{pane_pid}").strip()
        self.anchor_window = self.T("display-message", "-p", "#{window_id}").strip()

    def tearDown(self):
        self.cli("daemon-stop")
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def T(self, *args):
        result = subprocess.run(["tmux", "-L", self.socket, "-f", "/dev/null", *args],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def cli(self, *args):
        result = subprocess.run([sys.executable, BIN, "--socket", self.socket, *args],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def launch_fake_agent(self):
        self.cli("launch", "--window", self.anchor_window, "--exe", "/bin/sh",
                 "codex", "--", "-c", "read reply")
        window = self.T("display-message", "-p", "#{window_id}").strip()
        pane = self.T("display-message", "-p", "#{pane_id}").strip()
        self.assertNotEqual(window, self.anchor_window)
        self.cli("ensure")
        self.assertEqual(len(self.T("list-panes", "-t", window, "-F", "#{pane_id}").split()), 2)
        return window, pane

    def test_normal_agent_exit_removes_its_window_and_preserves_others(self):
        window, pane = self.launch_fake_agent()
        # A real process exits normally after one line; no model/API request.
        self.T("send-keys", "-t", pane, "finish", "Enter")
        self.cli("daemon-start")
        deadline = time.monotonic() + 5
        while window in self.T("list-windows", "-a", "-F", "#{window_id}").split():
            if time.monotonic() > deadline:
                self.fail("agent window remained alive with only its sidebar")
            time.sleep(.1)
        self.assertEqual(self.T("display-message", "-p", "-t", self.anchor, "#{pane_pid}").strip(),
                         self.anchor_pid)

    def test_closing_one_of_multiple_real_panes_keeps_the_window(self):
        window, pane = self.launch_fake_agent()
        second = self.T("split-window", "-d", "-t", pane, "-P", "-F", "#{pane_id}",
                        "/bin/sh").strip()
        second_pid = self.T("display-message", "-p", "-t", second, "#{pane_pid}").strip()
        self.T("kill-pane", "-t", pane)
        self.cli("ensure")
        self.assertIn(window, self.T("list-windows", "-a", "-F", "#{window_id}").split())
        self.assertEqual(self.T("display-message", "-p", "-t", second, "#{pane_pid}").strip(), second_pid)
        self.assertEqual(len(self.T("list-panes", "-t", window, "-F", "#{pane_id}").split()), 2)

    def test_remain_on_exit_terminal_is_preserved(self):
        window, pane = self.launch_fake_agent()
        self.T("set-option", "-t", window, "remain-on-exit", "on")
        self.T("send-keys", "-t", pane, "finish", "Enter")
        deadline = time.monotonic() + 3
        while self.T("display-message", "-p", "-t", pane, "#{pane_dead}").strip() != "1":
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.05)
        self.cli("ensure")
        self.assertIn(window, self.T("list-windows", "-a", "-F", "#{window_id}").split())
        self.assertIn(pane, self.T("list-panes", "-t", window, "-F", "#{pane_id}").split())
