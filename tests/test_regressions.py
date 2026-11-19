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
                        state_root=lambda:'/tmp/state path',
                        run_tmux=lambda *args: calls.append(args)), \
                patch.object(module['defaults'],'project_name',return_value='fixture'), \
                patch.object(module['defaults'],'get',return_value=dict(provider='claude',worktree=True)):
            menu(['-S', '/tmp/socket path'], '@3', '%9')
        items=calls[-1][6:]
        actions={items[i+1]:items[i+2] for i in range(0,len(items),3)}
        for key,tail in [('c',['launch','--window','@3','--no-worktree','codex']),
                         ('d',['launch','--window','@3']),
                         ('p',['defaults','--window','@3','--client','#{client_name}'])]:
            # Parse tmux's run-shell argument, then its shell argv. Paths and
            # the caller's state root must remain single literal arguments.
            outer=shlex.split(actions[key])
            self.assertEqual(outer[0],'run-shell')
            argv=shlex.split(outer[1])
            self.assertEqual(argv[:2],['env','TMUX_AGENTS_STATE_DIR=/tmp/state path'])
            self.assertIn('/tmp/install path/tmux-agents/bin/tmux-agents',argv)
            self.assertEqual(argv[-len(tail)-2:],['--socket-path','/tmp/socket path',*tail])
