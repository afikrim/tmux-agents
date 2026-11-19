"""Sizing against private tmux servers; real terminal PIDs must survive."""
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lib import sidebar_layout as layout, ui
from lib.tmuxutil import state_paths

ROOT=Path(__file__).resolve().parents[1]
ta=runpy.run_path(str(ROOT/'bin/tmux-agents'))


class SidebarLayoutTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ta-layout-')
        self.addCleanup(self.temp.cleanup)
        self.prefix=['-S',str(Path(self.temp.name)/'socket')]
        self.environment=patch.dict(os.environ,TMUX='',TMUX_AGENTS_STATE_DIR=str(Path(self.temp.name)/'state'))
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.t('-f','/dev/null','new-session','-d','-s','layout','-x','180','-y','40','sleep','120')
        self.addCleanup(lambda:self.t('kill-server'))
        self.main=self.t('display-message','-p','#{pane_id}').stdout.strip()
        self.window=self.t('display-message','-p','#{window_id}').stdout.strip()
        self.paths=state_paths(self.prefix)

    def t(self,*args):
        return subprocess.run(['tmux',*self.prefix,*args],capture_output=True,text=True,timeout=10)

    def ensure(self): ta['ensure_sidebars'](self.prefix,self.paths)

    def panes(self): return ta['list_all'](self.prefix)

    def side(self,window=None):
        return next((p for p in self.panes() if p.get('sidebar') and
                    p['window_id']==(window or self.window)),None)

    def cli(self,*args):
        return subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),*['--socket-path',self.prefix[1]],
                               'sidebar-width',*args],capture_output=True,text=True,timeout=20)

    def test_manual_width_minimum_and_new_projects_share_preference(self):
        self.ensure()
        original={p['pane_id']:p['pid'] for p in self.panes() if not p.get('sidebar')}
        self.assertEqual(self.side()['width'],32)
        self.t('resize-pane','-t',self.side()['pane_id'],'-x','44'); self.ensure()
        self.assertEqual(layout.preferences(self.paths)['width'],44)
        self.t('new-window','-d','-t','layout','-n','second','sleep','120'); self.ensure()
        self.t('new-session','-d','-s','other','-x','180','-y','40','sleep','120'); self.ensure()
        self.assertTrue(all(p['width']==44 for p in self.panes() if p.get('sidebar')))
        self.t('resize-pane','-t',self.side()['pane_id'],'-x','8'); self.ensure()
        self.assertEqual(layout.preferences(self.paths)['width'],24)
        self.assertTrue(all(p['width']==24 for p in self.panes() if p.get('sidebar')))
        now={p['pane_id']:p['pid'] for p in self.panes()}
        self.assertTrue(all(now[p]==pid for p,pid in original.items()))

    def test_terminal_shrink_collapse_and_restore_never_forget_preference(self):
        self.assertEqual(self.cli('64').returncode,0)
        self.t('resize-window','-t',self.window,'-x','90'); self.ensure()
        self.assertEqual(self.side()['width'],50)
        self.assertEqual(layout.preferences(self.paths)['width'],64)
        self.t('resize-window','-t',self.window,'-x','60'); self.ensure()
        self.assertIsNone(self.side())
        self.t('resize-window','-t',self.window,'-x','180'); self.ensure()
        self.assertEqual(self.side()['width'],64)
        self.assertEqual(layout.preferences(self.paths)['width'],64)

    def test_cli_configuration_overrides_drag_and_is_private_and_validated(self):
        self.ensure()
        self.t('resize-pane','-t',self.side()['pane_id'],'-x','55')
        self.assertEqual(self.cli('32','--json').returncode,0)
        self.assertEqual(self.side()['width'],32)
        self.assertEqual(self.cli('--min-width','40').returncode,0)
        self.assertEqual(self.side()['width'],40)
        before=layout.preferences(self.paths)
        for values in (['20'],['--min-width','0'],['999'],['--reset','30']):
            self.assertNotEqual(self.cli(*values).returncode,0)
            self.assertEqual(layout.preferences(self.paths),before)
        path=Path(self.paths['dir'])/'sidebar-layout.json'
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(self.cli('--reset').returncode,0)
        self.assertEqual(json.loads(self.cli('--json').stdout),layout.DEFAULTS)

    def test_zoom_and_hidden_do_not_change_focus_or_preference(self):
        self.cli('44')
        self.t('select-pane','-t',self.main)
        self.t('resize-pane','-Z','-t',self.main); self.ensure()
        self.assertEqual(self.t('display-message','-p','-t',self.main,'#{window_zoomed_flag}').stdout.strip(),'1')
        self.assertEqual(layout.preferences(self.paths)['width'],44)
        self.t('resize-window','-t',self.window,'-x','100'); self.ensure()
        self.t('resize-pane','-Z','-t',self.main); self.ensure()
        self.assertEqual(self.side()['width'],44)
        self.t('set-window-option','-t',self.window,'@tmux_agents_hidden','1')
        self.t('kill-pane','-t',self.side()['pane_id']); self.ensure()
        self.assertIsNone(self.side())
        self.t('set-window-option','-ut',self.window,'@tmux_agents_hidden'); self.ensure()
        self.assertEqual(self.side()['width'],44)
        self.assertEqual(self.t('display-message','-p','-t',self.window,'#{pane_id}').stdout.strip(),self.main)

    def test_diff_and_real_splits_keep_preference_and_main_space(self):
        self.cli('48')
        original=self.t('display-message','-p','-t',self.main,'#{pane_pid}').stdout.strip()
        diff=self.t('split-window','-fh','-d','-t',self.main,'-l','64','-P','-F','#{pane_id}',
                    'sleep','120').stdout.strip()
        self.t('set-option','-p','-t',diff,'@tmux_agents_diff','1'); self.ensure()
        self.assertEqual(self.side()['width'],48)
        self.t('resize-window','-t',self.window,'-x','120'); self.ensure()
        real=next(p for p in self.panes() if p['pane_id']==self.main)
        self.assertGreaterEqual(real['width'],layout.MIN_MAIN)
        self.assertEqual(layout.preferences(self.paths)['width'],48)
        self.t('kill-pane','-t',diff); self.ensure()
        self.assertEqual(self.side()['width'],48)
        self.t('split-window','-v','-d','-t',self.main,'sleep','120'); self.ensure()
        self.assertEqual(layout.preferences(self.paths)['width'],48)
        self.assertEqual(self.t('display-message','-p','-t',self.main,'#{pane_pid}').stdout.strip(),original)

    def test_linked_windows_and_server_restart_preserve_only_preferences(self):
        self.cli('42')
        self.t('new-session','-d','-s','linked','sleep','120')
        self.t('link-window','-s',self.window,'-t','linked:1'); self.ensure()
        self.assertEqual(len([p for p in self.panes() if p['window_id']==self.window and p.get('sidebar')]),2)
        # The two list-panes records refer to the same physical sidebar.
        self.assertEqual(len({p['pane_id'] for p in self.panes() if p['window_id']==self.window and p.get('sidebar')}),1)
        self.t('kill-server')
        self.t('-f','/dev/null','new-session','-d','-s','fresh','-x','180','-y','40','sleep','120')
        self.ensure()
        self.assertTrue(all(p['width']==42 for p in self.panes() if p.get('sidebar')))

    def test_resized_frames_keep_agent_third_and_current_hit_targets(self):
        sessions=[dict(id='$1',name='工具 project with a long title',attached=True,windows=[
            dict(id='@1',name='work',panes=[dict(id='%1',agent='codex',status='running')])])]
        rows=ui.build_rows(sessions)
        for width,height in ((24,30),(48,60),(24,12),(64,30)):
            lines,_,_,_,hits=ui.frame(rows,sessions,2,0,width,height,return_hits=True)
            self.assertTrue(all(ui.cell_width(re.sub(r'\x1b\[[0-9;]*m','',s))==width for s in lines))
            divider=height-height//3+1
            self.assertIn('──',lines[divider-1])
            y=next(y for y,i in hits.items() if rows[i]['kind']=='pane')
            self.assertGreater(y,divider)
            self.assertEqual(ui.hover_target(rows,hits,width,('mouse',35,width,y,False)),'%1')
            self.assertIsNone(ui.hover_target(rows,hits,width,('mouse',35,width+1,y,False)))
            self.assertEqual(ui.click_target(rows,hits,3,y)['ref'],'%1')

    def test_drag_during_reconciliation_is_not_swallowed_by_snapshot(self):
        self.cli('44')
        remember=layout.remember
        side=self.side()['pane_id']
        def drag_then_remember(*args,**kwargs):
            self.t('resize-pane','-t',side,'-x','12')
            # Refresh the final topology just as an in-flight mouse drag can
            # change it between the policy decision and the cached snapshot.
            args=list(args); args[1]=self.panes()
            return remember(*args,**kwargs)
        with patch.object(layout,'remember',side_effect=drag_then_remember):self.ensure()
        self.ensure()
        self.assertEqual(layout.preferences(self.paths)['width'],24)
        self.assertEqual(self.side()['width'],24)
