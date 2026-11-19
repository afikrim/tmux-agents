"""Explicit per-agent Git worktrees; never reset, stash, force-remove, or auto-prune."""
import hashlib
import os
import shutil
import subprocess
import time
import uuid

from . import store
from .tmuxutil import state_root


def git(directory, *args):
    return subprocess.run(['git','-C',directory,*args],capture_output=True,text=True,timeout=60)


def create(directory, provider, branch=''):
    if not shutil.which('git'):
        raise ValueError('Git is required for worktree mode')
    root=git(directory,'rev-parse','--show-toplevel')
    head=git(directory,'rev-parse','--verify','HEAD')
    if root.returncode or head.returncode:
        raise ValueError('Worktree mode needs a Git checkout with at least one commit')
    root=os.path.realpath(root.stdout.strip())
    relative=os.path.relpath(os.path.realpath(directory),root)
    common=git(root,'rev-parse','--git-common-dir')
    if common.returncode:
        raise ValueError(common.stderr.strip() or 'Cannot locate Git metadata')
    common=os.path.realpath(os.path.join(root,common.stdout.strip()))
    branch=branch or 'agents/%s-%s-%s'%(provider,time.strftime('%Y%m%d-%H%M%S'),uuid.uuid4().hex[:8])
    if git(root,'check-ref-format','--branch',branch).returncode:
        raise ValueError('Invalid worktree branch name')
    if git(root,'show-ref','--verify','--quiet','refs/heads/'+branch).returncode==0:
        raise ValueError('Branch already exists; choose a new branch name')
    parent=os.path.join(state_root(),'worktrees',hashlib.sha256(common.encode()).hexdigest()[:16])
    store.ensure_dir(parent)
    path=os.path.join(parent,provider+'-'+uuid.uuid4().hex[:12])
    result=git(root,'worktree','add','-b',branch,path,head.stdout.strip())
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Git could not create the worktree')
    record=dict(root=root,path=path,branch=branch,base=head.stdout.strip(),
                directory=os.path.normpath(os.path.join(path,relative)))
    if not os.path.isdir(record['directory']):
        removed=rollback(record)
        message='Current subdirectory is not in the committed checkout; launch from the repository root'
        if not removed:
            message+='; worktree retained for inspection: '+record['path']+' ('+branch+')'
        raise ValueError(message)
    return record


def rollback(record):
    """Undo a failed launch only while our new branch/checkout remain untouched."""
    head=git(record['root'],'rev-parse','refs/heads/'+record['branch'])
    if head.returncode or head.stdout.strip()!=record['base']:
        return False
    # Git itself refuses dirty, locked or otherwise unsafe worktree removal.
    if git(record['root'],'worktree','remove',record['path']).returncode:
        return False
    head=git(record['root'],'rev-parse','refs/heads/'+record['branch'])
    if head.returncode or head.stdout.strip()!=record['base']:
        return False
    return git(record['root'],'branch','-d','--',record['branch']).returncode==0
