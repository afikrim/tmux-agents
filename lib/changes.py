"""Read-only Git diffs and private, bounded per-pane turn checkpoints.

Never changes the repository index, working tree or object database.
"""
import base64
import difflib
import gzip
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

MAX_FILE = 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_FILES = 300


def git(cwd, *args):
    result = subprocess.run(['git', '--no-optional-locks', '-C', cwd, *args],
                            capture_output=True, timeout=5)
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', 'replace').strip() or 'Git query failed')
    return result.stdout


def repository(cwd):
    return os.fsdecode(git(cwd, 'rev-parse', '--show-toplevel')).strip()


def head(root):
    try:
        return git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()
    except ValueError:
        return ''


def candidates(root, base):
    if base:
        tracked = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--name-only', '--no-renames', '-z', base, '--')
    else:
        tracked = git(root, 'ls-files', '--cached', '-z')
    untracked = git(root, 'ls-files', '--others', '--exclude-standard', '-z')
    return sorted(set(os.fsdecode(item) for item in (tracked + untracked).split(b'\0') if item))


def safe_path(root, name):
    if os.path.isabs(name) or '..' in Path(name).parts:
        raise ValueError('Unsafe Git path')
    path = Path(root) / name
    # Do not follow a symlinked parent out of the checkout.
    if os.path.commonpath([os.path.realpath(path.parent), os.path.realpath(root)]) != os.path.realpath(root):
        raise ValueError('Path outside checkout')
    return path


def working_blob(root, name):
    path = safe_path(root, name)
    if path.is_symlink():
        return os.fsencode(os.readlink(path))
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError('Large file or submodule omitted')
    with path.open('rb') as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError('Large file omitted')
    return data


def working_mode(root, name):
    path = safe_path(root, name)
    if path.is_symlink():
        return '120000'
    if not path.exists():
        return '000000'
    return '100755' if path.stat().st_mode & 0o111 else '100644'


def committed_mode(root, base, name):
    if not base:
        return '000000'
    data = git(root, 'ls-tree', '-z', base, '--', name)
    return data.split(b' ', 1)[0].decode() if data else '000000'


def committed_blob(root, base, name):
    if not base:
        return None
    try:
        size = int(git(root, 'cat-file', '-s', base + ':' + name))
    except ValueError:
        return None
    if size > MAX_FILE:
        raise ValueError('Large file omitted')
    return git(root, 'show', base + ':' + name)


def checkpoint_path(paths, pane):
    if not re.fullmatch(r'%\d+', pane):
        raise ValueError('Invalid pane')
    return os.path.join(paths['dir'], 'diff', pane[1:] + '.json.gz')


def checkpoint(paths, pane, cwd, pid=0, agent_pid=0, agent_start=0, source='manual'):
    if os.path.exists(os.path.join(paths['dir'], 'disabled')):
        raise ValueError('Diff tracking is disabled')
    root = repository(cwd)
    base = head(root)
    names = candidates(root, base)
    files, modes, omitted, total = {}, {}, [], 0
    for index, name in enumerate(names[:MAX_FILES]):
        try:
            data = working_blob(root, name)
            total += len(data or b'')
            if total > MAX_TOTAL:
                omitted.extend(names[index:MAX_FILES])
                break
            files[name] = None if data is None else base64.b64encode(data).decode('ascii')
            modes[name] = working_mode(root, name)
        except (OSError, ValueError):
            omitted.append(name)
    omitted.extend(names[MAX_FILES:])
    record = dict(root=root, head=base, files=files, modes=modes, omitted=omitted,
                  pid=pid, agent_pid=agent_pid, agent_start=agent_start,
                  created=time.time(), source=source)
    destination = checkpoint_path(paths, pane)
    os.makedirs(paths['dir'], mode=0o700, exist_ok=True)
    os.makedirs(os.path.dirname(destination), mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(destination))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(gzip.compress(json.dumps(record).encode()))
        if os.path.exists(os.path.join(paths['dir'], 'disabled')):
            raise ValueError('Diff tracking is disabled')
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return record


def load_checkpoint(paths, pane, root, pid, identity=None):
    try:
        with gzip.open(checkpoint_path(paths, pane), 'rb') as stream:
            record = json.loads(stream.read(24 * 1024 * 1024))
        if os.path.realpath(record['root']) != os.path.realpath(root) or record.get('pid') != pid:
            return None
        if identity and record.get('agent_pid') and (
                record['agent_pid'] != identity[1] or
                abs(record.get('agent_start', 0) - identity[2]) >= 2):
            return None
        return record
    except (OSError, ValueError, KeyError, EOFError):
        return None


def diff_lines(root, record=None):
    base = record['head'] if record else head(root)
    saved = record.get('files', {}) if record else {}
    excluded = set(record.get('omitted', [])) if record else set()
    names = sorted(set(candidates(root, base)) | set(saved))
    output, changed, omitted, total = [], 0, len(excluded), 0
    for index, name in enumerate(names[:MAX_FILES]):
        if name in excluded:
            continue
        try:
            before = (None if saved[name] is None else base64.b64decode(saved[name])) if name in saved else committed_blob(root, base, name)
            after = working_blob(root, name)
            total += len(before or b'') + len(after or b'')
            if total > MAX_TOTAL:
                omitted += len(names[:MAX_FILES]) - index
                break
            mode_change = None
            if before == after:
                old_mode = record.get('modes', {}).get(name) if record and name in saved else committed_mode(root, base, name)
                new_mode = working_mode(root, name)
                if not old_mode or old_mode == new_mode:
                    continue
                mode_change = old_mode, new_mode
            changed += 1
            label = name.replace('\n', '\\n').replace('\r', '\\r')
            output.append('diff -- ' + label)
            if mode_change:
                output.extend(['old mode ' + mode_change[0], 'new mode ' + mode_change[1]])
            elif b'\0' in (before or b'') + (after or b''):
                output.append('Binary file changed')
            else:
                try:
                    left = (before or b'').decode('utf-8').splitlines(keepends=True)
                    right = (after or b'').decode('utf-8').splitlines(keepends=True)
                except UnicodeDecodeError:
                    output.append('Binary file changed')
                    continue
                patch = difflib.unified_diff(left, right,
                    fromfile='a/' + label if before is not None else '/dev/null',
                    tofile='b/' + label if after is not None else '/dev/null')
                for line in patch:
                    output.append(line.rstrip('\r\n'))
                    if len(output) >= 15000:
                        output.append('Diff truncated after 15,000 lines.')
                        return output, changed
            output.append('')
            if len(output) > 15000:
                output.append('Diff truncated after 15,000 lines.')
                break
        except (OSError, ValueError, subprocess.TimeoutExpired):
            omitted += 1
    omitted += max(0, len(names) - MAX_FILES)
    if omitted:
        output.append('%d large, unsupported or checkpoint-limited files omitted.' % omitted)
    if not changed:
        output.insert(0, 'No text changes to display.' if omitted else 'No changes since checkpoint.' if record else 'Working tree is clean.')
    return output, changed
