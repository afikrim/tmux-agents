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
    def test_tab_label_has_stable_cell_width(self):
        for index in (0,9,10,100):
            for title in ('short', 'A very long conversation title that keeps going', '工具開発資料é'*10):
                text=ui.tab_label(title,index)
                self.assertEqual(ui.cell_width(text),24)
                self.assertTrue(text.startswith(' '+str(index)+' '))
                self.assertEqual(text,ui.tab_label(title,index))
        title='A very long conversation title that keeps going'
        self.assertNotEqual(ui.tab_label(title,0,0),ui.tab_label(title,0,3))

    def test_tab_titles_follow_agent_program_and_directory(self):
        pane = {"title":"⠹ Fix login | app", "path":"/work/app", "program":"node"}
        for agent in ("codex", "claude", "opencode"):
            self.assertEqual(ui.tab_title(pane, agent), "Fix login")
        pane.update(program="nvim", custom_name="Old agent nickname")
        self.assertEqual(ui.tab_title(pane), "Neovim")
        pane["program"] = "-fish"
        self.assertEqual(ui.tab_title(pane), "app")
        pane["path"] = "/"
        self.assertEqual(ui.tab_title(pane), "/")

    def test_dynamic_tabs_keep_last_terminal_through_sidebar_focus(self):
        with tempfile.TemporaryDirectory(prefix='ta-tab-names-') as tmp:
            prefix = ['-S', str(Path(tmp) / 'socket')]
            def tmux(*args):
                rc,out,error=ta.run_tmux(prefix,*args)
                self.assertEqual(rc,0,error)
                return out.strip()
            try:
                tmux('-f','/dev/null','new-session','-d','-s','names','-n','original','sleep','30')
                first=tmux('display-message','-p','#{pane_id}')
                second=tmux('split-window','-d','-P','-F','#{pane_id}','sleep','30')
                sidebar=tmux('split-window','-d','-P','-F','#{pane_id}','sleep','30')
                tmux('set-option','-p','-t',sidebar,'@tmux_agents_sidebar','1')
                tmux('select-pane','-t',first,'-T','Fix login')
                state={'panes':{first:{'agent':'codex'},second:{'agent':''}}}
                def update():
                    ta.update_window_tabs(prefix,ta.list_all(prefix),state)
                    return tmux('show-window-options','-v','@tmux_agents_tab_title')
                self.assertEqual(update(),'Fix login')
                tmux('select-pane','-t',sidebar)
                self.assertEqual(update(),'Fix login')
                tmux('select-pane','-t',second)
                self.assertEqual(update(),'sleep')
                tmux('select-pane','-t',sidebar)
                self.assertEqual(update(),'sleep')
                tmux('kill-pane','-t',second)
                self.assertEqual(update(),'Fix login')
                tmux('select-pane','-t',first,'-T','New title #[fg=red] ##{pane_id}')
                self.assertEqual(update(),'New title ##[fg=red] ##{pane_id}')
                self.assertEqual(tmux('display-message','-p','#{window_name}'),'original')
            finally:
                ta.run_tmux(prefix,'kill-server')

    def test_live_title_and_custom_precedence(self):
        self.assertEqual(ui.pane_name('', '⠹ Fix login | app', 'codex', '/work/app'), 'Fix login')
        self.assertEqual(ui.pane_name('My task', 'Generated title', 'claude'), 'My task')
        self.assertEqual(ui.pane_name('', 'Codex', 'codex'), 'codex')
        self.assertEqual(ui.pane_name('', '/work/app', '', '/work/app'), 'shell')
        self.assertEqual(ui.pane_name('', 'Investigate error', 'opencode'), 'Investigate error')
        self.assertEqual(ui.pane_name('', '\x1bBad\x07 title', 'codex'), 'Bad title')

    def test_foreground_program_names_keep_custom_and_agent_titles(self):
        self.assertEqual(ui.pane_name('', '', '', program='nvim'), 'Neovim')
        self.assertEqual(ui.pane_name('', '', '', program='/usr/bin/htop'), 'htop')
        self.assertEqual(ui.pane_name('', '', '', program='-zsh'), 'shell')
        self.assertEqual(ui.pane_name('Editor', '', '', program='nvim'), 'Editor')
        self.assertEqual(ui.pane_name('', 'Fix auth', 'codex', program='node'), 'Fix auth')

    def test_program_label_changes_when_program_opens_and_exits(self):
        import time
        import shlex
        with tempfile.TemporaryDirectory(prefix='ta-program-') as tmp:
            prefix = ['-S', str(Path(tmp) / 'socket')]
            def tmux(*args):
                return ta.run_tmux(prefix, *args)
            try:
                rc, _, error = tmux('-f', '/dev/null', 'new-session', '-d', '-s', 'program', '/bin/sh')
                self.assertEqual(rc, 0, error)
                pane = tmux('display-message', '-p', '#{pane_id}')[1].strip()
                paths = {'state': str(Path(tmp) / 'state.json')}
                def name():
                    return ta.build_sessions(prefix, paths)[0]['windows'][0]['panes'][0]['display_name']
                def tab():
                    panes=ta.list_all(prefix)
                    ta.update_window_tabs(prefix,panes,{'panes':{p['pane_id']:{'agent':''} for p in panes}})
                    return tmux('show-window-options','-v','@tmux_agents_tab_title')[1].strip()
                self.assertEqual(name(), 'shell')
                tmux('send-keys', '-t', pane, 'sleep 30', 'Enter')
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and name() != 'sleep':
                    time.sleep(.1)
                self.assertEqual(name(), 'sleep')
                self.assertEqual(tab(), 'sleep')
                # Verify an actual Neovim process too when it is installed.
                import shutil
                nvim = shutil.which('nvim')
                if nvim:
                    tmux('send-keys', '-t', pane, 'C-c')
                    tmux('send-keys', '-t', pane, shlex.quote(nvim) + ' -u NONE -i NONE', 'Enter')
                    deadline = time.monotonic() + 4
                    while time.monotonic() < deadline and name() != 'Neovim':
                        time.sleep(.1)
                    self.assertEqual(name(), 'Neovim')
                    self.assertEqual(tab(), 'Neovim')
                    tmux('send-keys', '-t', pane, 'Escape', ':qa!', 'Enter')
                else:
                    tmux('send-keys', '-t', pane, 'C-c')
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and name() != 'shell':
                    time.sleep(.1)
                self.assertEqual(name(), 'shell')
                tmux('send-keys','-t',pane,'cd '+shlex.quote(tmp),'Enter')
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and tab()!=Path(tmp).name:
                    time.sleep(.1)
                self.assertEqual(tab(),Path(tmp).name)
            finally:
                tmux('kill-server')

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
