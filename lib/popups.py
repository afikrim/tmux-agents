"""Matching project switcher and shortcut help, hosted by native tmux popups."""
import os
import select
import shlex
import subprocess
import sys
import termios
import time
import tty

from . import theme, ui, history, inbox
from .tmuxutil import cli_socket_flags, run_tmux, state_root

SHORTCUTS = [
    ('Prefix I / i in sidebar', 'Inbox / Questions, approvals and unread results'),
    ('Prefix N', 'Inbox / Jump to the next agent needing attention'),
    ('Prefix H / h in sidebar', 'History / Search and resume conversations'),
    ('Prefix s', 'Global / Search and switch projects'),
    ('Prefix ?', 'Global / Open shortcut help'),
    ('Alt-e', 'Global / Focus project navigation'),
    ('Prefix S', 'Global / Hide or show navigation'),
    ('Prefix a', 'Global / Open the new-agent menu'),
    ('Prefix v', 'Global / Toggle the right-hand diff'),
    ('j/k or arrows', 'Navigation / Select project or individual agent pane'),
    ('Enter', 'Navigation / Open selected project or agent'),
    ('x', 'Navigation / Launch an agent in a new window'),
    ('n (agent menu)', 'Navigation / Create a project with its first agent'),
    ('d / p (agent menu)', 'Launch / Project default agent / Edit project defaults'),
    ('C / A / O (agent menu)', 'Launch / Codex, Claude or OpenCode in a new Git worktree'),
    ('w (new project, Agent field)', 'Launch / Toggle a separate Git worktree'),
    ('r', 'Navigation / Rename project or conversation label'),
    ('Shift-r', 'Navigation / Rename the selected window'),
    ('d', 'Navigation / Toggle diff for selected agent'),
    ('?', 'Navigation or diff / Open shortcut help'),
    ('q or Escape', 'Navigation / Return to the terminal'),
    ('Tab', 'Diff / Switch latest turn and working tree'),
    ('w', 'Diff / Toggle soft wrap; remembered per window'),
    ('h/l or arrows', 'Diff / Pan horizontally when not wrapped'),
    ('j/k or arrows', 'Diff / Scroll vertically'),
    ('g/G, Page keys', 'Diff / Jump or scroll through changes'),
    ('b', 'Diff / Take a manual checkpoint'),
    ('o', 'Diff / Choose repository; blank follows terminal cwd'),
    ('r', 'Diff / Refresh now'),
    ('q or Escape', 'Diff / Close the diff pane'),
    ('= / # / ~ / ?', 'Status source / Native, screen, observed, unknown'),
    ('▶ / ●', 'Project counts / Running, idle or waiting; unread opens the agent'),
    ('Prefix c', 'tmux / Create a normal terminal window'),
    ('Click tab / Prefix n,p', 'tmux / Switch windows including shells and editors'),
    ('Prefix o / z', 'tmux / Next pane or toggle pane zoom'),
    ('Prefix d', 'tmux / Detach the client'),
    ('notify --enable', 'Global / Opt in to desktop alerts for attention'),
    ('notify --list / --open', 'Global / List recent alerts and return to one'),
    ('sidebar-width [columns]', 'Layout / Remember sidebar width; --min-width sets the minimum'),
]


def open_popup(prefix, paths, kind='help', client='', window='', project=''):
    if not client:
        client = run_tmux(prefix, 'display-message', '-p', '#{client_name}')[1].strip()
    if not client:
        print('Attach to tmux to open this popup.', file=sys.stderr)
        return 1
    rc, dimensions, _ = run_tmux(prefix, 'display-message', '-p', '-c', client,
                                 '#{client_width} #{client_height}')
    try:
        columns, rows = map(int, dimensions.split())
    except ValueError:
        columns, rows = 100, 36
    width, height = max(10, min(78, columns - 4)), max(5, min(28, rows - 4))
    if kind in ('new-project','defaults'):
        height=max(5,min(12,rows-4))
    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bin', 'tmux-agents')
    command = shlex.join(['env', 'TMUX_AGENTS_STATE_DIR=' + state_root(), sys.executable,
                          script, *cli_socket_flags(prefix), 'popup', '--kind', kind, '--client', client,
                          '--window',window,'--project',project])
    # This command intentionally lives until its overlay closes. No daemon or
    # ensure lock is held, and no real pane is created or replaced.
    result = subprocess.run(['tmux', *prefix, 'display-popup', '-E', '-c', client,
        '-w', str(width), '-h', str(height), '-x', 'C', '-y', 'C', '-b', 'rounded',
        '-T', ' Project defaults ' if kind == 'defaults' else ' Attention ' if kind == 'attention' else ' New project ' if kind == 'new-project' else ' History ' if kind == 'history' else ' Projects ' if kind == 'sessions' else ' Shortcuts ',
        '-s', 'fg=%s,bg=%s' % (theme.PALETTE['fg'], theme.PALETTE['bg']),
        '-S', 'fg=%s,bg=%s' % (theme.PALETTE['accent'], theme.PALETTE['bg']), command],
        capture_output=True, text=True)
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


def entries(sessions, kind, query, current=''):
    if kind == 'attention':
        return inbox.entries(sessions,query)
    if kind == 'history':
        return history.entries(query)
    if kind == 'help':
        rows = [dict(id=str(index), name=key, detail=detail, current=False)
                for index, (key, detail) in enumerate(SHORTCUTS)]
    else:
        rows = []
        for session in sorted(sessions, key=lambda item: item['name'].casefold()):
            windows = session.get('windows', [])
            agents = [p for w in windows for p in w.get('panes', []) if p.get('agent')]
            waiting = sum(p.get('status') in ('needs-approval', 'question', 'needs-input') for p in agents)
            detail = '%d windows / %d agents' % (len(windows), len(agents))
            if waiting:
                detail += ' / %d waiting' % waiting
            rows.append(dict(id=session['id'], name=session['name'], detail=detail,
                             current=session['id'] == current))
    words = query.casefold().split()
    return [row for row in rows if all(word in (row['name'] + ' ' + row['detail']).casefold() for word in words)]


def frame(rows, kind, query, cursor, top, width, height, error=''):
    width, height = max(1, width), max(1, height)
    title = 'Attention inbox — Enter to visit agent' if kind == 'attention' else 'Conversation history — Enter to open / resume' if kind == 'history' else 'Switch project' if kind == 'sessions' else 'Keyboard shortcuts'
    lines = [ui.styled(' ' + title, width, 'accent', bold=True),
             ui.styled(' Search: ' + (query or ('titles, projects, providers…' if kind in ('history','attention') else 'projects…' if kind == 'sessions' else 'shortcuts…')), width,
                       'fg' if query else 'muted', 'panel'),
             ui.styled(' ' + (error or '─' * max(0, width - 2)), width, 'rose' if error else 'line')]
    count = max(0, (height - len(lines)) // 2)
    if cursor < top:
        top = cursor
    elif cursor >= top + count:
        top = max(0, cursor - count + 1)
    top = min(max(0, top), max(0, len(rows) - count))
    hits = {}
    for index in range(top, min(top + count, len(rows))):
        row = rows[index]
        background = 'selected' if index == cursor else 'bg'
        right = ' current' if row.get('current') else ''
        left = ui.fit(row['name'], max(0, width - len(right) - 4), False)
        text = (' > ' if index == cursor else '   ') + left
        text += ' ' * max(0, width - ui.cell_width(text) - len(right) - 1) + right
        hits[len(lines) + 1] = index
        hits[len(lines) + 2] = index
        lines.append(ui.styled(text, width, 'accent' if row.get('current') else 'fg', background, True))
        lines.append(ui.styled('   ' + row['detail'], width, 'muted', background))
    if not rows and len(lines) < height:
        lines.append(ui.styled(' No agents need attention' if kind == 'attention' else ' No saved conversations yet; waiting for native hooks' if kind == 'history' and not query else ' No matching conversations' if kind == 'history' else ' No matching projects' if kind == 'sessions' else ' No matching shortcuts', width, 'muted'))
    lines.extend(ui.styled('', width) for _ in range(max(0, height - len(lines))))
    return lines[:height], top, hits


def run(prefix, paths, kind, client, build_sessions):
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    query, cursor, top, error, current = '', 0, 0, '', ''
    sessions, rows, refresh, initialized = [], [], 0, False
    try:
        tty.setraw(fd)
        sys.stdout.write('\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h' + ui.BASE + '\x1b[2J')
        while True:
            if time.monotonic() >= refresh:
                selected = rows[cursor]['id'] if rows else None
                if kind in ('sessions','attention'):
                    sessions = build_sessions(prefix, paths)
                    current = run_tmux(prefix, 'display-message', '-p', '-c', client, '#{session_id}')[1].strip()
                rows = entries(sessions, kind, query, current)
                wanted = selected if initialized else current
                cursor = next((i for i, row in enumerate(rows) if row['id'] == wanted), 0)
                initialized, refresh = True, time.monotonic() + 1
            size = os.get_terminal_size(fd)
            lines, top, hits = frame(rows, kind, query, cursor, top, size.columns, size.lines, error)
            sys.stdout.write('\x1b[H' + '\r\n'.join(lines))
            sys.stdout.flush()
            if not select.select([fd], [], [], .25)[0]:
                continue
            raw = os.read(fd, 4096)
            if not raw:
                return
            for key in ui.parse_input(raw.decode('utf-8', 'replace')):
                activate = key == 'enter'
                if key in ('esc', '\x03'):
                    return
                if isinstance(key, tuple):
                    _, button, _x, y, release = key
                    if not release and button == 0 and y in hits and hits[y] < len(rows):
                        cursor, activate = hits[y], True
                    elif not release and button in (64, 65):
                        cursor = min(max(0, cursor + (1 if button == 65 else -1)), max(0, len(rows) - 1))
                elif key in ('down', '\x0e'):
                    cursor = min(max(0, len(rows) - 1), cursor + 1)
                elif key in ('up', '\x10'):
                    cursor = max(0, cursor - 1)
                elif key in ('pagedown', 'pageup'):
                    cursor = min(max(0, len(rows) - 1), max(0, cursor + (5 if key == 'pagedown' else -5)))
                elif key in ('backspace', '\x15') or (isinstance(key, str) and len(key) == 1 and key.isprintable()):
                    query = '' if key == '\x15' else query[:-1] if key == 'backspace' else (query + key)[:120]
                    rows, cursor, top, error = entries(sessions, kind, query, current), 0, 0, ''
                if activate and kind in ('history','attention') and rows:
                    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bin', 'tmux-agents')
                    command = (['resume',rows[cursor]['id']] if kind=='history' else
                               ['visit','--pane',rows[cursor]['id'],'--generation',rows[cursor]['generation']])
                    result = subprocess.run([sys.executable, script, *cli_socket_flags(prefix),
                        *command, '--client', client], capture_output=True, text=True)
                    if result.returncode == 0:
                        return
                    error, refresh = ui.san(result.stderr or result.stdout, 0).strip()[:160], 0
                if activate and kind == 'sessions' and rows:
                    rc, _, message = run_tmux(prefix, 'switch-client', '-c', client, '-t', rows[cursor]['id'])
                    if rc == 0:
                        return
                    error, refresh = ui.san(message, 0), 0
    finally:
        try:
            sys.stdout.write('\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l')
            sys.stdout.flush()
        except OSError:
            pass
        termios.tcsetattr(fd, termios.TCSANOW, old)
