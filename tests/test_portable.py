"""Fresh-machine installation, including cold tmux startup and relocated source."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PortableTest(unittest.TestCase):
    def test_failed_integration_restores_fresh_and_existing_install(self):
        for upgrade in (False, True):
            with self.subTest(upgrade=upgrade), tempfile.TemporaryDirectory(prefix='ta rollback ') as tmp:
                home = Path(tmp)
                env = {k: v for k, v in os.environ.items()
                       if not k.startswith(('TMUX', 'XDG_', 'CODEX_', 'OPENCODE_'))}
                env.update(HOME=str(home), TMUX_TMPDIR=str(home))
                prefix = home / 'custom prefix'
                args = [sys.executable, str(ROOT / 'scripts/install.py'), '--prefix', str(prefix)]
                target = prefix / 'share/tmux-agents'
                if upgrade:
                    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=45)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    (target / 'prior-marker').write_text('previous runtime')
                conf = home / '.tmux.conf'
                before_conf = conf.read_bytes() if conf.exists() else None
                claude = home / '.claude/settings.json'
                claude.parent.mkdir(exist_ok=True)
                claude.write_text('{"sentinel":true}\n')
                codex = home / '.codex/hooks.json'
                codex.parent.mkdir()
                codex.write_bytes(b'invalid JSON \xff')
                result = subprocess.run(args + ['--integrate', 'claude', 'codex'], env=env,
                                        capture_output=True, text=True, timeout=45)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(claude.read_text(), '{"sentinel":true}\n')
                self.assertEqual(codex.read_bytes(), b'invalid JSON \xff')
                self.assertEqual(conf.read_bytes() if conf.exists() else None, before_conf)
                if upgrade:
                    self.assertEqual((target / 'prior-marker').read_text(), 'previous runtime')
                    self.assertTrue((prefix / 'bin/tmux-agents').exists())
                else:
                    self.assertFalse(target.exists())
                    self.assertFalse((prefix / 'bin/tmux-agents').is_symlink())

    def test_upgrade_success_replaces_runtime_and_cleans_backup(self):
        with tempfile.TemporaryDirectory(prefix='ta upgrade ok ') as tmp:
            home = Path(tmp)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(('TMUX', 'XDG_', 'CODEX_', 'OPENCODE_'))}
            env.update(HOME=str(home), TMUX_TMPDIR=str(home))
            prefix = home / 'custom prefix'
            args = [sys.executable, str(ROOT / 'scripts/install.py'), '--prefix', str(prefix)]
            target = prefix / 'share/tmux-agents'
            for attempt in range(2):
                result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((target / 'bin/tmux-agents').is_file())
            leftovers = [p for p in target.parent.iterdir()
                         if p.name.startswith('.tmux-agents-previous-') or p.name.startswith('.tmux-agents-')]
            self.assertEqual(leftovers, [])

    def test_installer_refuses_unrelated_and_symlink_targets(self):
        # No tmux server needed: refusals happen before any configuration.
        with tempfile.TemporaryDirectory(prefix='ta refuse ') as tmp:
            home = Path(tmp)
            env = dict(os.environ, HOME=str(home))
            custom = home / 'custom'
            target = custom / 'share' / 'tmux-agents'
            target.mkdir(parents=True)
            (target / 'junk.txt').write_text('not a runtime')
            def attempt():
                return subprocess.run(
                    [sys.executable, str(ROOT / 'scripts' / 'install.py'),
                     '--prefix', str(custom)],
                    env=env, text=True, capture_output=True, timeout=60)
            r = attempt()
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('unrelated', r.stdout + r.stderr)
            self.assertTrue((target / 'junk.txt').is_file())
            shutil.rmtree(target)
            real = home / 'real-runtime'
            real.mkdir()
            os.symlink(real, target)
            r = attempt()
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('symlink', (r.stdout + r.stderr).lower())
            self.assertTrue(os.path.islink(target))

    def test_cold_bootstrap(self):
        with tempfile.TemporaryDirectory(prefix='ta portable ') as tmp:
            home = Path(tmp)
            source = home / 'source tree'
            source.mkdir()
            for name in ('bin', 'lib', 'hooks', 'plugins', 'extras', 'scripts'):
                shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns('__pycache__'))
            for name in ('install.sh', 'README.md', 'DESIGN.md', 'LICENSE-NOTICE', 'VERSION'):
                shutil.copy2(ROOT / name, source / name)
            conf = home / '.tmux.conf'
            conf.write_text('set -g status-left ORIGINAL\nset -g mouse off\nbind-key S display-message sentinel\n' +
                            ''.join('set-hook -g after-new-window[%d] "display-message sentinel-%d"\n' % (i, i)
                                    for i in range(90, 100)))
            sock = str(home / 'server')
            env = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home / 'data'),
                       XDG_STATE_HOME=str(home / 'state'), TMUX_TMPDIR=str(home))
            for key in list(env):
                if key.startswith('TMUX_AGENTS_') or key in ('TMUX', 'TMUX_PANE', 'XDG_CONFIG_HOME'):
                    env.pop(key)
            def run(*args):
                return subprocess.run(args, env=env, text=True, capture_output=True, timeout=45)
            # Force default socket to this temporary root, away from real servers.
            r = run('sh', str(source / 'install.sh'), '--integrate', 'codex', 'claude', 'opencode')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('no server was started', r.stdout)
            launcher = home / '.local/bin/tmux-agents'
            shutil.rmtree(source)
            # Installed copy is self-contained: runtime + opt-in Kitty profile
            # survive removal of the extracted source.
            self.assertTrue((home / 'data' / 'tmux-agents' / 'bin' / 'tmux-agents').is_file())
            self.assertTrue((home / 'data' / 'tmux-agents' / 'extras' / 'kitty-tmux-agents.conf').is_file())
            self.assertEqual(run(str(launcher), 'doctor').returncode, 0)
            self.assertNotIn(str(ROOT), conf.read_text())
            def tmux(*args):
                return run('tmux', '-S', sock, *args)
            try:
                r = tmux('-f', str(conf), 'new-session', '-d', '-s', 'portable', '-x', '160', '-y', '40')
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    panes = tmux('list-panes', '-a', '-F', '#{pane_id} #{@tmux_agents_sidebar}').stdout.splitlines()
                    if len(panes) == 2 and any(p.endswith(' 1') for p in panes):
                        break
                    time.sleep(.2)
                self.assertEqual(len(panes), 2, panes)
                for _ in range(2):
                    self.assertEqual(tmux('source-file', str(conf)).returncode, 0)
                hooks = tmux('show-hooks', '-g').stdout + tmux('show-hooks', '-gw').stdout
                self.assertEqual(hooks.count('sentinel-'), 10, hooks)
                owned = [line for line in hooks.splitlines() if '/bin/tmux-agents' in line]
                self.assertEqual(len(owned), 5, hooks)
                r = run(str(launcher), '--socket-path', sock, 'uninstall')
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertEqual(tmux('show-options', '-gv', 'status-left').stdout.strip(), 'ORIGINAL')
                self.assertEqual(tmux('show-options', '-gv', 'mouse').stdout.strip(), 'off')
                self.assertIn('sentinel', tmux('list-keys', '-T', 'prefix', 'S').stdout)
                self.assertEqual(len(tmux('list-panes', '-a').stdout.splitlines()), 1)
                self.assertNotIn('# >>> tmux-agents >>>', conf.read_text())
                self.assertEqual(tmux('show-hooks', '-g').stdout.count('sentinel-'), 10)
            finally:
                run(str(launcher), '--socket-path', sock, 'daemon-stop')
                tmux('kill-server')
