import os
import shutil
import subprocess
import unittest
from pathlib import Path
from lib import detect, status, ui

class DetectionRegressionTest(unittest.TestCase):
    def test_wrapper_arguments_are_not_agents(self):
        self.assertIsNone(detect.classify_command("rtk proxy rg codex ."))
        self.assertIsNone(detect.classify_command("python3 hooks/agent-event --agent codex"))
        self.assertIsNone(detect.classify_command("codex app-server --listen stdio://"))
        self.assertEqual(detect.classify_command("rtk proxy codex --no-daemon"), "codex")
        self.assertEqual(detect.classify_command("node /x/codex.js"), "codex")

    def test_linux_shebang_agent(self):
        self.assertEqual(detect.classify_command('/bin/sh /tmp/tool/codex --no-daemon'), 'codex')
        self.assertEqual(detect.classify_command('/bin/bash /tmp/tool/claude'), 'claude')
        self.assertIsNone(detect.classify_command('/bin/sh /tmp/script codex'))
        self.assertIsNone(detect.classify_command('/bin/sh -s codex'))
        self.assertIsNone(detect.classify_command('/bin/sh -c "rg codex ."'))

    def test_numbered_response_with_composer_is_not_question(self):
        self.assertIsNone(status.screen_attention("Permission details?\n1. First point\n2. Second point\n› "))

    @unittest.skipUnless(shutil.which("node"), "Node not installed")
    def test_opencode_real_bridge(self):
        p = subprocess.run(["node", str(Path(__file__).with_name("opencode-bridge.mjs"))],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)


class WindowSelectionTest(unittest.TestCase):
    def test_window_row_identifies_terminal_to_focus(self):
        rows = ui.build_rows([{"id":"$1", "name":"dev", "windows":[
            {"id":"@2", "index":"1", "name":"code", "panes":[
                {"id":"%5", "agent":"codex", "status":"running", "current":False}]}]}])
        self.assertEqual(next(row for row in rows if row["kind"] == "pane")["focus_pane"], "%5")


class MenuPathTest(unittest.TestCase):
    def test_menu_action_quotes_install_path_and_socket(self):
        import runpy
        import shlex
        from unittest.mock import patch
        module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'bin/tmux-agents'))
        menu = module['do_menu']
        calls = []
        with patch.dict(menu.__globals__, BASE='/tmp/install path/tmux-agents',
                        run_tmux=lambda *args: calls.append(args)):
            menu(['-S', '/tmp/socket path'], '@3', '%9')
        action = calls[0][8]
        # First parse tmux's run-shell argument; then parse its shell argv.
        outer = shlex.split(action)
        self.assertEqual(outer[0], 'run-shell')
        argv = shlex.split(outer[1])
        self.assertIn('/tmp/install path/tmux-agents/bin/tmux-agents', argv)
        self.assertEqual(argv[-6:], ['--socket-path', '/tmp/socket path', 'launch', '--window', '@3', 'codex'])
