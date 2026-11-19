"""New project + first agent form. Native tmux popup; no placeholder terminal."""
import contextlib
import io
import os
import select
import sys
import termios
import tty
from types import SimpleNamespace

from . import ui
from .tmuxutil import run_tmux

KINDS=('codex','claude','opencode')


def frame(name,directory,kind,selected,width,height,error='',worktree=False):
    fields=['Name: '+name,'Folder: '+directory,'Agent: < '+kind+' > / worktree '+('on' if worktree else 'off'),'Create project + agent']
    count=max(1,height-3)
    start=min(max(0,selected-count+1),max(0,len(fields)-count))
    lines=[ui.styled(' New project + agent',width,'accent',bold=True)]
    for index in range(start,min(len(fields),start+count)):
        value=fields[index]
        if ui.cell_width(value)>width-4 and selected==index:
            while value and ui.cell_width(value)>max(0,width-4): value=value[1:]
        lines.append(ui.styled((' > ' if selected==index else '   ')+value,width,
                     'fg','selected' if selected==index else 'bg'))
    lines.append(ui.styled(' '+error,width,'rose'))
    lines.append(ui.styled(' Tab next · Enter continue · w worktree (Agent) · Esc cancel',width,'muted'))
    lines=lines[:height]
    lines.extend(ui.styled('',width) for _ in range(height-len(lines)))
    return lines


def run(prefix,client,window,launch):
    if not window:
        window=run_tmux(prefix,'display-message','-p','-c',client,'#{window_id}')[1].strip()
    directory=run_tmux(prefix,'display-message','-p','-t',window or '=', '#{pane_current_path}')[1].strip()
    directory=directory if os.path.isdir(directory) else os.path.expanduser('~')
    name=os.path.basename(directory.rstrip('/')).replace('.','-').replace(':','-') or 'project'
    selected,kind,error,worktree=0,0,'',False
    fd=sys.stdin.fileno()
    old=termios.tcgetattr(fd)
    decoder=ui.InputDecoder()
    try:
        tty.setraw(fd)
        sys.stdout.write('\x1b[?1049h\x1b[?25l'+ui.BASE+'\x1b[2J')
        while True:
            size=os.get_terminal_size(fd)
            lines=frame(name,directory,KINDS[kind],selected,size.columns,size.lines,error,worktree)
            sys.stdout.write('\x1b[H'+'\r\n'.join(lines)); sys.stdout.flush()
            ready=select.select([fd],[],[],.05 if decoder.pending else .25)[0]
            if not ready:
                keys=decoder.feed(flush=True) if decoder.pending else []
            else:
                raw=os.read(fd,4096)
                if not raw: return
                keys=decoder.feed(raw.decode('utf-8','replace'))
            for key in keys:
                if key in ('esc','\x03'): return
                if key in ('tab','down'):
                    selected=(selected+1)%4
                elif key=='up':
                    selected=(selected-1)%4
                elif selected==2 and key in ('left','right'):
                    kind=(kind+(1 if key=='right' else -1))%len(KINDS)
                elif selected==2 and key in ('w',' '):
                    worktree=not worktree
                elif key=='enter':
                    if selected<3:
                        selected+=1
                        continue
                    if not name.strip():
                        error='Enter a project name'; selected=0; continue
                    if not directory.strip():
                        error='Enter a project folder'; selected=1; continue
                    output=io.StringIO()
                    args=SimpleNamespace(kind=KINDS[kind],exe='',launch_args=[],env=[],window=window,
                                         project=name.strip(),directory=directory,client=client,worktree=worktree,branch='',no_default_args=True)
                    with contextlib.redirect_stdout(output):
                        rc=launch(args,prefix)
                    if not rc: return
                    error=ui.san(output.getvalue(),0).strip() or 'Could not create project'
                elif selected in (0,1):
                    value=name if selected==0 else directory
                    if key=='backspace': value=value[:-1]
                    elif key=='\x15': value=''
                    elif isinstance(key,str) and len(key)==1 and key.isprintable(): value=(value+key)[:4096 if selected==1 else 120]
                    if selected==0: name=value
                    else: directory=value
    finally:
        try:
            sys.stdout.write('\x1b[?25h\x1b[?1049l'); sys.stdout.flush()
        except OSError: pass
        try: termios.tcsetattr(fd,termios.TCSANOW,old)
        except OSError: pass
