"""Native window tabs through an attached TTY, including non-agent windows."""
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

from lib import theme

ROOT=Path(__file__).resolve().parents[1]


class WindowTabsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ta-tabs-')
        self.addCleanup(self.temp.cleanup)
        self.prefix=['-S',str(Path(self.temp.name)/'socket')]
        self.env=dict(os.environ,TERM='xterm-256color',TMUX_AGENTS_STATE_DIR=str(Path(self.temp.name)/'state'))
        self.env.pop('TMUX',None); self.env.pop('TMUX_PANE',None)
        self.t('-f','/dev/null','new-session','-d','-s','tabs','-n','code','-x','180','-y','40','sleep','60')
        self.addCleanup(lambda:self.t('kill-server'))
        self.t('new-window','-d','-t','tabs','-n','editor','sleep','60')
        self.t('new-window','-d','-t','tabs','-n','shell','sleep','60')
        for window,name,value in theme.TMUX_OPTIONS:
            result=self.t('set-option','-g',*(['-w'] if window else []),name,value)
            self.assertEqual(result.returncode,0,result.stderr)
        self.t('set-option','-g','mouse','on')
        subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],'ensure'],
                       env=self.env,check=True,timeout=20,stdout=subprocess.DEVNULL)
        self.original=self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout
        self.master,slave=pty.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',40,180,0,0))
        self.client=subprocess.Popen(['tmux',*self.prefix,'attach-session','-t','tabs'],
                          stdin=slave,stdout=slave,stderr=slave,env=self.env,start_new_session=True)
        os.close(slave)
        self.addCleanup(self.close_client)
        self.output=''
        deadline=time.monotonic()+3
        self.name=''
        while not self.name and time.monotonic()<deadline:
            self.drain(.1)
            self.name=self.t('list-clients','-F','#{client_name}').stdout.strip()
        self.assertTrue(self.name)

    def t(self,*args):
        return subprocess.run(['tmux',*self.prefix,*args],env=self.env,capture_output=True,text=True,timeout=10)

    def drain(self,seconds):
        output=b''; deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if select.select([self.master],[],[],.05)[0]:
                try: output+=os.read(self.master,65536)
                except OSError: break
        self.output+=output.decode('utf-8','replace')

    def close_client(self):
        if self.client.poll() is None:
            self.client.terminate()
            try: self.client.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.client.kill(); self.client.wait()
        os.close(self.master)

    def padding(self,side='left'):
        value=self.t('display-message','-p','-c',self.name,'#{E:status-'+side+'}').stdout.rstrip('\n')
        self.assertEqual(value,' '*len(value))
        return len(value)

    def click(self,x):
        os.write(self.master,('\x1b[<0;%d;1M\x1b[<0;%d;1m'%(x,x)).encode())
        self.drain(.4)

    def current(self):
        return self.t('display-message','-p','-c',self.name,'#{window_index}').stdout.strip()

    def sidebar(self):
        return next(line.split('|')[0] for line in self.t('list-panes','-F',
            '#{pane_id}|#{@tmux_agents_sidebar}').stdout.splitlines() if line.endswith('|1'))

    def test_tabs_click_non_agent_windows_after_sidebar_padding(self):
        self.assertEqual(self.padding(),33)
        self.assertEqual(self.padding('right'),0)
        self.assertEqual(self.t('show-options','-g','-v','status-position').stdout.strip(),'top')
        # First label is " 0 code ", then one separator; click inside editor.
        self.click(33+len(' 0 code ')+1+3)
        self.assertEqual(self.current(),'1')
        self.click(33+3)
        self.assertEqual(self.current(),'0')
        self.assertEqual(self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout,self.original)

    def test_padding_tracks_resize_zoom_hidden_sidebar_and_diff(self):
        sidebar=self.sidebar()
        self.t('resize-pane','-t',sidebar,'-x','44')
        self.assertEqual(self.padding(),45)
        self.drain(1.1)
        self.click(45+len(' 0 code ')+1+3)
        self.assertEqual(self.current(),'1')
        self.assertEqual(self.padding(),33)
        main=self.t('display-message','-p','-c',self.name,'#{pane_id}').stdout.strip()
        self.t('resize-pane','-Z','-t',main)
        self.assertEqual(self.padding(),0)
        self.t('resize-pane','-Z','-t',main)
        sidebar=self.sidebar()
        self.t('kill-pane','-t',sidebar)
        self.assertEqual(self.padding(),0)
        self.drain(1.1)
        self.click(3)
        self.assertEqual(self.current(),'0')
        main=self.t('display-message','-p','-c',self.name,'#{pane_id}').stdout.strip()
        diff=self.t('split-window','-fh','-d','-t',main,'-l','40','-P','-F','#{pane_id}','sleep','30').stdout.strip()
        self.t('set-option','-p','-t',diff,'@tmux_agents_diff','1')
        self.assertEqual(self.padding('right'),41)
        self.t('resize-pane','-Z','-t',main)
        self.assertEqual(self.padding('right'),0)
        self.t('resize-pane','-Z','-t',main)
        self.t('kill-pane','-t',diff)
        self.assertEqual(self.padding('right'),0)

    def test_dynamic_title_renders_literal_metadata_in_native_tab(self):
        import runpy
        module=runpy.run_path(str(ROOT/'bin/tmux-agents'))
        pane=self.t('display-message','-p','-c',self.name,'#{pane_id}').stdout.strip()
        self.t('select-pane','-t',pane,'-T','Hi #[fg=red] ##{x}')
        module['update_window_tabs'](self.prefix,module['list_all'](self.prefix),
                                    {'panes':{pane:{'agent':'codex'}}})
        self.output=''
        self.t('refresh-client','-S','-t',self.name)
        self.drain(.4)
        self.assertIn('Hi #[fg=red] #{x}',self.output)
        self.assertNotIn('##[fg=red]',self.output)

    def test_focused_tab_scrolls_and_inactive_tab_stays_still(self):
        import runpy
        module=runpy.run_path(str(ROOT/'bin/tmux-agents'))
        panes=module['list_all'](self.prefix)
        real=[p for p in panes if not p.get('sidebar')]
        for p in real:
            self.t('select-pane','-t',p['pane_id'],'-T','ABCDEFGHIJKLMNOPQRSTUVWXYZ longer title')
        panes=module['list_all'](self.prefix)
        state={'panes':{p['pane_id']:{'agent':'codex'} for p in real}}
        animation={}
        module['update_window_tabs'](self.prefix,panes,state,animation)
        before={wid:self.t('show-window-options','-t',wid,'-v','@tmux_agents_tab_label').stdout for wid in animation}
        for entry in animation.values():
            entry['since']-=3
        module['animate_window_tabs'](self.prefix,animation)
        for wid,entry in animation.items():
            after=self.t('show-window-options','-t',wid,'-v','@tmux_agents_tab_label').stdout
            self.assertEqual(len(after.rstrip('\n')),24)
            if entry['focused']:
                self.assertNotEqual(before[wid],after)
            else:
                self.assertEqual(before[wid],after)
        self.assertEqual(self.current(),'0')
        self.t('refresh-client','-S','-t',self.name)
        self.drain(.2)
        self.click(33+24+1+3)
        self.assertEqual(self.current(),'1')

    def test_live_daemon_animates_focused_directory_tab(self):
        folder=Path(self.temp.name)/'abcdefghijklmnopqrstuvwxyz-long-project-directory'
        folder.mkdir()
        wid=self.t('new-window','-t','tabs','-c',str(folder),'-P','-F','#{window_id}','/bin/sh').stdout.strip()
        command=[sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1]]
        try:
            subprocess.run(command+['daemon-start'],env=self.env,check=True,timeout=10,stdout=subprocess.DEVNULL)
            labels=set(); deadline=time.monotonic()+6
            while time.monotonic()<deadline and len(labels)<2:
                label=self.t('show-window-options','-t',wid,'-v','@tmux_agents_tab_label').stdout.rstrip('\n')
                if label:
                    self.assertEqual(len(label),24)
                    labels.add(label)
                self.drain(.15)
            self.assertGreaterEqual(len(labels),2)
        finally:
            subprocess.run(command+['daemon-stop'],env=self.env,timeout=10,stdout=subprocess.DEVNULL)

    def test_overflow_keeps_current_window_visible_and_shortcuts_work(self):
        for index in range(14):
            self.t('new-window','-d','-t','tabs','-n','long-window-%02d'%index,'sleep','30')
        self.output=''
        self.t('new-window','-t','tabs','-n','visible-last-tab','sleep','30')
        self.drain(.6)
        self.assertIn('visible-last-tab',self.output)
        self.assertEqual(self.current(),'17')
        os.write(self.master,b'\x02p')
        self.drain(.4)
        self.assertEqual(self.current(),'16')
        os.write(self.master,b'\x02n')
        self.drain(.4)
        self.assertEqual(self.current(),'17')
