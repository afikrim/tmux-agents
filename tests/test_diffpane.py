import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import changes, diffpane
from lib.tmuxutil import state_paths

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('ta_diff_tests', str(ROOT / 'bin/tmux-agents'))
spec = importlib.util.spec_from_loader('ta_diff_tests', loader)
ta = importlib.util.module_from_spec(spec)
loader.exec_module(ta)


class DiffPaneTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ta-diffpane-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.repo = self.directory / 'repo with spaces'
        self.repo.mkdir()
        self.prefix = ['-S', str(self.directory / 'socket')]
        self.environment = patch.dict(os.environ, TMUX_AGENTS_STATE_DIR=str(self.directory / 'state'), TMUX='')
        self.environment.start()
        self.addCleanup(self.environment.stop)
        changes.git(str(self.repo), 'init', '-q')
        changes.git(str(self.repo), 'config', 'user.name', 'Test')
        changes.git(str(self.repo), 'config', 'user.email', 'test@example.test')
        (self.repo / 'code').write_text('original\n')
        changes.git(str(self.repo), 'add', '.')
        changes.git(str(self.repo), 'commit', '-qm', 'base')
        agent = self.directory / 'codex'
        agent.write_text('#!/bin/sh\nwhile true; do echo tick; sleep 1; done\n')
        agent.chmod(0o755)
        rc, _, error = self.tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'diff',
                                 '-x', '180', '-y', '40', '-c', str(self.repo), str(agent))
        self.assertEqual(rc, 0, error)
        self.addCleanup(lambda: self.tmux('kill-server'))
        self.target = self.tmux('display-message', '-p', '#{pane_id}')[1].strip()
        self.window = self.tmux('display-message', '-p', '#{window_id}')[1].strip()
        self.pid = self.tmux('display-message', '-p', '#{pane_pid}')[1].strip()
        self.paths = state_paths(self.prefix)
        ta.ensure_sidebars(self.prefix, self.paths)

    def tmux(self, *args):
        return ta.run_tmux(self.prefix, *args)

    def toggle(self):
        return ta.cmd_diff(SimpleNamespace(pane=self.target, window=self.window), self.prefix)

    def viewer(self):
        return next((p['pane_id'] for p in ta.list_all(self.prefix) if p.get('diff')), '')

    def wait_for(self, predicate, seconds=6):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.1)
        self.fail('Timed out waiting for diff pane state')

    def test_toggle_render_checkpoint_q_and_infrastructure_exclusion(self):
        (self.repo / 'code').write_text('original\nnew change\n')
        self.assertEqual(self.toggle(), 0)
        viewer = self.viewer()
        self.assertTrue(viewer)
        self.wait_for(lambda: '+new change' in self.tmux('capture-pane', '-p', '-t', viewer)[1])
        self.assertIn('Working tree', self.tmux('capture-pane', '-p', '-t', viewer)[1])
        sessions = ta.build_sessions(self.prefix, self.paths)
        self.assertEqual([p['id'] for p in sessions[0]['windows'][0]['panes']], [self.target])
        self.assertNotIn(viewer, ta.daemon_once(self.prefix, self.paths)['panes'])
        left = next(p for p in ta.list_all(self.prefix) if p.get('sidebar'))
        self.assertEqual(self.tmux('display-message', '-p', '-t', left['pane_id'], '#{pane_width}')[1].strip(), '32')
        self.tmux('send-keys', '-t', viewer, 'b')
        self.wait_for(lambda: Path(changes.checkpoint_path(self.paths, self.target)).exists())
        (self.repo / 'code').write_text('original\nnew change\nnext edit\n')
        self.wait_for(lambda: '+next edit' in self.tmux('capture-pane', '-p', '-t', viewer)[1])
        self.assertIn('Since checkpoint', self.tmux('capture-pane', '-p', '-t', viewer)[1])
        self.tmux('send-keys', '-t', viewer, 'q')
        self.wait_for(lambda: not self.viewer())
        self.assertEqual(self.tmux('display-message', '-p', '-t', self.target, '#{pane_pid}')[1].strip(), self.pid)
        self.assertEqual(self.toggle(), 0)
        self.assertTrue(self.viewer())
        self.assertEqual(self.toggle(), 0)
        self.assertFalse(self.viewer())

    def test_native_turn_checkpoint_is_opt_in_and_excludes_earlier_changes(self):
        def hook():
            env = dict(os.environ, TMUX=self.prefix[1] + ',1,0', TMUX_PANE=self.target)
            result = subprocess.run([sys.executable, str(ROOT / 'hooks/agent-event'), '--agent', 'codex'],
                                    env=env, input=json.dumps({'hook_event_name': 'UserPromptSubmit'}),
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
        hook()
        self.assertFalse(Path(changes.checkpoint_path(self.paths, self.target)).exists())
        self.toggle()
        (self.repo / 'code').write_text('original\nuser change\n')
        hook()
        self.assertTrue(Path(changes.checkpoint_path(self.paths, self.target)).exists())
        (self.repo / 'code').write_text('original\nuser change\nagent change\n')
        label, _, _, lines, count = diffpane.read_diff(self.prefix, self.paths, self.target, 'iteration')
        self.assertEqual(label, 'Latest turn')
        self.assertEqual(count, 1)
        self.assertIn('+agent change', lines)
        self.assertNotIn('+user change', lines)

    def test_closing_agent_removes_navigation_and_diff_but_preserves_other_window(self):
        self.toggle()
        rc, keeper, _ = self.tmux('new-window', '-d', '-P', '-F', '#{pane_id}', '-n', 'keep', 'sleep', '30')
        self.assertEqual(rc, 0)
        keeper = keeper.strip()
        keeper_pid = self.tmux('display-message', '-p', '-t', keeper, '#{pane_pid}')[1].strip()
        self.tmux('kill-pane', '-t', self.target)
        ta.ensure_sidebars(self.prefix, self.paths)
        windows = self.tmux('list-windows', '-a', '-F', '#{window_id}')[1].split()
        self.assertNotIn(self.window, windows)
        self.assertEqual(self.tmux('display-message', '-p', '-t', keeper, '#{pane_pid}')[1].strip(), keeper_pid)

    def test_narrow_window_and_non_git_empty_state(self):
        self.tmux('resize-window', '-t', self.window, '-x', '100')
        self.assertEqual(self.toggle(), 1)
        self.assertFalse(self.viewer())
        self.tmux('resize-window', '-t', self.window, '-x', '180')
        self.tmux('respawn-pane', '-k', '-t', self.target, '-c', str(self.directory), 'sleep', '30')
        self.assertEqual(self.toggle(), 0)
        viewer = self.viewer()
        self.wait_for(lambda: 'Unavailable' in self.tmux('capture-pane', '-p', '-t', viewer)[1])
        self.tmux('send-keys', '-t', viewer, 'o')
        self.tmux('send-keys', '-t', viewer, '-l', str(self.repo))
        self.tmux('send-keys', '-t', viewer, 'Enter')
        self.wait_for(lambda: 'Working tree' in self.tmux('capture-pane', '-p', '-t', viewer)[1])
        self.assertEqual(self.tmux('show-options', '-p', '-v', '-t', self.target,
                                   '@tmux_agents_diff_root')[1].strip(), os.path.realpath(self.repo))
        self.tmux('send-keys', '-t', viewer, 'q')
        self.wait_for(lambda: not self.viewer())

    def test_h_l_pan_and_wrap_toggle_persists_across_reopen(self):
        (self.repo / 'code').write_text('BEGIN-' + 'x' * 220 + '-END\n')
        self.toggle()
        viewer = self.viewer()
        capture = lambda: self.tmux('capture-pane', '-p', '-t', viewer)[1]
        self.wait_for(lambda: 'BEGIN-' in capture())
        self.assertNotIn('-END', capture())
        self.tmux('send-keys', '-t', viewer, '-l', 'l' * 100)
        self.wait_for(lambda: '-END' in capture())
        self.tmux('send-keys', '-t', viewer, '-l', 'h' * 100)
        self.wait_for(lambda: 'BEGIN-' in capture())
        self.assertNotIn('-END', capture())
        self.tmux('send-keys', '-t', viewer, 'w')
        self.wait_for(lambda: 'wrap:on' in capture() and '-END' in capture())
        self.assertIn('BEGIN-', capture())
        self.assertEqual(self.tmux('show-window-options', '-v', '-t', viewer,
                                   '@tmux_agents_diff_wrap')[1].strip(), '1')
        self.toggle()
        self.toggle()
        viewer = self.viewer()
        self.wait_for(lambda: 'wrap:on' in capture() and '-END' in capture())
        self.tmux('send-keys', '-t', viewer, 'w')
        self.wait_for(lambda: 'wrap:off' in capture() and 'BEGIN-' in capture())
        self.assertNotIn('-END', capture())

    def test_diff_follows_other_real_terminal_in_same_window(self):
        self.toggle()
        viewer = self.viewer()
        other = self.directory / 'another-repo'
        other.mkdir()
        changes.git(str(other), 'init', '-q')
        (other / 'other.txt').write_text('other workspace change\n')
        rc, pane, error = self.tmux('split-window', '-v', '-t', self.target,
                                    '-c', str(other), '-P', '-F', '#{pane_id}', 'sleep', '30')
        self.assertEqual(rc, 0, error)
        pane = pane.strip()
        self.wait_for(lambda: self.tmux('show-options', '-p', '-v', '-t', viewer,
                                        '@tmux_agents_diff_target')[1].strip() == pane)
        self.wait_for(lambda: '+other workspace change' in self.tmux('capture-pane', '-p', '-t', viewer)[1])
        self.assertEqual(self.tmux('show-options', '-p', '-v', '-t', pane,
                                   '@tmux_agents_diff_tracking')[1].strip(), '1')
