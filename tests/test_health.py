import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from lib import adapters, health, store, status
from lib.tmuxutil import state_paths

ROOT=Path(__file__).resolve().parents[1]


class HealthTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ta-health-')
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.env=dict(os.environ,HOME=str(root),TMUX_AGENTS_STATE_DIR=str(root/'state'),
            PYTHONDONTWRITEBYTECODE='1',
            TMUX_AGENTS_CLAUDE_SETTINGS=str(root/'claude.json'),
            TMUX_AGENTS_CODEX_HOOKS=str(root/'codex.json'),TMUX_AGENTS_OPENCODE_JSONC=str(root/'open.json'))
        self.env.pop('TMUX',None); self.env.pop('TMUX_PANE',None)
        self.guard=patch.dict(os.environ,self.env,clear=True)
        self.guard.start(); self.addCleanup(self.guard.stop)
        self.prefix=['-S',str(root/'socket')]
        self.paths=state_paths(self.prefix)
        self.now=time.time()
        self.dependencies=dict(ok=True,checks=[],encoding='UTF-8',git='/tools/git',
            agents={p:'/tools/'+p for p in health.PROVIDERS})

    def report(self,events=(),state='unknown',generation='123:456.0',updated=None):
        store.atomic_write(self.paths['state'],dict(updated=self.now if updated is None else updated,
            panes={'%1':dict(agent='codex',generation=generation,status=state,source='')}))
        for event in events:
            values=dict(agent='codex',agent_pid=123,agent_start=456.,socket_id=self.paths['sid'])
            values.update(event)
            kind=values.pop('kind','running')
            store.append_event(self.paths,'%1',kind,**values)
        with patch.object(health,'run_tmux',return_value=(0,'socket','')):
            return health.collect(self.prefix,self.paths,
                [dict(pane='%1',provider='codex',pid=123,start=456.)],42,self.dependencies,self.now)

    def cli(self,*args):
        return subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'--socket-path',self.prefix[1],
            *args],env=self.env,capture_output=True,text=True,timeout=30)

    def t(self,*args):
        return subprocess.run(['tmux',*self.prefix,*args],env=self.env,capture_output=True,text=True,timeout=10)

    def test_configuration_presence_is_separate_from_delivery_and_secrets(self):
        self.assertEqual(health.integration('codex')['status'],'not-configured')
        for provider in health.PROVIDERS:
            adapters.integrate(provider)
            self.assertEqual(health.integration(provider)['status'],'configured')
        path=Path(self.env['TMUX_AGENTS_CODEX_HOOKS'])
        config=json.loads(path.read_text()); config['apiKey']='SECRET_MUST_NOT_APPEAR'
        path.write_text(json.dumps(config))
        report=self.report()
        self.assertEqual(report['providers'][0]['delivery'],'unverified')
        self.assertNotIn('SECRET_MUST_NOT_APPEAR',json.dumps(report)+health.render(report))
        del config['hooks']['SessionStart']
        path.write_text(json.dumps(config))
        result=health.integration('codex')
        self.assertEqual(result['status'],'partial')
        self.assertIn('SessionStart',result['missing_events'])

    def test_bad_configs_and_other_installations_are_reported_without_raw_contents(self):
        path=Path(self.env['TMUX_AGENTS_CODEX_HOOKS'])
        path.write_text('{"secret":"SECRET_MUST_NOT_APPEAR",broken')
        self.assertEqual(health.integration('codex')['status'],'invalid')
        path.write_text(json.dumps({'hooks':{'SessionStart':[{'hooks':[{'type':'command',
            'command':'python3 /old/hooks/agent-event --agent codex'}]}]}}))
        self.assertEqual(health.integration('codex')['status'],'different-installation')
        Path(self.env['TMUX_AGENTS_OPENCODE_JSONC']).write_text('{// comment\n"plugins":"bad"}')
        self.assertEqual(health.integration('opencode')['status'],'invalid')

    def test_manual_and_legacy_events_do_not_prove_native_hook_delivery(self):
        report=self.report([dict(origin='manual',ts=self.now-1)])
        provider=report['providers'][0]
        self.assertEqual(provider['delivery'],'unverified')
        self.assertEqual(provider['last_event']['origin'],'manual')
        self.assertIsNone(provider['last_hook'])
        self.assertEqual(report['agents'][0]['hook_delivery'],'unverified')
        self.assertIn('signal',report['agents'][0]['reason'])

    def test_hook_from_old_process_is_historical_not_current_connectivity(self):
        report=self.report([dict(origin='hook',agent_pid=999,ts=self.now-1)])
        self.assertEqual(report['providers'][0]['delivery'],'observed')
        self.assertEqual(report['agents'][0]['hook_delivery'],'unverified')
        self.assertIn('not bound to this agent process',report['agents'][0]['reason'])

    def test_current_delivery_expired_events_and_stale_snapshots_have_honest_reasons(self):
        report=self.report([dict(origin='hook',ts=self.now-1)],state='running')
        self.assertEqual(report['agents'][0]['hook_delivery'],'observed')
        self.assertEqual(report['agents'][0]['reason'],'')
        os.unlink(self.paths['events'])
        report=self.report([dict(origin='hook',ts=self.now-900)],updated=self.now-20)
        self.assertIn('expired',report['agents'][0]['reason'])
        self.assertFalse(report['runtime']['snapshot_fresh'])
        self.assertTrue(any('stale' in warning for warning in report['warnings']))
        self.assertIn('expired',status.unknown_reason(dict(kind='running',ts=self.now-900),True,self.now))
        report=self.report(generation='old-process')
        self.assertIn('No current daemon snapshot',report['agents'][0]['reason'])
        report=self.report([dict(origin='hook',ts=self.now-.1)],updated=self.now-.5)
        self.assertIn('after the last snapshot',report['agents'][0]['reason'])

    def test_no_server_doctor_is_read_only_and_strict_reports_bad_config(self):
        before=set(Path(self.temp.name).rglob('*'))
        result=self.cli('doctor','--json')
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(result.stdout)
        self.assertFalse(report['runtime']['server'])
        self.assertEqual(before,set(Path(self.temp.name).rglob('*')))
        Path(self.env['TMUX_AGENTS_CLAUDE_SETTINGS']).write_text('invalid SECRET_MUST_NOT_APPEAR')
        result=self.cli('doctor','--strict','--json')
        self.assertEqual(result.returncode,1,result.stderr)
        self.assertTrue(json.loads(result.stdout)['warnings'])
        self.assertNotIn('SECRET_MUST_NOT_APPEAR',result.stdout+result.stderr)
        self.assertEqual(self.cli('doctor','--dependencies-only').returncode,0)
        missing=subprocess.run([sys.executable,str(ROOT/'bin/tmux-agents'),'doctor','--json'],
            env=dict(self.env,PATH='/no/tmux-agents-test-tools'),capture_output=True,text=True,timeout=10)
        self.assertEqual(missing.returncode,1,missing.stderr)
        self.assertEqual(json.loads(missing.stdout)['runtime']['daemon'],'unavailable')

    def test_real_hook_delivery_and_doctor_json_do_not_change_session_or_state(self):
        agent=Path(self.temp.name)/'codex'
        agent.write_text('#!/bin/sh\nwhile true; do sleep 1; done\n'); agent.chmod(0o755)
        self.assertEqual(self.t('-f','/dev/null','new-session','-d','-s','health',str(agent)).returncode,0)
        self.addCleanup(lambda:self.t('kill-server'))
        adapters.integrate('codex')
        time.sleep(.2)
        self.assertEqual(self.cli('daemon','--once').returncode,0)
        initial=json.loads(self.cli('doctor','--json').stdout)
        self.assertEqual(initial['agents'][0]['hook_delivery'],'unverified')
        self.assertTrue(initial['agents'][0]['reason'])
        pane=self.t('display-message','-p','#{pane_id}').stdout.strip()
        socket=self.t('display-message','-p','#{socket_path},#{pid},0').stdout.strip()
        payload=dict(hook_event_name='UserPromptSubmit',session_id='health-test',prompt='SECRET_CHAT')
        result=subprocess.run([sys.executable,str(ROOT/'hooks/agent-event'),'--agent','codex'],
            input=json.dumps(payload),env=dict(self.env,TMUX=socket,TMUX_PANE=pane),
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.cli('daemon','--once').returncode,0)
        paths=state_paths(self.prefix)
        files=[Path(paths['state']),Path(paths['events']),Path(self.env['TMUX_AGENTS_CODEX_HOOKS'])]
        before={str(p):(p.stat().st_mtime_ns,p.read_bytes()) for p in files}
        topology=self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout
        result=self.cli('doctor','--json')
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads(result.stdout)
        self.assertEqual(report['agents'][0]['hook_delivery'],'observed')
        self.assertEqual(report['agents'][0]['status'],'running')
        self.assertNotIn('SECRET_CHAT',result.stdout+result.stderr)
        self.assertEqual(before,{str(p):(p.stat().st_mtime_ns,p.read_bytes()) for p in files})
        self.assertEqual(topology,self.t('list-panes','-a','-F','#{pane_id}|#{pane_pid}').stdout)


if __name__=='__main__': unittest.main()
