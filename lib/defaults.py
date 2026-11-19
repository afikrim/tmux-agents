"""Explicit, private project launch preferences. No repository config is executed."""
import fcntl
import json
import os

from . import store
from .tmuxutil import state_root, run_tmux

PROVIDERS=('codex','claude','opencode')


def path():
    return os.path.join(state_root(),'project-defaults.json')


def project_name(prefix,window='',explicit=''):
    if explicit: return explicit
    return run_tmux(prefix,'display-message','-p',*(['-t',window] if window else []),'#{session_name}')[1].strip()


def _read():
    try:
        with open(path()) as f: raw=f.read(64*1024*1024+1)
    except FileNotFoundError:
        return {}
    if len(raw)>64*1024*1024: raise ValueError('Project defaults file is too large: '+path())
    try: records=json.loads(raw)
    except ValueError: raise ValueError('Invalid project defaults JSON: '+path()) from None
    if not isinstance(records,dict) or len(records)>1000:
        raise ValueError('Invalid project defaults object: '+path())
    return records


def _text(value,label,limit):
    if not isinstance(value,str) or len(value.encode())>limit or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError(label+' must be text without controls (max '+str(limit)+' bytes)')
    return value


def validate(record):
    if not isinstance(record,dict): raise ValueError('Invalid project defaults record')
    result=dict(record)
    provider=record.get('provider','codex')
    if provider not in PROVIDERS: raise ValueError('Choose codex, claude or opencode')
    directory=_text(record.get('directory',''),'Directory',4096)
    if directory:
        directory=_text(os.path.abspath(os.path.expanduser(directory)),'Directory',4096)
    argv=record.get('arguments',[])
    if not isinstance(argv,list) or len(argv)>128:
        raise ValueError('Arguments must be a JSON array with at most 128 strings')
    for arg in argv: _text(arg,'Argument',16384)
    if sum(len(arg.encode()) for arg in argv)>16384:
        raise ValueError('Saved arguments exceed 16 KiB')
    worktree=record.get('worktree',False)
    if type(worktree) is not bool: raise ValueError('Worktree preference must be on or off')
    result.update(provider=provider,directory=directory,
                  arguments=list(argv),worktree=worktree)
    return result


def get(name):
    if not name: return {}
    records=_read()
    return validate(records[name]) if name in records else {}


def _lock():
    store.ensure_dir(state_root())
    fd=os.open(path()+'.lock',os.O_CREAT|os.O_RDWR,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX)
    return fd


def save(name,updates):
    if not _text(name,'Project name',480).strip(): raise ValueError('Choose a project name')
    lock=_lock()
    try:
        records=_read()
        previous=records.get(name,{})
        if not isinstance(previous,dict): raise ValueError('Invalid project defaults record')
        record=dict(previous)
        record.update(updates)
        if ('provider' in updates and updates['provider']!=previous.get('provider','codex')
                and 'arguments' not in updates):
            record['arguments']=[]
        records[name]=validate(record)
        if len(records)>1000: raise ValueError('At most 1000 project defaults can be saved')
        store.atomic_write(path(),records)
        return records[name]
    finally: os.close(lock)


def reset(name):
    if not os.path.exists(path()): return
    lock=_lock()
    try:
        records=_read()
        if name in records:
            del records[name]
            store.atomic_write(path(),records)
    finally: os.close(lock)


def rename(old,new,action):
    """Keep preferences with sidebar-renamed projects; refuse saved-name collisions."""
    if not os.path.exists(path()) or old==new: return action()
    lock=_lock()
    try:
        records=_read()
        if old not in records: return action()
        if new in records:
            raise ValueError('Saved defaults already exist for that name; choose another name or reset those defaults first')
        result=action()
        if result[0]==0:
            records[new]=records.pop(old)
            try: store.atomic_write(path(),records)
            except OSError:
                return 1,'','Project renamed, but defaults could not be moved; they remain under '+old
        return result
    finally: os.close(lock)


def resolve(record,provider=None,directory=None,worktree=None,arguments=None,no_default_args=False):
    record=validate(record) if record else {}
    preferred=record.get('provider','codex')
    provider=provider or preferred
    if provider not in PROVIDERS: raise ValueError('Unknown agent provider')
    return dict(provider=provider,directory=record.get('directory','') if directory is None else directory,
        worktree=record.get('worktree',False) if worktree is None else worktree,
        arguments=(list(arguments) if arguments is not None else [] if no_default_args or provider!=preferred
                   else list(record.get('arguments',[]))))
