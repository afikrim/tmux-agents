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

from lib import defaults

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('ta_defaults',str(ROOT/'bin/tmux-agents'))
spec=importlib.util.spec_from_loader('ta_defaults',loader)
ta=importlib.util.module_from_spec(spec); loader.exec_module(ta)


class DefaultsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ta defaults ')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.env=dict(os.environ,TMUX_AGENTS_STATE_DIR=str(self.root/'state'))
        self.env.pop('TMUX',None); self.env.pop('TMUX_PANE',None)
        self.guard=patch.dict(os.environ,self.env,clear=True)
        self.guard.start(); self.addCleanup(self.guard.stop)
        self.prefix=['-S',str(self.root/'socket')]

    def cli(self,*args):
        return subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],*args],
            env=self.env,capture_output=True,text=True,timeout=20)

    def t(self,*args):
        return ta.run_tmux(self.prefix,*args)

    def start(self):
        self.repo=self.root/'repo'; self.repo.mkdir()
        result=self.t('-f','/dev/null','new-session','-d','-s','app','-c',str(self.repo),'sleep','120')
        self.assertEqual(result[0],0,result[2]); self.addCleanup(lambda:self.t('kill-server'))
        self.window=self.t('display-message','-p','#{window_id}')[1].strip()
        self.exe=self.root/'fake agent'
        self.capture=self.root/'capture.json'
        self.exe.write_text('#!'+sys.executable+'\nimport json,os,sys,time\n'
            'with open(os.path.join(os.path.dirname(__file__),"capture.json"),"w") as f:\n'
            ' json.dump({"argv":sys.argv[1:],"cwd":os.getcwd(),"state_root":os.environ.get("TMUX_AGENTS_STATE_DIR")},f)\ntime.sleep(90)\n')
        self.exe.chmod(0o755)

    def launch(self,*args):
        if self.capture.exists(): self.capture.unlink()
        result=self.cli('launch','--window',self.window,'--exe',str(self.exe),*args)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            try: return json.loads(self.capture.read_text())
            except (OSError,ValueError): time.sleep(.05)
        self.fail('Fake agent did not start')

    def test_private_per_project_storage_validation_and_reset_preserve_other_records(self):
        self.assertEqual(defaults.get('app'),{})
        self.assertFalse(Path(defaults.path()).exists())
        defaults.save('app',dict(provider='claude',arguments=['--model','some model']))
        defaults.save('other',dict(provider='opencode'))
        before=Path(defaults.path()).read_bytes()
        with self.assertRaises(ValueError): defaults.save('app',dict(arguments='not an array'))
        self.assertEqual(before,Path(defaults.path()).read_bytes())
        defaults.save('app',dict(directory=str(self.root)))
        self.assertEqual(defaults.get('app')['arguments'],['--model','some model'])
        defaults.save('app',dict(provider='opencode'))
        self.assertEqual(defaults.get('app')['arguments'],[])
        defaults.save('app',dict(provider='claude',arguments=['--model','replacement']))
        self.assertEqual(defaults.get('app')['arguments'],['--model','replacement'])
        self.assertEqual(stat.S_IMODE(os.stat(defaults.path()).st_mode),0o600)
        defaults.reset('app')
        self.assertEqual(defaults.get('app'),{})
        self.assertEqual(defaults.get('other')['provider'],'opencode')
        records=json.loads(Path(defaults.path()).read_text()); records['broken']=None
        Path(defaults.path()).write_text(json.dumps(records)); before=Path(defaults.path()).read_bytes()
        with self.assertRaises(ValueError): defaults.save('broken',dict(provider='codex'))
        self.assertEqual(before,Path(defaults.path()).read_bytes())
        defaults.reset('broken')
        self.assertEqual(defaults.get('other')['provider'],'opencode')

    def test_precedence_keeps_provider_specific_arguments_separate(self):
        record=dict(provider='claude',directory='/project',arguments=['--model','saved'],worktree=True)
        self.assertEqual(defaults.resolve(record)['provider'],'claude')
        self.assertEqual(defaults.resolve(record,provider='codex')['arguments'],[])
        result=defaults.resolve(record,provider='claude',directory='/explicit',worktree=False,arguments=['--model','cli'])
        self.assertEqual(result,dict(provider='claude',directory='/explicit',worktree=False,arguments=['--model','cli']))
        self.assertEqual(defaults.resolve(record,arguments=[])['arguments'],[])
        self.assertEqual(defaults.resolve(record,no_default_args=True)['arguments'],[])
        from lib.tmuxutil import state_root
        with patch.dict(os.environ,{'TMUX_AGENTS_STATE_DIR':'relative prefs'}), patch('os.getcwd',return_value=str(self.root)):
            self.assertEqual(state_root(),str(self.root/'relative prefs'))

    def test_cli_show_set_and_bad_input_do_not_change_running_terminals(self):
        self.start()
        before=self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}')[1]
        result=self.cli('defaults','--project','app','--provider','claude','--args-json','["--model","saved"]')
        self.assertEqual(result.returncode,0,result.stderr)
        result=self.cli('defaults','--project','app','--show')
        self.assertEqual(json.loads(result.stdout)['arguments'],['--model','saved'])
        saved=Path(defaults.path()).read_bytes()
        for args in (['--args-json','not json'],['--args-json','[7]'],['--show','--provider','codex']):
            self.assertNotEqual(self.cli('defaults','--project','app',*args).returncode,0)
            self.assertEqual(saved,Path(defaults.path()).read_bytes())
        self.assertEqual(before,self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}')[1])

    def test_launch_uses_saved_provider_directory_literal_argv_and_explicit_overrides(self):
        self.start()
        directory=self.root/'saved folder'; directory.mkdir()
        explicit=self.root/'explicit'; explicit.mkdir()
        marker=self.root/'must-not-exist'
        argv=['--model','saved model','$(touch '+str(marker)+')']
        defaults.save('app',dict(provider='claude',directory=str(directory),arguments=argv,worktree=False))
        self.t('set-environment','-gu','TMUX_AGENTS_STATE_DIR')
        result=self.launch()
        self.assertEqual(result['argv'],argv)
        self.assertEqual(result['state_root'],str(self.root/'state'))
        self.assertEqual(Path(result['cwd']).resolve(),directory.resolve())
        self.assertFalse(marker.exists())
        result=self.launch('--directory',str(explicit),'--no-worktree','opencode','--','--model','cli')
        self.assertEqual(result['argv'],['--model','cli'])
        self.assertEqual(Path(result['cwd']).resolve(),explicit.resolve())
        self.assertEqual(self.launch('--no-default-args','claude')['argv'],[])
        self.assertEqual(self.launch('--no-default-args')['argv'],[])
        result=self.launch('--no-defaults')
        self.assertEqual(result['argv'],[])
        self.assertEqual(Path(result['cwd']).resolve(),self.repo.resolve())

    def test_saved_worktree_preference_can_be_overridden_per_launch(self):
        self.start()
        for args in (['init','-q'],['config','user.name','Test'],['config','user.email','test@example.invalid'],
                     ['config','commit.gpgsign','false']):
            subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True)
        (self.repo/'file').write_text('committed')
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True,capture_output=True)
        subprocess.run(['git','-C',str(self.repo),'commit','-qm','Initial'],check=True,capture_output=True)
        defaults.save('app',dict(provider='claude',directory=str(self.repo),worktree=True))
        result=self.launch()
        self.assertNotEqual(Path(result['cwd']).resolve(),self.repo.resolve())
        branch=subprocess.check_output(['git','-C',result['cwd'],'branch','--show-current'],text=True).strip()
        self.assertTrue(branch.startswith('agents/claude-'))
        result=self.launch('--no-worktree')
        self.assertEqual(Path(result['cwd']).resolve(),self.repo.resolve())
        self.assertTrue(defaults.get('app')['worktree'])

    def test_missing_directory_and_corrupt_settings_fail_closed_with_escape_hatch(self):
        self.start()
        defaults.save('app',dict(directory=str(self.root/'missing')))
        before=self.t('list-windows','-a','-F','#{window_id}')[1]
        result=self.cli('launch','--window',self.window,'--exe',str(self.exe))
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(before,self.t('list-windows','-a','-F','#{window_id}')[1])
        Path(defaults.path()).write_text('invalid JSON')
        self.assertNotEqual(self.cli('launch','--window',self.window,'--exe',str(self.exe)).returncode,0)
        self.assertEqual(Path(defaults.path()).read_text(),'invalid JSON')
        self.assertEqual(self.launch('--no-defaults')['argv'],[])

    def test_sidebar_rename_moves_defaults_and_refuses_saved_name_collision(self):
        self.start()
        session=self.t('display-message','-p','#{session_id}')[1].strip()
        defaults.save('app',dict(provider='opencode'))
        self.assertEqual(ta.rename_target(self.prefix,'session',session,'renamed')[0],0)
        self.assertEqual(defaults.get('app'),{})
        self.assertEqual(defaults.get('renamed')['provider'],'opencode')
        defaults.save('reserved',dict(provider='claude'))
        self.assertNotEqual(ta.rename_target(self.prefix,'session',session,'reserved')[0],0)
        self.assertEqual(self.t('display-message','-p','-t',session,'#{session_name}')[1].strip(),'renamed')
        self.assertEqual(defaults.get('reserved')['provider'],'claude')


if __name__=='__main__': unittest.main()
