import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from lib import worktrees

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('ta_worktrees',str(ROOT/'bin/tmux-agents'))
spec=importlib.util.spec_from_loader('ta_worktrees',loader)
ta=importlib.util.module_from_spec(spec)
loader.exec_module(ta)


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ta worktree ')
        self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)/'source repo'
        self.repo.mkdir()
        self.guard=patch.dict(os.environ,{'TMUX_AGENTS_STATE_DIR':str(Path(self.temp.name)/'state')})
        self.guard.start(); self.addCleanup(self.guard.stop)
        self.git('init','-q')
        self.git('config','user.name','Test')
        self.git('config','user.email','test@example.invalid')
        self.git('config','commit.gpgsign','false')
        (self.repo/'service').mkdir()
        (self.repo/'service'/'file').write_text('committed\n')
        self.git('add','.')
        self.git('commit','-qm','Initial')
        self.prefix=['-S',str(Path(self.temp.name)/'socket')]

    def git(self,*args):
        result=worktrees.git(str(self.repo),*args)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout.strip()

    def start(self):
        result=ta.run_tmux(self.prefix,'-f','/dev/null','new-session','-d','-s','app',
                           '-x','180','-y','40','-c',str(self.repo),'sleep','60')
        self.assertEqual(result[0],0,result[2])
        self.addCleanup(lambda:ta.run_tmux(self.prefix,'kill-server'))
        self.executable=Path(self.temp.name)/'fake agent'
        self.executable.write_text('#!/bin/sh\nwhile true; do sleep 1; done\n')
        self.executable.chmod(0o755)

    def args(self,**changes):
        fields=dict(kind='codex',exe=str(self.executable),launch_args=[],env=[],window='',
                    project='',directory=str(self.repo),client='',worktree=True,branch='')
        fields.update(changes)
        return SimpleNamespace(**fields)

    def test_dirty_source_is_untouched_and_tracked_subdirectory_is_preserved(self):
        (self.repo/'service'/'file').write_text('staged\n')
        self.git('add','service/file')
        (self.repo/'service'/'file').write_text('unstaged\n')
        (self.repo/'untracked').write_text('keep me')
        before=(self.git('status','--porcelain'),self.git('diff','--cached'),self.git('rev-parse','HEAD'))
        record=worktrees.create(str(self.repo/'service'),'codex','agents/auth')
        self.assertEqual(Path(record['directory']).name,'service')
        self.assertEqual((Path(record['directory'])/'file').read_text(),'committed\n')
        self.assertEqual(worktrees.git(record['path'],'branch','--show-current').stdout.strip(),'agents/auth')
        self.assertEqual(before,(self.git('status','--porcelain'),self.git('diff','--cached'),self.git('rev-parse','HEAD')))

    def test_invalid_existing_branches_and_non_git_paths_create_nothing(self):
        before=self.git('worktree','list','--porcelain')
        for branch in ('--bad','bad..name','bad name'):
            with self.assertRaises(ValueError): worktrees.create(str(self.repo),'codex',branch)
        self.git('branch','agents/existing')
        with self.assertRaises(ValueError): worktrees.create(str(self.repo),'codex','agents/existing')
        with self.assertRaises(ValueError): worktrees.create(self.temp.name,'codex')
        (self.repo/'untracked-dir').mkdir()
        with self.assertRaises(ValueError): worktrees.create(str(self.repo/'untracked-dir'),'codex','agents/missing-subdir')
        self.assertNotIn('agents/missing-subdir',self.git('branch','--list'))
        self.assertEqual(before,self.git('worktree','list','--porcelain'))

    def test_rollback_keeps_user_changes_and_removes_only_untouched_checkout(self):
        record=worktrees.create(str(self.repo),'claude')
        (Path(record['path'])/'newfile').write_text('user work')
        self.assertFalse(worktrees.rollback(record))
        self.assertTrue(Path(record['path']).exists())
        self.assertEqual((Path(record['path'])/'newfile').read_text(),'user work')
        clean=worktrees.create(str(self.repo),'opencode')
        self.assertTrue(worktrees.rollback(clean))
        self.assertFalse(Path(clean['path']).exists())
        self.assertNotIn(clean['branch'],self.git('branch','--list'))

    def test_launch_all_providers_in_separate_checkouts_and_normal_launch_unchanged(self):
        self.start()
        for provider in ('codex','claude','opencode'):
            self.assertEqual(ta.cmd_launch(self.args(kind=provider),self.prefix),0)
            path=ta.run_tmux(self.prefix,'display-message','-p','#{pane_current_path}')[1].strip()
            self.assertNotEqual(os.path.realpath(path),str(self.repo.resolve()))
            self.assertTrue(worktrees.git(path,'branch','--show-current').stdout.strip().startswith('agents/'+provider+'-'))
        self.assertEqual(ta.cmd_launch(self.args(worktree=False),self.prefix),0)
        path=ta.run_tmux(self.prefix,'display-message','-p','#{pane_current_path}')[1].strip()
        self.assertEqual(os.path.realpath(path),str(self.repo.resolve()))

    def test_failed_tmux_launch_rolls_back_new_worktree_and_branch(self):
        self.start()
        before=self.git('worktree','list','--porcelain')
        original=ta.run_tmux
        def fail(prefix,*args,**kwargs):
            return (1,'','simulated tmux failure') if args[0]=='new-window' else original(prefix,*args,**kwargs)
        with patch.object(ta,'run_tmux',side_effect=fail):
            self.assertEqual(ta.cmd_launch(self.args(branch='agents/failed'),self.prefix),1)
        self.assertEqual(before,self.git('worktree','list','--porcelain'))
        self.assertNotIn('agents/failed',self.git('branch','--list'))

    def test_sidebar_launch_uses_last_terminal_subdirectory(self):
        self.start()
        _,last,_=ta.run_tmux(self.prefix,'split-window','-d','-P','-F','#{pane_id}',
                             '-c',str(self.repo/'service'),'sleep','60')
        _,sidebar,_=ta.run_tmux(self.prefix,'split-window','-d','-P','-F','#{pane_id}','sleep','60')
        ta.run_tmux(self.prefix,'set-option','-p','-t',sidebar.strip(),'@tmux_agents_sidebar','1')
        ta.run_tmux(self.prefix,'set-window-option','@tmux_agents_tab_pane',last.strip())
        ta.run_tmux(self.prefix,'select-pane','-t',sidebar.strip())
        self.assertEqual(ta.cmd_launch(self.args(directory=''),self.prefix),0)
        path=ta.run_tmux(self.prefix,'display-message','-p','#{pane_current_path}')[1].strip()
        self.assertEqual(Path(path).name,'service')
        self.assertNotEqual(Path(path).resolve(),(self.repo/'service').resolve())

    def test_missing_executable_and_duplicate_project_do_not_create_worktrees(self):
        self.start()
        before=self.git('worktree','list','--porcelain')
        self.assertEqual(ta.cmd_launch(self.args(exe='/missing/agent'),self.prefix),1)
        self.assertEqual(ta.cmd_launch(self.args(project='app'),self.prefix),1)
        self.assertEqual(before,self.git('worktree','list','--porcelain'))


if __name__=='__main__': unittest.main()
