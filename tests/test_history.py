import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from lib import history, popups, ui

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('ta_history', str(ROOT/'bin/tmux-agents'))
spec = importlib.util.spec_from_loader('ta_history', loader)
ta = importlib.util.module_from_spec(spec)
loader.exec_module(ta)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ta-history-')
        self.addCleanup(self.temp.cleanup)
        self.env = dict(os.environ, TMUX_AGENTS_STATE_DIR=str(Path(self.temp.name)/'state'))
        self.env.pop('TMUX', None)
        self.env.pop('TMUX_PANE', None)
        self.guard = patch.dict(os.environ, self.env, clear=True)
        self.guard.start()
        self.addCleanup(self.guard.stop)
        self.prefix = ['-S', str(Path(self.temp.name)/'socket')]

    def t(self, *args):
        return subprocess.run(['tmux', *self.prefix, *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def cli(self, *args):
        return subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),
                               '--socket-path',self.prefix[1],*args], env=self.env,
                              capture_output=True,text=True,timeout=20)

    def remember(self, provider='codex', ident='session-1', **changes):
        fields=dict(title='Fix login',directory=self.temp.name,project='app',
                    pane='%1',socket='test',pid=123,start=456.)
        fields.update(changes)
        history.remember(provider,ident,**fields)

    def test_private_metadata_dedup_search_and_bounded_retention(self):
        self.remember()
        self.remember(title='New name')
        self.assertEqual(len(history.read()),1)
        listed=self.cli('history','--list')
        self.assertEqual(listed.returncode,0,listed.stderr)
        self.assertEqual(json.loads(listed.stdout)[0]['title'],'New name')
        self.assertEqual(history.entries('APP codex New')[0]['name'],'New name')
        self.assertEqual(history.entries('missing'),[])
        self.assertEqual(stat.S_IMODE(os.stat(history.path()).st_mode),0o600)
        with patch.object(history,'LIMIT',2):
            self.remember('claude','session-2')
            self.remember('opencode','ses_3')
        self.assertEqual(len(history.read()),2)
        self.assertEqual(set(history.read()[0]),{'id','provider','conversation','title',
            'directory','project','pane','socket','pid','start','updated'})

    def test_ids_and_provider_resume_argv_are_validated(self):
        for value in ('--last','bad;touch file','x\nother',{},['x']):
            self.assertEqual(history.conversation_id({'session_id':value}),'')
        self.assertEqual(history.conversation_id({'thread_id':'thread-1'}),'thread-1')
        for provider,expected in [('codex',['--no-daemon','resume','session-1']),
            ('claude',['--resume','session-1']),('opencode',['--session','session-1'])]:
            self.assertEqual(history.resume_args({'provider':provider,'conversation':'session-1'}),expected)

    def test_refresh_cannot_attach_old_conversation_to_new_process(self):
        self.remember()
        self.remember(title='Wrong',pid=999,refresh=True)
        self.assertEqual(history.read()[0]['title'],'Fix login')
        self.remember(title='Renamed',refresh=True)
        self.assertEqual(history.read()[0]['title'],'Renamed')

    def test_history_popup_search_and_frame_fit(self):
        self.remember()
        rows=popups.entries([], 'history', 'app login')
        self.assertEqual(len(rows),1)
        for width,height in ((24,8),(78,28)):
            lines,_,hits=popups.frame(rows,'history','',0,0,width,height)
            self.assertEqual(len(lines),height)
            import re
            self.assertTrue(all(ui.cell_width(re.sub(r'\x1b\[[0-9;]*m','',line))==width for line in lines))
            self.assertEqual(set(hits.values()),{0})

    def test_missing_directory_fails_without_creating_window(self):
        self.assertEqual(self.t('-f','/dev/null','new-session','-d','-s','app','sleep','60').returncode,0)
        self.addCleanup(lambda:self.t('kill-server'))
        self.remember(directory='/no/such/tmux-agents-history-directory')
        record=history.read()[0]
        before=self.t('list-windows','-a','-F','#{window_id}').stdout
        result=self.cli('resume',record['id'])
        self.assertNotEqual(result.returncode,0)
        self.assertIn('directory is missing',result.stderr)
        self.assertEqual(before,self.t('list-windows','-a','-F','#{window_id}').stdout)

    def test_native_hooks_record_live_jump_and_exact_resume_for_each_provider(self):
        self.assertEqual(self.t('-f','/dev/null','new-session','-d','-s','app','-x','180','-y','40','sleep','90').returncode,0)
        self.addCleanup(lambda:self.t('kill-server'))
        # Resume must not inherit or even require valid fresh-launch settings.
        Path(self.env['TMUX_AGENTS_STATE_DIR']).mkdir(parents=True,exist_ok=True)
        (Path(self.env['TMUX_AGENTS_STATE_DIR'])/'project-defaults.json').write_text('invalid JSON')
        for provider in history.PROVIDERS:
            with self.subTest(provider=provider):
                executable=Path(self.temp.name)/provider
                executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >"$0.args"\nwhile true; do sleep 1; done\n')
                executable.chmod(0o755)
                pane=self.t('new-window','-t','app','-P','-F','#{pane_id}','-c',self.temp.name,str(executable)).stdout.strip()
                ident='conversation-'+provider
                def hook(target):
                    socket=self.t('display-message','-p','#{socket_path},#{pid},0').stdout.strip()
                    env=dict(self.env,TMUX=socket,TMUX_PANE=target)
                    payload=dict(hook_event_name='SessionStart',session_id=ident,
                                 prompt='SECRET_CHAT_MUST_NOT_BE_STORED')
                    result=subprocess.run([sys.executable,str(ROOT/'hooks/agent-event'),'--agent',provider],
                        input=json.dumps(payload),env=env,capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,0,result.stderr)
                self.t('select-pane','-t',pane,'-T','Fix '+provider)
                time.sleep(.2)
                hook(pane)
                records=history.read()
                record=next(r for r in records if r['provider']==provider)
                self.assertNotIn('SECRET_CHAT',Path(history.path()).read_text())
                count=self.t('list-windows','-a','-F','#{window_id}').stdout
                result=self.cli('resume',record['id'],'--exe','/missing/should-not-be-used')
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(count,self.t('list-windows','-a','-F','#{window_id}').stdout)
                self.assertEqual(self.t('display-message','-p','#{pane_id}').stdout.strip(),pane)
                self.t('kill-window','-t',pane)
                result=self.cli('resume',record['id'],'--exe',str(executable))
                self.assertEqual(result.returncode,0,result.stderr)
                deadline=time.monotonic()+3
                expected=history.resume_args(record)
                while time.monotonic()<deadline:
                    args=Path(str(executable)+'.args').read_text().splitlines()
                    if args==expected: break
                    time.sleep(.05)
                self.assertEqual(args,expected)
                newpane=self.t('display-message','-p','#{pane_id}').stdout.strip()
                self.assertEqual(os.path.realpath(self.t('display-message','-p','-t',newpane,'#{pane_current_path}').stdout.strip()),os.path.realpath(self.temp.name))
                hook(newpane)
                count=self.t('list-windows','-a','-F','#{window_id}').stdout
                self.assertEqual(self.cli('resume',record['id'],'--exe','/missing').returncode,0)
                self.assertEqual(count,self.t('list-windows','-a','-F','#{window_id}').stdout)
                self.t('kill-window','-t',newpane)
        # History also survives removal of the entire project session.
        self.t('new-session','-d','-s','keepalive','sleep','60')
        self.t('kill-session','-t','app')
        record=next(r for r in history.read() if r['provider']=='opencode')
        result=self.cli('resume',record['id'],'--exe',str(Path(self.temp.name)/'opencode'))
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('app',self.t('list-sessions','-F','#{session_name}').stdout.splitlines())


if __name__ == '__main__':
    unittest.main()
