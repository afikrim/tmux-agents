"""Native tool -> safe filename -> live caption, with no model calls."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from lib import activity, store
from lib.tmuxutil import state_paths

ROOT=Path(__file__).resolve().parents[1]
ta=runpy.run_path(str(ROOT/'bin/tmux-agents'))


class TargetMetadataTest(unittest.TestCase):
    def test_native_fields_patch_headers_and_post_tool_working_target(self):
        for tool,field,path,expected in (
            ('Edit','file_path','/private/project/app.py','Editing app.py'),
            ('Read','filePath','/home/user/README.md','Reading README.md'),
            ('Write','path','C:\\code\\index.ts','Editing index.ts'),
            ('NotebookEdit','notebook_path','/data/experiment.ipynb','Editing experiment.ipynb'),
            ('Grep','path','/project/src','Searching src')):
            payload=dict(tool_name=tool,tool_input={field:path,'old_string':'PRIVATE CONTENT'})
            target=activity.hook_target('PreToolUse',payload)
            self.assertEqual(activity.caption('running',activity.hook_action('PreToolUse',payload),
                                               target=target),expected)
            self.assertEqual(activity.caption('running','Thinking',target=activity.hook_target('PostToolUse',payload)),
                             'Working on '+target)
        patch_text='*** Begin Patch\n*** Update File: /private/src/app.py\n@@\n-SECRET OLD\n+SECRET NEW\n*** End Patch'
        for data in (patch_text,dict(input=patch_text),dict(patch=patch_text),
                     json.dumps(dict(input=patch_text))):
            self.assertEqual(activity.hook_target('PreToolUse',dict(tool_name='apply_patch',tool_input=data)),'app.py')
        self.assertEqual(activity.hook_target('PreToolUse',dict(tool_name='read',tool_target='/src/工具.py')),'工具.py')

    def test_unsafe_targets_and_non_file_tools_never_expose_payloads(self):
        for value in ('',None,{},['file'],'.','..','/', 'https://host/name?secret',
                      '/tmp/evil\x1b[31m.py','/tmp/bidi\u202epy','x'*300):
            self.assertEqual(activity.safe_target(value),'')
        for tool in ('Bash','request_user_input','web_fetch','unknown'):
            payload=dict(tool_name=tool,tool_input=dict(file_path='/private/token',command='secret',query='secret'))
            self.assertEqual(activity.hook_target('PreToolUse',payload),'')
        self.assertEqual(activity.hook_target('Stop',dict(tool_name='Edit',tool_target='app.py')),'')
        for status in ('idle','question','needs-input','needs-approval','unknown','done'):
            self.assertNotIn('app.py',activity.caption(status,'Editing files',target='app.py'))

    def test_event_log_keeps_only_bounded_basename_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            paths=dict(dir=temp,events=str(Path(temp)/'events.jsonl'))
            store.append_event(paths,'%1','running',action='Editing files',target='/private/project/app.py')
            raw=Path(paths['events']).read_text()
            self.assertIn('app.py',raw); self.assertNotIn('/private',raw)
            store.append_event(paths,'%1','running',action='Reading files',target='evil\x1b.py')
            self.assertNotIn('evil',Path(paths['events']).read_text())


class NativeCaptionTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ta-action-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.prefix=['-S',str(self.root/'socket')]
        self.env=patch.dict(os.environ,HOME=str(self.root),TMUX='',TMUX_PANE='',
                            TMUX_AGENTS_STATE_DIR=str(self.root/'state'))
        self.env.start(); self.addCleanup(self.env.stop)
        self.t('-f','/dev/null','new-session','-d','-s','actions','sleep','120')
        self.addCleanup(lambda:self.t('kill-server'))
        self.paths=state_paths(self.prefix)

    def t(self,*args):
        return subprocess.run(['tmux',*self.prefix,*args],capture_output=True,text=True,timeout=10)

    def agent(self,provider):
        script=self.root/provider
        script.write_text('#!/bin/sh\nwhile true; do sleep 1; done\n'); script.chmod(0o755)
        pane=self.t('new-window','-d','-t','actions','-P','-F','#{pane_id}',str(script)).stdout.strip()
        time.sleep(.2)
        return pane

    def hook(self,pane,provider,event,**fields):
        env=dict(os.environ,TMUX=self.prefix[1]+',1,0',TMUX_PANE=pane)
        result=subprocess.run([sys.executable,str(ROOT/'hooks/agent-event'),'--agent',provider],
                  env=env,input=json.dumps(dict(hook_event_name=event,**fields)),
                  text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        return ta['daemon_once'](self.prefix,self.paths)['panes'][pane]

    def test_codex_claude_and_opencode_targets_reach_live_sidebar_captions(self):
        for provider,tool,data in (
            ('claude','Edit',dict(tool_input=dict(file_path='/private/project/app.py',old_string='SECRET CONTENT'))),
            ('codex','apply_patch',dict(tool_input=dict(input='*** Begin Patch\n*** Update File: /private/project/app.py\n@@\n+SECRET CONTENT\n*** End Patch'))),
            ('opencode','edit',dict(tool_target='app.py'))):
            pane=self.agent(provider)
            state=self.hook(pane,provider,'PreToolUse',tool_name=tool,**data)
            self.assertEqual(state['caption'],'Editing app.py')
            self.assertEqual(state['agent'],provider)
            state=self.hook(pane,provider,'PostToolUse',tool_name=tool,**data)
            self.assertEqual(state['caption'],'Working on app.py')
            state=self.hook(pane,provider,'Stop')
            self.assertNotIn('app.py',state['caption'])
            self.assertIn('zzZ',state['caption'])
        raw=Path(self.paths['events']).read_text()
        self.assertNotIn('SECRET CONTENT',raw); self.assertNotIn('/private/project',raw)
        self.assertNotIn('tool_input',raw)

    def test_attention_expiry_and_replacement_clear_file_detail(self):
        pane=self.agent('claude')
        info=self.hook(pane,'claude','PreToolUse',tool_name='Read',tool_input=dict(file_path='/tmp/README.md'))
        self.assertEqual(info['caption'],'Reading README.md')
        waiting=self.hook(pane,'claude','PermissionRequest',tool_name='Edit',tool_input=dict(file_path='/tmp/app.py'))
        self.assertIn('Need approval',waiting['caption']); self.assertNotIn('app.py',waiting['caption'])
        asking=self.hook(pane,'claude','PreToolUse',tool_name='AskUserQuestion',tool_input=dict(file_path='/tmp/app.py'))
        self.assertIn('Need input',asking['caption']); self.assertNotIn('app.py',asking['caption'])
        pid,start=info['generation'].split(':')
        store.append_event(self.paths,pane,'running',agent='claude',agent_pid=int(pid),agent_start=float(start),
                           socket_id=self.paths['sid'],ts=time.time()-8000,action='Editing files',target='app.py')
        self.assertNotIn('app.py',ta['daemon_once'](self.prefix,self.paths)['panes'][pane]['caption'])
        self.hook(pane,'claude','PreToolUse',tool_name='Edit',tool_input=dict(file_path='/tmp/app.py'))
        self.t('respawn-pane','-k','-t',pane,'sleep','120')
        self.assertNotIn('app.py',ta['daemon_once'](self.prefix,self.paths)['panes'][pane]['caption'])
