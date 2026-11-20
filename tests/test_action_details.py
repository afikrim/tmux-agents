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
                     dict(command=patch_text),
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
            ('codex','apply_patch',dict(tool_input=dict(command='*** Begin Patch\n*** Update File: /private/project/app.py\n@@\n+SECRET CONTENT\n*** End Patch'))),
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

    def test_all_native_tool_names_and_task_summaries_reach_captions(self):
        pane=self.agent('claude')
        cases=[('Bash',dict(command='pytest --token SECRET',description='Run regression tests'),'Running · Run regression tests'),
               ('Agent',dict(description='Review auth flow',prompt='PRIVATE DELEGATE PROMPT'),'Delegating · Review auth flow'),
               ('WebFetch',dict(url='https://example.com/private?token=SECRET'),'Browsing example.com'),
               ('mcp__github__create_pull_request',dict(body='PRIVATE PR BODY'*10000),'Github · Create pull request'),
               ('TodoWrite',dict(todos=[dict(content='PRIVATE TASK CONTENT')]),'Updating tasks'),
               ('update_plan',dict(plan=[dict(step='PRIVATE PLAN')]),'Planning')]
        for tool,data,expected in cases:
            info=self.hook(pane,'claude','PreToolUse',tool_name=tool,tool_input=data)
            self.assertEqual(info['caption'],expected)
        failed=self.hook(pane,'claude','PostToolUseFailure',tool_name='Bash',
                         tool_input=dict(command='pytest'),error='PRIVATE ERROR')
        self.assertEqual(failed['caption'],'Tool failed · Bash')
        started=self.hook(pane,'claude','SubagentStart',agent_type='Explore')
        self.assertEqual(started['caption'],'Delegating · Explore')
        stopped=self.hook(pane,'claude','SubagentStop',agent_type='Explore')
        self.assertEqual(stopped['status'],'running')
        compact=self.hook(pane,'claude','PreCompact')
        self.assertEqual(compact['caption'],'Compacting context')
        raw=Path(self.paths['events']).read_text()
        for secret in ('SECRET','PRIVATE DELEGATE PROMPT','PRIVATE PR BODY','PRIVATE TASK CONTENT','PRIVATE ERROR'):
            self.assertNotIn(secret,raw)


class HarnessActivityTest(unittest.TestCase):
    def test_long_activity_caption_scrolls_without_changing_hit_map(self):
        from lib import ui
        sessions=[dict(id='$1',name='project',attached=True,windows=[dict(id='@1',panes=[
            dict(id='%1',agent='codex',status='running',caption='Running · Run regression tests for all providers')])])]
        rows=ui.build_rows(sessions)
        first=ui.frame(rows,sessions,2,0,24,30,return_hits=True,caption_elapsed=0)
        later=ui.frame(rows,sessions,2,0,24,30,return_hits=True,caption_elapsed=4)
        caption_y=max(y for y,i in first[-1].items() if rows[i]['kind']=='pane')
        self.assertNotEqual(first[0][caption_y-1],later[0][caption_y-1])
        self.assertEqual(dict(first[-1]),dict(later[-1]))

    def test_dynamic_tools_commands_and_descriptions(self):
        for tool,data,expected in (
            ('Bash',dict(command='rtk proxy python3 -m pytest --token SECRET'),'Running · pytest'),
            ('exec_command',dict(cmd='git status --short'),'Running · git status'),
            ('mcp__new_service__perform_new_operation',dict(description='PRIVATE BODY'),'New service · Perform new operation'),
            ('mcp__database__execute_query',dict(description='PRIVATE BODY',command='SECRET'),'Running · Database · Execute query')):
            payload=dict(tool_name=tool,tool_input=data)
            meta=activity.hook_metadata('PreToolUse',payload)
            self.assertEqual(activity.caption('running',activity.hook_action('PreToolUse',payload),
                             **meta),expected)
        self.assertEqual(activity.safe_detail('Run tests token=SECRET'),'Run tests token=<redacted>')
        self.assertEqual(activity.safe_detail('Read /private/token'),'')
        self.assertEqual(activity.safe_detail('multiline\nbody'),'')

    def test_native_running_overrides_passive_composer_but_not_attention_widget(self):
        from lib import status
        ev=dict(kind='running',ts=time.time())
        self.assertEqual(status.classify('codex',ev,'› ',True,'idle',False,True),('running','event'))
        self.assertEqual(status.classify('codex',ev,'Allow execution? [y/n]',True,'idle',False,True),('needs-approval','screen'))
        self.assertEqual(status.classify('codex',ev,'› ',True,'idle',False,False),('idle','screen'))
