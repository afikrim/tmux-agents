"""Dependency checks shared by the CLI and user-local installer."""
import locale
import re
import shutil
import subprocess
import sys


def doctor():
    ok = sys.version_info >= (3, 9)
    print(('OK' if ok else 'MISSING') + ': Python ' + sys.version.split()[0] + ' (requires 3.9+)')
    for name in ('tmux', 'ps'):
        path = shutil.which(name)
        valid = bool(path)
        detail = path or 'install with your system package manager'
        if path and name == 'tmux':
            result = subprocess.run([path, '-V'], capture_output=True, text=True)
            detail = result.stdout.strip()
            match = re.search(r'(\d+)\.(\d+)', detail)
            valid = result.returncode == 0 and bool(match) and tuple(map(int, match.groups())) >= (3, 3)
            detail += ' (requires 3.3+)'
        print(('OK' if valid else 'MISSING') + ': ' + name + ' — ' + detail)
        ok = ok and valid
    print('Terminal encoding: ' + locale.getpreferredencoding(False))
    for agent in ('codex', 'claude', 'opencode'):
        print('Agent ' + agent + ': ' + (shutil.which(agent) or 'not installed (optional)'))
    return 0 if ok else 1
