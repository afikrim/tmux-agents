"""Read-only integration diagnostics. Configuration is not proof of hook delivery."""
import json
import os
import time

from . import adapters, store, status, ui
from .tmuxutil import run_tmux, claude_settings_path, codex_hooks_path, opencode_config_path

PROVIDERS=('codex','claude','opencode')


def integration(provider):
    path={'codex':codex_hooks_path,'claude':claude_settings_path,'opencode':opencode_config_path}[provider]()
    result=dict(provider=provider,path=path,status='not-configured',missing_events=[],
                action='Run tmux-agents integrate '+provider)
    try:
        with open(path) as f: raw=f.read(1024*1024+1)
    except FileNotFoundError:
        return result
    except OSError:
        return dict(result,status='unreadable')
    if len(raw)>1024*1024:
        return dict(result,status='invalid',action='Configuration exceeds the 1 MiB diagnostic limit; inspect it manually')
    try:
        root=json.loads(adapters.strip_jsonc(raw) if provider=='opencode' else raw)
    except ValueError:
        return dict(result,status='invalid',action='Repair invalid JSON in this configuration, then rerun doctor')
    if not isinstance(root,dict):
        return dict(result,status='invalid')
    if provider=='opencode':
        _,plugins=adapters._plugin_list(root)
        if plugins is None and any(key in root for key in ('plugins','plugin')):
            return dict(result,status='invalid')
        plugins=plugins or []
        packages=[p.get('package','') if isinstance(p,dict) else p for p in plugins]
        if any(not isinstance(p,str) for p in packages):
            return dict(result,status='invalid')
        matched=adapters.PLUGIN_DIR in packages
        if matched:
            present=all(os.path.isfile(os.path.join(adapters.PLUGIN_DIR,name)) for name in ('package.json','tui.ts'))
            return dict(result,status='configured' if present else 'broken-runtime',
                        action='Restart existing OpenCode sessions to load plugin changes' if present else 'Reinstall tmux-agents to restore plugin files')
        if any(isinstance(p,str) and 'opencode-tmux-agents' in p for p in packages):
            return dict(result,status='different-installation')
        return result
    hooks=root.get('hooks',{})
    if not isinstance(hooks,dict):
        return dict(result,status='invalid')
    expected=adapters.CODEX_EVENTS if provider=='codex' else adapters.CLAUDE_EVENTS
    found=[]; other=False
    for name in expected:
        groups=hooks.get(name,[])
        if not isinstance(groups,list): return dict(result,status='invalid')
        for group in groups:
            if not isinstance(group,dict) or not isinstance(group.get('hooks',[]),list):
                return dict(result,status='invalid')
            for hook in group.get('hooks',[]):
                if not isinstance(hook,dict): return dict(result,status='invalid')
                command=hook.get('command','')
                if hook.get('type')=='command' and command==adapters.hook_command(provider):
                    found.append(name)
                elif isinstance(command,str) and 'agent-event' in command and '--agent '+provider in command:
                    other=True
    missing=[name for name in expected if name not in found]
    if found:
        state='partial' if missing else 'configured'
        if not os.path.isfile(adapters.HOOK_SCRIPT): state='broken-runtime'
        action=('Open /hooks in Codex and verify handler trust if events are not arriving'
                if provider=='codex' and not missing else result['action'] if missing else 'Wait for the next native lifecycle event to confirm delivery')
        return dict(result,status=state,missing_events=missing,action=action)
    return dict(result,status='different-installation' if other else 'not-configured')


def _bound(event,agent):
    try:
        return (event.get('agent')==agent['provider'] and event.get('pane')==agent['pane']
                and event.get('apid')==agent['pid'] and bool(agent['start'])
                and abs(float(event.get('astart',0))-agent['start'])<2)
    except (ValueError,TypeError):
        return False


def _event(event,now):
    if not event: return None
    kind=event.get('kind','')
    if kind not in ('running','idle','done','unknown','needs-input','needs-approval','question'):
        kind='unrecognized'
    origin=event.get('origin') if event.get('origin') in ('hook','manual') else 'unverified'
    return dict(timestamp=float(event['t']),age_seconds=max(0,int(now-float(event['t']))),
                kind=kind,origin=origin)


def collect(prefix,paths,live,daemon_pid,dependencies,now=None):
    now=time.time() if now is None else now
    warnings=[]
    server=run_tmux(prefix,'display-message','-p','#{socket_path}')[0]==0
    installed=os.path.isfile(paths['install'])
    disabled=os.path.exists(os.path.join(paths['dir'],'disabled'))
    daemon=('no-server' if not server else 'disabled' if disabled else 'running' if daemon_pid
            else 'not-running' if installed else 'not-installed')
    if daemon=='not-running': warnings.append('Installed sidebar daemon is not running; run tmux-agents daemon-start')
    snapshot=store.read_state(paths)
    if not isinstance(snapshot,dict):
        snapshot={}
        warnings.append('Daemon snapshot is invalid')
    try: age=now-float(snapshot.get('updated',0))
    except (ValueError,TypeError): age=-1
    fresh=0<=age<=8
    if live and not fresh:
        warnings.append('Agent snapshot is missing or stale; check the daemon before trusting displayed status')
    try:
        events=[e for e in store.read_events(paths,max_age=365*86400)
                if e.get('sock','') in ('',paths['sid'])]
    except (OSError,ValueError,TypeError,AttributeError):
        events=[]
        warnings.append('Lifecycle event log is unreadable or invalid')
    providers=[]; agents=[]
    for provider in PROVIDERS:
        config=integration(provider)
        observed=[e for e in events if e.get('agent')==provider]
        hooks=[e for e in observed if e.get('origin')=='hook']
        last=max(observed,key=lambda e:float(e['t']),default=None)
        last_hook=max(hooks,key=lambda e:float(e['t']),default=None)
        active=[a for a in live if a['provider']==provider]
        config.update(executable=dependencies['agents'].get(provider),
                      last_event=_event(last,now),last_hook=_event(last_hook,now),
                      delivery='observed' if last_hook else 'unverified',active_agents=len(active))
        if config['status'] not in ('configured','not-configured'):
            warnings.append(provider+': '+config['status']+' integration; '+config['action'])
        for agent in active:
            current_hook=max((e for e in hooks if _bound(e,agent)),key=lambda e:float(e['t']),default=None)
            latest=max((e for e in observed if e.get('pane')==agent['pane']),key=lambda e:float(e['t']),default=None)
            states=snapshot.get('panes',{})
            info=states.get(agent['pane'],{}) if isinstance(states,dict) else {}
            if not isinstance(info,dict): info={}
            generation='%s:%s'%(agent['pid'],agent['start'])
            same=info.get('generation')==generation
            state=info.get('status','unknown') if same else 'unknown'
            if state not in ('running','idle','done','unknown','needs-input','needs-approval','question'):
                state='unknown'
            reason=''
            if not same:
                reason='No current daemon snapshot for this agent process'
            elif state=='unknown':
                event=dict(kind=latest.get('kind'),ts=latest.get('t')) if latest else None
                bound=_bound(latest,agent) if latest else False
                if fresh and bound and float(latest['t'])>now-age:
                    reason='New lifecycle event arrived after the last snapshot; wait for the next daemon poll'
                else:
                    reason=status.unknown_reason(event,bound,now)
            if not current_hook:
                warnings.append(provider+' '+agent['pane']+': native hook delivery has not been confirmed for this process; '+config['action'])
            if reason: warnings.append(provider+' '+agent['pane']+': '+reason)
            source=info.get('source','') if same else ''
            if source not in ('event','process','screen','inferred',''): source=''
            agents.append(dict(pane=agent['pane'],provider=provider,status=state,
                source=source,snapshot_fresh=fresh,
                hook_delivery='observed' if current_hook else 'unverified',
                last_hook=_event(current_hook,now),reason=reason))
        providers.append(config)
    return dict(dependencies=dependencies,runtime=dict(server=server,daemon=daemon,
                snapshot_fresh=fresh,snapshot_age_seconds=int(age) if age>=0 else None),
                providers=providers,agents=agents,warnings=warnings)


def render(report):
    lines=['Runtime: daemon '+report['runtime']['daemon']]
    for provider in report['providers']:
        last=provider['last_hook']
        seen=str(last['age_seconds'])+'s ago' if last else 'no retained verified hook event'
        lines.append(provider['provider']+': '+provider['status']+'; hook delivery '+provider['delivery']+'; last hook '+seen)
        if provider['missing_events']: lines.append('  Missing events: '+', '.join(provider['missing_events']))
        if provider['last_event']:
            event=provider['last_event']
            lines.append('  Last lifecycle event: '+str(event['age_seconds'])+'s ago; '+event['kind']+'; origin '+event['origin'])
        lines.append('  '+provider['action'])
    for agent in report['agents']:
        lines.append(agent['provider']+' '+agent['pane']+': '+agent['status']+'; source '+(agent['source'] or 'none')+'; hooks '+agent['hook_delivery']+'; '+(agent['reason'] or 'current status evidence available'))
    lines.extend('WARN: '+warning for warning in report['warnings'])
    return '\n'.join(ui.san(line,0) for line in lines)
