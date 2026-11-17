import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from lib import store, ui

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('ta_names', str(ROOT / 'bin/tmux-agents'))
spec = importlib.util.spec_from_loader('ta_names', loader)
ta = importlib.util.module_from_spec(spec)
loader.exec_module(ta)


class NamesTest(unittest.TestCase):
    def test_live_title_and_custom_precedence(self):
        self.assertEqual(ui.pane_name('', '⠹ Fix login | app', 'codex', '/work/app'), 'Fix login')
        self.assertEqual(ui.pane_name('My task', 'Generated title', 'claude'), 'My task')
        self.assertEqual(ui.pane_name('', 'Codex', 'codex'), 'codex')
        self.assertEqual(ui.pane_name('', '/work/app', '', '/work/app'), 'shell')
        self.assertEqual(ui.pane_name('', 'Investigate error', 'opencode'), 'Investigate error')
        self.assertEqual(ui.pane_name('', '\x1bBad\x07 title', 'codex'), 'Bad title')

    def test_rename_and_automatic_titles_on_real_server(self):
        with tempfile.TemporaryDirectory(prefix='ta-names-') as tmp:
            prefix = ['-S', str(Path(tmp) / 'socket')]
            def tmux(*args):
                return ta.run_tmux(prefix, *args)
            try:
                rc, _, error = tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'names', 'sleep', '30')
                self.assertEqual(rc, 0, error)
                pane = tmux('display-message', '-p', '#{pane_id}')[1].strip()
                session = tmux('display-message', '-p', '#{session_id}')[1].strip()
                window = tmux('display-message', '-p', '#{window_id}')[1].strip()
                paths = {'state': str(Path(tmp) / 'state.json')}
                store.atomic_write(paths['state'], {'panes': {pane: {'agent': 'codex', 'status': 'running'}}})
                tmux('select-pane', '-t', pane, '-T', '⠋ Fix login | app')
                def name():
                    return ta.build_sessions(prefix, paths)[0]['windows'][0]['panes'][0]['display_name']
                self.assertEqual(name(), 'Fix login | app')
                nickname = 'Login "fix"; $(touch NO) #{pane_id}'
                self.assertEqual(ta.rename_target(prefix, 'pane', pane, nickname)[0], 0)
                self.assertEqual(name(), nickname)
                tmux('select-pane', '-t', pane, '-T', 'New title')
                self.assertEqual(name(), nickname)
                self.assertEqual(ta.rename_target(prefix, 'pane', pane, '')[0], 0)
                self.assertEqual(name(), 'New title')
                self.assertEqual(ta.rename_target(prefix, 'session', session, 'renamed project')[0], 0)
                self.assertEqual(tmux('display-message', '-p', '-t', pane, '#{session_name}')[1].strip(), 'renamed project')
                self.assertEqual(ta.rename_target(prefix, 'window', window, 'work item')[0], 0)
                self.assertEqual(tmux('display-message', '-p', '-t', pane, '#{window_name}')[1].strip(), 'work item')
                self.assertNotEqual(ta.rename_target(prefix, 'session', session, '')[0], 0)
                import sys
                result = subprocess.run([sys.executable, str(ROOT / 'bin/tmux-agents'),
                                         '--socket-path', prefix[1], 'rename', '--session', session, ''],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                result = subprocess.run([sys.executable, str(ROOT / 'bin/tmux-agents'),
                                         '--socket-path', prefix[1], 'rename', '--pane', pane, 'CLI name'],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(name(), 'CLI name')
            finally:
                tmux('kill-server')
