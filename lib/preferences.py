"""Project defaults editor in a native popup; changes are explicit and cancelable."""
import os
import select
import shlex
import sys
import termios
import tty

from . import defaults, ui


def frame(project,provider,directory,arguments,worktree,selected,width,height,error=''):
    fields=['Agent: < '+provider+' >','Folder: '+(directory or '(follow focused terminal)'),
            'Arguments: '+(arguments or '(none)'), 'Worktree: < '+('on' if worktree else 'off')+' >',
            'Save project defaults','Reset saved defaults']
    count=max(1,height-3)
    start=min(max(0,selected-count+1),max(0,len(fields)-count))
    lines=[ui.styled(' Defaults / '+project,width,'accent',bold=True)]
    for index in range(start,min(len(fields),start+count)):
        value=fields[index]
        while selected==index and value and ui.cell_width(value)>max(0,width-4): value=value[1:]
        lines.append(ui.styled((' > ' if selected==index else '   ')+value,width,'fg',
                               'selected' if selected==index else 'bg'))
    lines.append(ui.styled(' '+error,width,'rose'))
    lines.append(ui.styled(' Tab next · arrows/Space choose · Enter continue · Esc cancel',width,'muted'))
    return (lines+[ui.styled('',width)]*max(0,height-len(lines)))[:height]


def run(prefix,client,window,project=''):
    project=defaults.project_name(prefix,window,project)
    if not project: return
    error=''
    try: record=defaults.get(project)
    except (ValueError,OSError) as exc:
        record={}; error=str(exc)
    provider=record.get('provider','codex')
    directory=record.get('directory','')
    arguments=shlex.join(record.get('arguments',[]))
    argument_drafts={provider:arguments}
    worktree=record.get('worktree',False)
    selected=0; decoder=ui.InputDecoder()
    fd=sys.stdin.fileno(); old=termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdout.write('\x1b[?1049h\x1b[?25l'+ui.BASE+'\x1b[2J')
        while True:
            size=os.get_terminal_size(fd)
            lines=frame(project,provider,directory,arguments,worktree,selected,size.columns,size.lines,error)
            sys.stdout.write('\x1b[H'+'\r\n'.join(lines)); sys.stdout.flush()
            ready=select.select([fd],[],[],.05 if decoder.pending else .25)[0]
            if not ready: keys=decoder.feed(flush=True) if decoder.pending else []
            else:
                raw=os.read(fd,4096)
                if not raw: return
                keys=decoder.feed(raw.decode('utf-8','replace'))
            for key in keys:
                if key in ('esc','\x03'): return
                if key in ('tab','down'): selected=(selected+1)%6
                elif key=='up': selected=(selected-1)%6
                elif selected==0 and key in ('left','right',' '):
                    argument_drafts[provider]=arguments
                    index=defaults.PROVIDERS.index(provider)
                    provider=defaults.PROVIDERS[(index+(-1 if key=='left' else 1))%3]
                    arguments=argument_drafts.get(provider,'')
                elif selected==3 and key in ('left','right',' ','w'): worktree=not worktree
                elif key=='enter':
                    if selected<4: selected+=1; continue
                    try:
                        if selected==5: defaults.reset(project)
                        else:
                            defaults.save(project,dict(provider=provider,directory=directory,
                                arguments=shlex.split(arguments),worktree=worktree))
                        return
                    except (ValueError,OSError) as exc: error=ui.san(str(exc),0)
                elif selected in (1,2):
                    value=directory if selected==1 else arguments
                    if key=='backspace': value=value[:-1]
                    elif key=='\x15': value=''
                    elif isinstance(key,str) and len(key)==1 and key.isprintable(): value=(value+key)[:16384]
                    if selected==1: directory=value
                    else: arguments=value
    finally:
        try:
            sys.stdout.write('\x1b[?25h\x1b[?1049l'); sys.stdout.flush()
        except OSError: pass
        try: termios.tcsetattr(fd,termios.TCSANOW,old)
        except OSError: pass
