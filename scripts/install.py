#!/usr/bin/env python3
"""Install a self-contained copy; never change the user's shell or terminal."""
import argparse
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from lib.portable import doctor
from lib import adapters

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--prefix', type=Path, help='installation prefix (default: ~/.local; honors XDG_DATA_HOME)')
ap.add_argument('--integrate', nargs='+', choices=['codex', 'claude', 'opencode'], default=[])
args = ap.parse_args()
if doctor():
    sys.exit('Install the missing dependencies, then rerun install.sh.')
prefix = (args.prefix or Path.home() / '.local').expanduser().resolve()
if args.prefix:
    data = prefix / 'share'
else:
    data = Path(os.environ.get('XDG_DATA_HOME') or str(prefix / 'share')).expanduser()
target = data.resolve() / 'tmux-agents'
launcher = prefix / 'bin' / 'tmux-agents'
entry = target / 'bin' / 'tmux-agents'
if launcher.exists() or launcher.is_symlink():
    if not launcher.is_symlink() or launcher.resolve() != entry:
        sys.exit('Refusing to replace unrelated launcher: ' + str(launcher))
RUNTIME_DIRS = ('bin', 'lib', 'hooks', 'plugins', 'extras')
RUNTIME_DOCS = ('README.md', 'DESIGN.md', 'LICENSE-NOTICE', 'VERSION')
if os.path.islink(target):
    sys.exit('Refusing to follow symlink runtime directory: ' + str(target))
target.parent.mkdir(parents=True, exist_ok=True)
backup = None
stage = Path(tempfile.mkdtemp(prefix='.tmux-agents-', dir=target.parent))
have_backup = False
moved_stage = False
created_launcher = False
configuring_tmux = False
provider_snapshots = {}
try:
    for name in RUNTIME_DIRS:
        src = SOURCE / name
        if src.is_dir():
            shutil.copytree(src, stage / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'node_modules'))
    for name in RUNTIME_DOCS:
        shutil.copy2(SOURCE / name, stage / name)
    if target.exists():
        # A runtime copy has no user data; backups and state live outside it.
        if not (target / 'bin/tmux-agents').is_file():
            sys.exit('Refusing to replace unrelated directory: ' + str(target))
        backup = Path(tempfile.mkdtemp(prefix='.tmux-agents-previous-', dir=target.parent))
        backup.rmdir()
        target.rename(backup)
        have_backup = True
    stage.rename(target)
    moved_stage = True
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if not launcher.is_symlink():
        launcher.symlink_to(entry)
        created_launcher = True
    # Integrations happen before tmux activation. Restore their exact bytes if
    # a later integration fails, so no handler points at a removed runtime.
    locations = {'codex': adapters.codex_hooks_path,
                 'claude': adapters.claude_settings_path,
                 'opencode': adapters.opencode_config_path}
    for agent in dict.fromkeys(args.integrate):
        path = Path(locations[agent]())
        provider_snapshots[path] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
    for agent in dict.fromkeys(args.integrate):
        subprocess.run([sys.executable, str(entry), 'integrate', agent], check=True)
    configuring_tmux = True
    subprocess.run([sys.executable, str(entry), 'install'], check=True)
except SystemExit:
    raise
except Exception as exc:
    if configuring_tmux:
        # tmux may have partially sourced a user's config. Keep every new
        # handler and sidebar executable available for repair/uninstall.
        message = 'Configuration failed; installed runtime retained at %s. Retry install.sh or run %s uninstall.' % (target, shlex.quote(str(launcher)))
        if have_backup:
            message += ' Previous runtime retained at ' + str(backup)
        sys.exit(message + '\n' + str(exc))
    for path, snapshot in provider_snapshots.items():
        if snapshot is None:
            path.unlink(missing_ok=True)
        else:
            content, mode = snapshot
            fd, restored = tempfile.mkstemp(prefix='.tmux-agents-restore-', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(restored, mode)
                os.replace(restored, path)
            finally:
                if os.path.exists(restored):
                    os.unlink(restored)
    if moved_stage:
        # New copy or configuration failed: drop the new tree, restore the old.
        if os.path.islink(target):
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        if have_backup and not target.exists():
            backup.rename(target)
        if created_launcher and launcher.is_symlink():
            launcher.unlink()
    elif have_backup and not target.exists():
        backup.rename(target)
    sys.exit('Install failed; provider settings and previous runtime restored: %s' % exc)
finally:
    if stage.exists():
        shutil.rmtree(stage)
if have_backup and backup.exists() and not os.path.islink(backup):
    shutil.rmtree(backup)
print('\nInstalled runtime: ' + str(target))
print('Installed launcher: ' + str(launcher))
if str(launcher.parent) not in os.environ.get('PATH', '').split(os.pathsep):
    print('Add the launcher to your PATH, e.g. in ~/.profile:')
    print('    export PATH=%s:"$PATH"' % shlex.quote(str(launcher.parent)))
    print('Then open a new shell (or log in again) before running tmux-agents.')
print('Start tmux. Run tmux-agents doctor to check this machine.')
