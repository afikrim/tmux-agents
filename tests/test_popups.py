import fcntl
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

from lib import popups, ui

ROOT = Path(__file__).resolve().parents[1]


class PopupModelTest(unittest.TestCase):
    def test_search_sessions_status_help_and_frame_geometry(self):
        sessions = [dict(id='$1', name='工具 backend', windows=[dict(panes=[
            dict(agent='codex', status='needs-approval')])]), dict(id='$2', name='frontend', windows=[])]
        rows = popups.entries(sessions, 'sessions', 'backend waiting', '$1')
        self.assertEqual([row['id'] for row in rows], ['$1'])
        self.assertTrue(rows[0]['current'])
        self.assertTrue(popups.entries([], 'help', 'wrap'))
        self.assertFalse(popups.entries(sessions, 'sessions', 'missing'))
        for kind in ('sessions', 'help'):
            rows = popups.entries(sessions, kind, '', '$1')
            for width, height in ((24, 8), (78, 26)):
                lines, top, hits = popups.frame(rows, kind, '', 0, 0, width, height)
                self.assertEqual(len(lines), height)
                self.assertTrue(all(ui.cell_width(re.sub(r'\x1b\[[0-9;]*m', '', line)) == width for line in lines))
                self.assertTrue(all(0 <= index < len(rows) for index in hits.values()))


class PopupTTYTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ta-popup-')
        self.addCleanup(self.temp.cleanup)
        self.prefix = ['-S', str(Path(self.temp.name) / 'socket')]
        self.env = dict(os.environ, TERM='xterm-256color', TMUX_AGENTS_STATE_DIR=str(Path(self.temp.name) / 'state'))
        self.env.pop('TMUX', None)
        self.env.pop('TMUX_PANE', None)
        self.t('-f', '/dev/null', 'new-session', '-d', '-s', 'alpha', '-x', '140', '-y', '40', 'sleep', '60')
        self.addCleanup(lambda: self.t('kill-server'))
        self.t('new-session', '-d', '-s', 'beta search', 'sleep', '60')
        self.original = self.t('list-panes', '-a', '-F', '#{pane_id} #{pane_pid}').stdout
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 140, 0, 0))
        self.client = subprocess.Popen(['tmux', *self.prefix, 'attach-session', '-t', 'alpha'],
                                       stdin=slave, stdout=slave, stderr=slave, env=self.env, start_new_session=True)
        os.close(slave)
        self.addCleanup(self.close_client)
        self.name = ''
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.drain(.1)
            self.name = self.t('list-clients', '-F', '#{client_name}').stdout.strip()
            if self.name:
                break
        self.assertTrue(self.name)

    def t(self, *args):
        return subprocess.run(['tmux', *self.prefix, *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def drain(self, seconds):
        chunks = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([self.master], [], [], .05)[0]:
                try:
                    chunks.append(os.read(self.master, 65536))
                except OSError:
                    break
        return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', b''.join(chunks).decode('utf-8', 'replace'))

    def close_client(self):
        if self.client.poll() is None:
            self.client.terminate()
            try:
                self.client.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.client.kill()
                self.client.wait()
        os.close(self.master)

    def popup(self, command, title):
        process = subprocess.Popen([sys.executable, str(ROOT / 'bin/tmux-agents'),
                                    '--socket-path', self.prefix[1], command, '--client', self.name],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline, output = time.monotonic() + 4, ''
        while time.monotonic() < deadline:
            output += self.drain(.1)
            if title in output:
                break
        if title not in output:
            process.kill()
            out, err = process.communicate()
            self.fail(output + out + err)
        return process

    def test_search_and_enter_switch_original_client_without_creating_panes(self):
        process = self.popup('sessions', 'Switch project')
        try:
            os.write(self.master, b'beta\r')
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                self.drain(.1)
                current = self.t('display-message', '-p', '-c', self.name, '#{session_name}').stdout.strip()
                if current == 'beta search':
                    break
            self.assertEqual(current, 'beta search')
            out, err = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, out + err)
            self.assertEqual(self.t('list-panes', '-a', '-F', '#{pane_id} #{pane_pid}').stdout, self.original)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_help_popup_escape_preserves_session_and_panes(self):
        process = self.popup('help', 'Keyboard shortcuts')
        try:
            os.write(self.master, b'wrap')
            self.assertIn('soft wrap', self.drain(.6))
            os.write(self.master, b'\x1b')
            out, err = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, out + err)
            self.assertEqual(self.t('display-message', '-p', '-c', self.name, '#{session_name}').stdout.strip(), 'alpha')
            self.assertEqual(self.t('list-panes', '-a', '-F', '#{pane_id} #{pane_pid}').stdout, self.original)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_mouse_hover_through_tmux_does_not_steal_focus_or_switch_project(self):
        self.t('set-option', '-g', 'mouse', 'on')
        main = self.t('display-message', '-p', '-t', 'alpha', '#{pane_id}').stdout.strip()
        other = self.t('new-window', '-d', '-P', '-F', '#{pane_id}', '-t', 'alpha', 'sleep', '60').stdout.strip()
        title = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ hovering reveals this long title'
        self.t('set-option', '-p', '-t', other, '@tmux_agents_name', title)
        subprocess.run([sys.executable, str(ROOT/'bin/tmux-agents'), '--socket-path',
                        self.prefix[1], 'ensure'], env=self.env, check=True, timeout=20,
                       stdout=subprocess.DEVNULL)
        self.drain(.8)
        rows = [line.split('|') for line in self.t('list-panes','-t','alpha', '-F',
                '#{pane_id}|#{@tmux_agents_sidebar}|#{pane_left}|#{pane_top}').stdout.splitlines()]
        sidebar = next(row for row in rows if row[1] == '1')
        def capture():
            return self.t('capture-pane','-p','-t',sidebar[0]).stdout
        def motion(y, x=5):
            os.write(self.master, ('\x1b[<35;%d;%dM' %
                     (int(sidebar[2])+x, int(sidebar[3])+y)).encode())
            self.drain(.25)
        initial = capture()
        y = next(i for i,line in enumerate(initial.splitlines(),1) if 'ABCDE' in line)
        before = self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout
        motion(y)
        self.assertEqual(self.t('display-message','-p','-c',self.name,'#{pane_id}').stdout.strip(), main)
        self.drain(1.3)
        motion(y, 6)  # Same row must not reset the title's scroll timer.
        self.drain(.5)
        self.assertNotEqual(capture().splitlines()[y-1], initial.splitlines()[y-1])
        self.assertIn('>', capture())  # Active-window indicator remains visible.
        beta_y = next(i for i,line in enumerate(initial.splitlines(),1) if 'beta search' in line)
        motion(beta_y)
        self.assertIn('Agents / alpha', capture())
        self.assertEqual(self.t('display-message','-p','-c',self.name,'#{session_name}').stdout.strip(), 'alpha')
        motion(10)  # Blank space clears the hover and stops marquee.
        self.assertIn('ABCDE', capture().splitlines()[y-1])
        self.assertEqual(self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout, before)
        self.assertEqual(self.t('display-message','-p','-c',self.name,'#{pane_id}').stdout.strip(), main)
