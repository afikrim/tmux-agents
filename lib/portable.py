"""Dependency checks shared by the CLI and user-local installer."""
import locale
import re
import shutil
import subprocess
import sys


def dependency_report():
    ok = sys.version_info >= (3, 9)
    checks=[dict(name='Python',ok=ok,detail=sys.version.split()[0]+' (requires 3.9+)')]
    for name in ('tmux', 'ps'):
        path = shutil.which(name)
        valid = bool(path)
        detail = path or 'install with your system package manager'
        if path and name == 'tmux':
            try:
                result = subprocess.run([path, '-V'], capture_output=True, text=True, timeout=5)
                detail = result.stdout.strip()
                match = re.search(r'(\d+)\.(\d+)', detail)
                valid = result.returncode == 0 and bool(match) and tuple(map(int, match.groups())) >= (3, 3)
            except (OSError,subprocess.TimeoutExpired):
                detail,valid='Could not query tmux version',False
            detail += ' (requires 3.3+)'
        checks.append(dict(name=name,ok=valid,detail=detail))
        ok = ok and valid
    return dict(ok=ok,checks=checks,encoding=locale.getpreferredencoding(False),git=shutil.which('git'),
                agents={agent:shutil.which(agent) for agent in ('codex','claude','opencode')})


def doctor(report=None):
    report=dependency_report() if report is None else report
    for check in report['checks']:
        print(('OK' if check['ok'] else 'MISSING')+': '+check['name']+' — '+check['detail'])
    print('Terminal encoding: '+report['encoding'])
    print('Diff/worktree Git: '+(report['git'] or 'not installed (optional)'))
    for agent,path in report['agents'].items():
        print('Agent '+agent+': '+(path or 'not installed (optional)'))
    return 0 if report['ok'] else 1
