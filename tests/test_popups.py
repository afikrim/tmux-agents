import fcntl
import json
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
    def test_new_project_form_fits_and_keeps_selected_field_visible(self):
        from lib.project import frame
        for width,height in ((24,5),(78,12)):
            for selected in range(4):
                lines=frame('工具 workspace','/a very long project path/'*4,'codex',selected,width,height)
                self.assertEqual(len(lines),height)
                plain=[re.sub(r'\x1b\[[0-9;]*m','',line) for line in lines]
                self.assertTrue(any(' > ' in line for line in plain))
                self.assertTrue(all(ui.cell_width(line)==width for line in plain))
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

    def test_new_project_popup_cancel_preserves_every_terminal(self):
        process=self.popup('new-project','New project + agent')
        os.write(self.master,b'\x1b')
        out,err=process.communicate(timeout=3)
        self.assertEqual(process.returncode,0,out+err)
        self.assertEqual(self.t('list-panes','-a','-F','#{pane_id} #{pane_pid}').stdout,self.original)
        self.assertEqual(self.t('display-message','-p','-c',self.name,'#{session_name}').stdout.strip(),'alpha')

    def test_new_project_popup_creates_first_agent_without_shell_placeholder(self):
        directory=Path(self.temp.name)/'repo path with spaces'
        directory.mkdir()
        binaries=Path(self.temp.name)/'bin'
        binaries.mkdir()
        for provider in ('codex','claude','opencode'):
            agent=binaries/provider
            agent.write_text('#!/bin/sh\necho "› "\nwhile true; do sleep 1; done\n')
            agent.chmod(0o755)
        self.env['PATH']=str(binaries)+os.pathsep+self.env['PATH']
        self.t('set-environment','-g','PATH',self.env['PATH'])
        process=self.popup('new-project','New project + agent')
        os.write(self.master,('\x15new project\t\x15'+str(directory)+'\t\x1b[C\r\r').encode())
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            self.drain(.1)
            current=self.t('display-message','-p','-c',self.name,'#{session_name}').stdout.strip()
            if current=='new project': break
        self.assertEqual(current,'new project')
        out,err=process.communicate(timeout=3)
        self.assertEqual(process.returncode,0,out+err)
        windows=self.t('list-windows','-t','new project','-F','#{window_name}').stdout.splitlines()
        self.assertEqual(windows,['claude'])
        panes=[line.split('|') for line in self.t('list-panes','-t','new project','-F',
            '#{pane_id}|#{@tmux_agents_sidebar}|#{pane_current_path}').stdout.splitlines()]
        real=[row for row in panes if row[1]!='1']
        self.assertEqual(len(real),1)
        self.assertEqual(Path(real[0][2]).resolve(),directory.resolve())
        after=self.t('list-panes','-a','-F','#{pane_id} #{pane_pid}').stdout.splitlines()
        self.assertTrue(all(line in after for line in self.original.splitlines()))
        before=self.t('list-panes','-a','-F','#{pane_id} #{pane_pid}').stdout
        duplicate=subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],
             'launch','--project','new project','--directory',str(directory),'codex'],env=self.env,
             capture_output=True,text=True,timeout=10)
        self.assertNotEqual(duplicate.returncode,0)
        self.assertEqual(self.t('list-panes','-a','-F','#{pane_id} #{pane_pid}').stdout,before)
        relative=subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],
             'launch','--project','relative','--exe','bin/codex','--directory',str(directory),'codex'],
             cwd=self.temp.name,env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual(relative.returncode,0,relative.stdout+relative.stderr)
        self.assertEqual(self.t('list-windows','-t','relative','-F','#{window_name}').stdout.strip(),'codex')

    def test_agent_menu_opens_new_project_form(self):
        window=self.t('display-message','-p','-c',self.name,'#{window_id}').stdout.strip()
        process=subprocess.Popen([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],
                                  'menu','--window',window],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        output=''; deadline=time.monotonic()+3
        while 'New agent' not in output and time.monotonic()<deadline: output+=self.drain(.1)
        self.assertIn('New agent',output)
        os.write(self.master,b'n')
        output=''; deadline=time.monotonic()+4
        while 'Folder:' not in output and time.monotonic()<deadline: output+=self.drain(.1)
        self.assertIn('Folder:',output)
        os.write(self.master,b'\x1b')
        out,err=process.communicate(timeout=3)
        self.assertEqual(process.returncode,0,out+err)
        self.drain(.2)
        self.assertEqual(self.t('list-panes','-a','-F','#{pane_id} #{pane_pid}').stdout,self.original)

    def test_mouse_hover_through_tmux_does_not_steal_focus_or_switch_project(self):
        self.t('set-option', '-g', 'mouse', 'on')
        main = self.t('display-message', '-p', '-t', 'alpha', '#{pane_id}').stdout.strip()
        agent=Path(self.temp.name)/'codex'
        agent.write_text('#!/bin/sh\nwhile true; do sleep 1; done\n')
        agent.chmod(0o755)
        other = self.t('new-window', '-d', '-P', '-F', '#{pane_id}', '-t', 'alpha', str(agent)).stdout.strip()
        title = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ hovering reveals this long title'
        self.t('set-option', '-p', '-t', other, '@tmux_agents_name', title)
        subprocess.run([sys.executable, str(ROOT/'bin/tmux-agents'), '--socket-path',
                        self.prefix[1], 'ensure'], env=self.env, check=True, timeout=20,
                       stdout=subprocess.DEVNULL)
        subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],
                       'daemon','--once'],env=self.env,check=True,stdout=subprocess.DEVNULL)
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
        deadline=time.monotonic()+4
        while 'ABCDE' not in initial and time.monotonic()<deadline:
            self.drain(.1)
            initial=capture()
        y = next(i for i,line in enumerate(initial.splitlines(),1) if 'ABCDE' in line)
        before = self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout
        motion(y)
        self.assertEqual(self.t('display-message','-p','-c',self.name,'#{pane_id}').stdout.strip(), main)

        self.drain(1.3)
        motion(y, 6)  # Same row must not reset the title's scroll timer.
        self.drain(.5)
        self.assertNotEqual(capture().splitlines()[y-1], initial.splitlines()[y-1])
        self.assertNotIn('> ABC',capture())  # Hover never makes the hidden agent active.
        beta_y = next(i for i,line in enumerate(initial.splitlines(),1) if 'beta search' in line)
        motion(beta_y)
        self.assertIn('Agents / alpha', capture())
        self.assertEqual(self.t('display-message','-p','-c',self.name,'#{session_name}').stdout.strip(), 'alpha')
        motion(10)  # Blank space clears the hover and stops marquee.
        self.assertIn('ABCDE', capture().splitlines()[y-1])
        self.assertEqual(self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout, before)
        self.assertEqual(self.t('display-message','-p','-c',self.name,'#{pane_id}').stdout.strip(), main)

    def test_project_click_leaves_destination_sidebar_for_last_terminal(self):
        self.t('set-option','-g','mouse','on')
        main=self.t('display-message','-p','-t','beta search','#{pane_id}').stdout.strip()
        last=self.t('split-window','-d','-t',main,'-P','-F','#{pane_id}','sleep','60').stdout.strip()
        subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],
                        'ensure'],env=self.env,check=True,timeout=20,stdout=subprocess.DEVNULL)
        def sidebar(session):
            return next(line.split('|')[0] for line in self.t('list-panes','-t',session,'-F',
                '#{pane_id}|#{@tmux_agents_sidebar}').stdout.splitlines() if line.endswith('|1'))
        source,destination=sidebar('alpha'),sidebar('beta search')
        self.t('set-window-option','-t',last,'@tmux_agents_tab_pane',last)
        self.t('select-pane','-t',destination)
        self.t('select-pane','-t',source)
        self.drain(.8)
        capture=self.t('capture-pane','-p','-t',source).stdout
        y=next(i for i,line in enumerate(capture.splitlines(),1) if 'beta search' in line)
        top=int(self.t('display-message','-p','-t',source,'#{pane_top}').stdout.strip())
        before=self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout
        os.write(self.master,('\x1b[<0;5;%dM\x1b[<0;5;%dm'%(top+y,top+y)).encode())
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            self.drain(.15)
            selected=self.t('display-message','-p','-c',self.name,'#{pane_id}').stdout.strip()
            if selected==last: break
        self.assertEqual(selected,last)
        self.assertEqual(self.t('display-message','-p','-c',self.name,'#{session_name}').stdout.strip(),'beta search')
        self.assertEqual(before,self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout)

    def test_project_unread_click_visits_and_acknowledges_exact_agent(self):
        agent=Path(self.temp.name)/'codex'
        agent.write_text('#!/bin/sh\nwhile true; do sleep 1; done\n')
        agent.chmod(0o755)
        target=self.t('new-window','-d','-P','-F','#{pane_id}','-t','beta search',str(agent)).stdout.strip()
        self.t('set-option','-g','mouse','on')
        def hook(event,**extra):
            env=dict(self.env,TMUX=self.prefix[1]+',1,0',TMUX_PANE=target)
            result=subprocess.run([sys.executable,str(ROOT/'hooks/agent-event'),'--agent','codex'],
                env=env,input=json.dumps(dict(hook_event_name=event,**extra)),capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
        def poll():
            result=subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],
                'daemon','--once'],env=self.env,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads(result.stdout)['panes'][target]
        hook('PreToolUse',tool_name='Grep',tool_input={'pattern':'private conversation text'})
        self.assertEqual(poll()['caption'],'Searching code')
        hook('Stop')
        stopped=poll()
        self.assertTrue(stopped['unread'])
        self.assertEqual(stopped['status'],'idle')
        self.assertIn('zzZ',stopped['caption'])
        subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],'ensure'],
                       env=self.env,check=True,timeout=20,stdout=subprocess.DEVNULL)
        self.drain(.8)
        sidebar=next(line.split('|') for line in self.t('list-panes','-t','alpha','-F',
            '#{pane_id}|#{@tmux_agents_sidebar}|#{pane_left}|#{pane_top}').stdout.splitlines() if '|1|' in line)
        lines=self.t('capture-pane','-p','-t',sidebar[0]).stdout.splitlines()
        project_y=next(i for i,line in enumerate(lines,1) if 'beta search' in line)
        y=project_y+1
        x=lines[y-1].index('1 unread')+2
        os.write(self.master,('\x1b[<35;%d;%dM' % (int(sidebar[2])+x,int(sidebar[3])+y)).encode())
        self.drain(.2)
        self.assertTrue(poll()['unread'])  # Hover/preview never marks read.
        os.write(self.master,('\x1b[<0;%d;%dM' % (int(sidebar[2])+x,int(sidebar[3])+y)).encode())
        self.drain(.8)
        self.assertEqual(self.t('display-message','-p','-c',self.name,'#{pane_id}').stdout.strip(),target)
        self.assertFalse(poll()['unread'])
        self.assertFalse(poll()['unread'])  # Same stop never becomes unread again.
        self.t('switch-client','-c',self.name,'-t','alpha')
        hook('Stop')
        self.assertTrue(poll()['unread'])
        for path in Path(self.env['TMUX_AGENTS_STATE_DIR']).rglob('events.jsonl'):
            self.assertNotIn('private conversation text',path.read_text())
        hook('PreToolUse',tool_name='request_user_input')
        self.assertEqual(poll()['status'],'question')
        hook('Notification',notification_type='elicitation_dialog')
        self.assertEqual(poll()['status'],'needs-input')
        # Old records did not distinguish a normal turn-end from a decision.
        # Upgrade clears that ambiguous INPUT; new typed requests retain it.
        path=next(Path(self.env['TMUX_AGENTS_STATE_DIR']).rglob('events.jsonl'))
        records=[json.loads(line) for line in path.read_text().splitlines()]
        records[-1].pop('input_required')
        path.write_text(''.join(json.dumps(record)+'\n' for record in records))
        self.assertEqual(poll()['status'],'idle')
        hook('Notification',notification_type='elicitation_dialog')
        self.assertEqual(poll()['status'],'needs-input')
        hook('Stop')
        self.assertEqual(poll()['status'],'idle')
        path.write_text('')
        self.t('respawn-pane','-k','-t',target,'sleep','60')
        self.assertEqual(poll()['status'],'done')
