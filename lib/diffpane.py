"""Optional right-hand, read-only diff pane. No third-party dependencies."""
import os
import queue
import select
import sys
import termios
import threading
import time
import tty

from . import changes, detect, ui
from .tmuxutil import cli_socket_flags, run_tmux, state_root


def owner_info(prefix, target):
    rc, text, _ = run_tmux(prefix, 'display-message', '-p', '-t', target,
                           '#{pane_pid}\t#{pane_current_path}\t#{pane_id}')
    parts = text.rstrip('\n').split('\t')
    if rc or len(parts) != 3 or parts[2] != target or not parts[0].isdigit():
        raise ValueError('The selected terminal has closed.')
    return int(parts[0]), parts[1]


def toggle(prefix, paths, panes, target='', window='', directory=''):
    if not window:
        window = next((p['window_id'] for p in panes if p['pane_id'] == target), '')
    if not window:
        window = run_tmux(prefix, 'display-message', '-p', '#{window_id}')[1].strip()
    group = [p for p in panes if p['window_id'] == window]
    owned = [p for p in group if p.get('diff')]
    if owned:
        if directory:
            root = changes.repository(os.path.expanduser(directory))
            owner = target if any(p['pane_id'] == target and not p.get('diff') and not p.get('sidebar') for p in group) else run_tmux(prefix, 'show-options', '-p', '-v', '-t', owned[0]['pane_id'], '@tmux_agents_diff_target')[1].strip()
            run_tmux(prefix, 'set-option', '-p', '-t', owner, '@tmux_agents_diff_root', root)
            return 0
        for pane in owned:
            rc, tag, _ = run_tmux(prefix, 'show-options', '-p', '-v', '-t', pane['pane_id'], '@tmux_agents_diff')
            if rc == 0 and tag.strip() == '1':
                run_tmux(prefix, 'kill-pane', '-t', pane['pane_id'])
        return 0
    real = [p for p in group if not p.get('sidebar') and not p.get('diff')]
    chosen = next((p for p in real if p['pane_id'] == target),
                  next((p for p in real if p['active']), next(iter(real), None)))
    if not chosen:
        return 1
    if directory:
        root = changes.repository(os.path.expanduser(directory))
        run_tmux(prefix, 'set-option', '-p', '-t', chosen['pane_id'], '@tmux_agents_diff_root', root)
    width = chosen['win_width']
    if width < 120:
        run_tmux(prefix, 'display-message', 'Widen this window to at least 120 columns for the diff pane.')
        return 1
    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'bin', 'tmux-agents')
    command = ['env', 'TMUX_AGENTS_STATE_DIR=' + state_root(), sys.executable,
               script, *cli_socket_flags(prefix), 'diff-view', '--target', chosen['pane_id']]
    rc, pane, error = run_tmux(prefix, 'split-window', '-fh', '-d', '-t', chosen['pane_id'],
                              '-l', str(min(64, max(40, (width - 32) // 2))),
                              '-c', chosen['path'], '-P', '-F', '#{pane_id}', *command)
    if rc:
        run_tmux(prefix, 'display-message', 'Cannot open diff: ' + ui.san(error, 0))
        return rc
    pane = pane.strip()
    run_tmux(prefix, 'set-option', '-p', '-t', pane, '@tmux_agents_diff', '1')
    run_tmux(prefix, 'set-option', '-p', '-t', pane, '@tmux_agents_diff_target', chosen['pane_id'])
    run_tmux(prefix, 'set-option', '-p', '-t', chosen['pane_id'], '@tmux_agents_diff_tracking', '1')
    for left in group:
        if left.get('sidebar'):
            run_tmux(prefix, 'resize-pane', '-t', left['pane_id'], '-x', '32')
    return 0


def read_diff(prefix, paths, target, mode, save=False):
    pid, cwd = owner_info(prefix, target)
    override = run_tmux(prefix, 'show-options', '-p', '-v', '-t', target, '@tmux_agents_diff_root')[1].strip()
    cwd = override or cwd
    root = changes.repository(cwd)
    identity = detect.agent_identity(pid, detect.process_table())
    if save:
        changes.checkpoint(paths, target, cwd, pid, source='manual')
    record = changes.load_checkpoint(paths, target, root, pid, identity) if mode == 'iteration' else None
    lines, count = changes.diff_lines(root, record)
    if record:
        label = 'Latest turn' if record.get('source') == 'turn' else 'Since checkpoint'
        hint = 'Checkpoint ' + time.strftime('%H:%M:%S', time.localtime(record['created']))
    else:
        label = 'Working tree'
        hint = 'No turn checkpoint; showing working tree.' if mode == 'iteration' else 'Tracked + untracked changes against HEAD'
    return label, os.path.basename(root), hint, lines, count


def text_clusters(text):
    import unicodedata
    cluster = ""
    for raw in text:
        for char in ('    ' if raw == '\t' else raw):
            if unicodedata.category(char).startswith('C'):
                continue
            if unicodedata.combining(char):
                if cluster:
                    cluster += char
            else:
                if cluster:
                    yield cluster
                cluster = char
    if cluster:
        yield cluster


def cell_slice(text, offset, width):
    out, position = "", 0
    for cluster in text_clusters(text):
        size = ui.cell_width(cluster)
        if position >= offset + width:
            break
        if position < offset < position + size:
            out += " " * min(width, position + size - offset)
        elif position >= offset:
            if position + size > offset + width:
                break
            out += cluster
        position += size
    return out


def display_rows(lines, width, wrap=False):
    rows = []
    space = max(1, width - 1)
    for source, line in enumerate(lines):
        color = ('accent' if line.startswith('diff -- ') else 'blue' if line.startswith('@@')
                 else 'accent' if line.startswith('+') else 'rose' if line.startswith('-') else 'fg')
        if not wrap:
            rows.append((line, color, source))
            continue
        chunk, used = '', 0
        for cluster in text_clusters(line):
            size = ui.cell_width(cluster)
            if used + size > space and chunk:
                rows.append((chunk, color, source))
                chunk, used = '', 0
            if size > space:
                cluster, size = '?', 1
            chunk, used = chunk + cluster, used + size
        rows.append((chunk, color, source))
    return rows


def frame(data, width, height, top=0, horizontal=0, wrap=False, prepared=None):
    label, repo, hint, lines, count = data
    width, height = max(1, width), max(1, height)
    header = [ui.styled(' changes / wrap:%s' % ('on' if wrap else 'off'), width, 'accent', bold=True),
              ui.styled(' %s / %s / %d files' % (label, repo, count), width),
              ui.styled(' ' + hint, width, 'muted'),
              ui.styled(' ' + '─' * max(0, width - 2), width, 'line')]
    footer = []
    body_height = max(0, height - len(header) - len(footer))
    rows = prepared if prepared is not None else display_rows(lines, width, wrap)
    top = min(max(0, top), max(0, len(rows) - body_height))
    body = []
    for line, color, _source in rows[top:top + body_height]:
        body.append(ui.styled(' ' + cell_slice(line, 0 if wrap else horizontal, max(0, width - 1)), width, color))
    body.extend(ui.styled('', width) for _ in range(body_height - len(body)))
    return (header + body + footer)[:height], top


def run(prefix, paths, target):
    fd = sys.stdin.fileno()
    own = os.environ.get('TMUX_PANE', '')
    old = termios.tcgetattr(fd)
    top, horizontal, mode = 0, 0, 'iteration'
    data = ('Loading', '', 'Reading repository…', [], 0)
    mailbox = queue.Queue()
    worker = None
    refresh = 0
    save = False
    editing, edit_text, edit_error = False, '', ''
    wrap = bool(own and run_tmux(prefix, 'show-window-options', '-v', '-t', own, '@tmux_agents_diff_wrap')[1].strip() == '1')
    cache_key, prepared, anchor = None, [], None
    max_horizontal = 0
    try:
        tty.setraw(fd)
        sys.stdout.write('\x1b[?1049h\x1b[?25l' + ui.BASE + '\x1b[2J')
        while True:
            if own:
                # Follow real terminal focus in this window, including mouse
                # and native tmux selection, while ignoring both side panels.
                rc, panes, _ = run_tmux(prefix, 'list-panes', '-t', own, '-F',
                                        '#{pane_id}\t#{pane_active}\t#{@tmux_agents_sidebar}\t#{@tmux_agents_diff}')
                for line in panes.splitlines():
                    parts = line.split('\t')
                    if len(parts) == 4 and parts[1] == '1' and parts[2:] == ['', '']:
                        if parts[0] != target:
                            run_tmux(prefix, 'set-option', '-p', '-t', own, '@tmux_agents_diff_target', parts[0])
                            run_tmux(prefix, 'set-option', '-p', '-t', parts[0], '@tmux_agents_diff_tracking', '1')
                rc, current, _ = run_tmux(prefix, 'show-options', '-p', '-v', '-t', own, '@tmux_agents_diff_target')
                current = current.strip()
                if rc == 0 and current.startswith('%') and current != target:
                    target, top, horizontal, refresh = current, 0, 0, 0
                rc, exists, _ = run_tmux(prefix, 'display-message', '-p', '-t', target, '#{pane_id}')
                if rc or exists.strip() != target:
                    break
            try:
                key, result = mailbox.get_nowait()
                if key == (target, mode):
                    data = result
            except queue.Empty:
                pass
            if time.monotonic() >= refresh and not (worker and worker.is_alive()):
                def update(chosen, selected_mode, checkpoint):
                    try:
                        result = read_diff(prefix, paths, chosen, selected_mode, checkpoint)
                    except Exception as error:
                        hint = 'Install Git to enable diffs.' if isinstance(error, FileNotFoundError) else 'Choose a Git repository.'
                        result = ('Unavailable', '', hint, [str(error), '', 'Repository unavailable. Open shortcut help for settings.'], 0)
                    mailbox.put(((chosen, selected_mode), result))
                worker = threading.Thread(target=update, args=(target, mode, save), daemon=True)
                worker.start()
                refresh, save = time.monotonic() + 1.5, False
            size = os.get_terminal_size(fd)
            key = (id(data[3]), size.columns, wrap)
            if key != cache_key:
                prepared = display_rows(data[3], size.columns, wrap)
                cache_key = key
                max_horizontal = max(0, max((ui.cell_width(ui.san(row[0], 0)) for row in prepared), default=0) - max(1, size.columns - 1))
                horizontal = min(horizontal, max_horizontal)
                if anchor is not None:
                    top = next((i for i, row in enumerate(prepared) if row[2] == anchor), top)
                    anchor = None
            lines, top = frame(data, size.columns, size.lines, top, horizontal, wrap, prepared)
            if editing and len(lines) >= 2:
                lines[-2] = ui.styled(' Repository: ' + edit_text[-max(1, size.columns - 14):], size.columns, 'accent')
                lines[-1] = ui.styled(' ' + (edit_error or 'enter save / esc cancel / ^U clear'), size.columns, 'muted')
            sys.stdout.write('\x1b[H' + '\r\n'.join(lines))
            sys.stdout.flush()
            if not select.select([fd], [], [], .25)[0]:
                continue
            raw = os.read(fd, 4096)
            if not raw:
                break
            for key in ui.parse_input(raw.decode('utf-8', 'replace')):
                if editing:
                    if key in ('esc', '\x03'):
                        editing = False
                    elif key == 'enter':
                        try:
                            if edit_text.strip():
                                root = changes.repository(os.path.expanduser(edit_text.strip()))
                                run_tmux(prefix, 'set-option', '-p', '-t', target, '@tmux_agents_diff_root', root)
                            else:
                                run_tmux(prefix, 'set-option', '-pu', '-t', target, '@tmux_agents_diff_root')
                            editing, refresh, top = False, 0, 0
                        except Exception as error:
                            edit_error = str(error)
                    elif key == 'backspace':
                        edit_text = edit_text[:-1]
                    elif key == '\x15':
                        edit_text = ''
                    elif isinstance(key, str) and len(key) == 1 and key.isprintable():
                        edit_text = (edit_text + key)[:4096]
                    continue
                if key == '?':
                    from .popups import open_popup
                    open_popup(prefix, paths, 'help')
                    continue
                if key == 'o':
                    editing, edit_text, edit_error = True, '', ''
                    continue
                if key in ('q', 'esc', '\x03'):
                    return
                if key == 'tab':
                    mode = 'working' if mode == 'iteration' else 'iteration'
                    refresh, top, horizontal = 0, 0, 0
                elif key == 'b':
                    save, mode, refresh, top = True, 'iteration', 0, 0
                elif key == 'r':
                    refresh = 0
                elif key == 'w':
                    anchor = prepared[min(top, len(prepared) - 1)][2] if prepared else 0
                    wrap, horizontal = not wrap, 0
                    if own:
                        run_tmux(prefix, 'set-window-option', '-t', own, '@tmux_agents_diff_wrap', '1' if wrap else '0')
                elif key in ('j', 'down'):
                    top += 1
                elif key in ('k', 'up'):
                    top = max(0, top - 1)
                elif key in ('pagedown', 'pageup'):
                    top = max(0, top + (1 if key == 'pagedown' else -1) * max(1, size.lines - 8))
                elif key in ('g', 'home'):
                    top = 0
                elif key in ('G', 'end'):
                    top = len(prepared)
                elif key in ('h', 'l', 'left', 'right') and not wrap:
                    horizontal = min(max_horizontal, max(0, horizontal + (4 if key in ('l', 'right') else -4)))
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old)
            sys.stdout.write('\x1b[?25h\x1b[?1049l')
            sys.stdout.flush()
        except OSError:
            pass
        if own:
            rc, tag, _ = run_tmux(prefix, 'show-options', '-p', '-v', '-t', own, '@tmux_agents_diff')
            if rc == 0 and tag.strip() == '1':
                run_tmux(prefix, 'select-pane', '-t', target)
                run_tmux(prefix, 'kill-pane', '-t', own)
