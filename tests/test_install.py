"""Install/uninstall reversibility on a private server + TEMP HOME.

Covers: sentinel hooks/bindings preserved, owned-index reuse (no drift),
legacy cleanup, no recreation after uninstall past the poll interval,
narrow-window collapse/restore, daemon singleton.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(BASE, "bin", "tmux-agents")
SOCK = "ta-install-%d" % os.getpid()


def T(*args, timeout=15):
    return subprocess.run(["tmux", "-L", SOCK, "-f", "/dev/null", *args],
                          capture_output=True, text=True, timeout=timeout)


class InstallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="ta-inst-home-")
        cls.conf = os.path.join(cls.home, "tmux.conf")
        with open(cls.conf, "w") as f:
            f.write("# test conf\nset -g base-index 1\n")
        cls.claude = os.path.join(cls.home, "claude-settings.json")
        with open(cls.claude, "w") as f:
            f.write('{"hooks": {"Stop": [{"hooks": [{"type": "command", '
                    '"command": "sentinel-cmd"}]}]}}')
        cls.codex = os.path.join(cls.home, "codex-hooks.json")
        with open(cls.codex, "w") as f:
            f.write('{"hooks": {}}')
        cls.opencode = os.path.join(cls.home, "opencode.jsonc")
        with open(cls.opencode, "w") as f:
            f.write('{\n// comment\n"plugins": []\n}\n')
        T("kill-server")
        time.sleep(0.2)
        T("new-session", "-d", "-s", "inst", "-x", "200", "-y", "50")

    @classmethod
    def tearDownClass(cls):
        try:
            env = cls.env(cls)
            subprocess.run([sys.executable, BIN, "--socket", SOCK,
                            "daemon-stop"], env=env, timeout=15)
        except Exception:
            pass
        T("kill-server")
        shutil.rmtree(cls.home, ignore_errors=True)

    @staticmethod
    def env(_=None):
        e = dict(os.environ)
        e["TMUX_AGENTS_STATE_DIR"] = os.path.join(InstallTest.home, "state")
        e["TMUX_AGENTS_TMUX_CONF"] = InstallTest.conf
        e["TMUX_AGENTS_CLAUDE_SETTINGS"] = InstallTest.claude
        e["TMUX_AGENTS_CODEX_HOOKS"] = InstallTest.codex
        e["TMUX_AGENTS_OPENCODE_JSONC"] = InstallTest.opencode
        e.pop("TMUX", None)
        e.pop("TMUX_PANE", None)
        return e

    def cli(self, *args, timeout=60):
        return subprocess.run([sys.executable, BIN, "--socket", SOCK, *args],
                              capture_output=True, text=True,
                              timeout=timeout, env=self.env())

    def test_install_uninstall_roundtrip(self):
        from lib.theme import TMUX_OPTIONS
        original_theme = {}
        for window, name, _ in TMUX_OPTIONS:
            flags = ["-g", "-w"] if window else ["-g"]
            original_theme[name] = T("show-options", *flags, "-v", name).stdout.rstrip("\n")
        # Sentinel hook + binding that must survive everything.
        T("set-hook", "-g", "after-new-window[5]", "run-shell 'echo sentinel'")
        T("bind-key", "-T", "prefix", "Q", "display-message", "sentinel")
        T("bind-key", "-T", "prefix", "v", "display-message", "prior-diff-key")
        r = self.cli("install")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("sourced rc=0", r.stdout)
        # Block present in temp conf, production untouched.
        with open(self.conf) as f:
            conf = f.read()
        self.assertIn("# >>> tmux-agents >>>", conf)
        production = Path.home() / ".tmux.conf"
        if production.exists():
            self.assertNotIn("ta-install", production.read_text())
        time.sleep(3.0)
        # Sidebar present exactly once; sentinel hook intact.
        panes = T("list-panes", "-a", "-F", "#{pane_id}").stdout.split()
        self.assertEqual(len(panes), 2, panes)
        hooks = T("show-hooks", "-g").stdout
        self.assertIn("after-new-window[5]", hooks)
        self.assertIn("tmux-agents", hooks)
        keys = T("list-keys", "-T", "prefix", "Q").stdout
        self.assertIn("sentinel", keys)
        # Owned index recorded and reused on refresh (no drift).
        def install_meta():
            subs = [d for d in os.listdir(os.path.join(self.home, "state"))
                    if os.path.isdir(os.path.join(self.home, "state", d))]
            self.assertEqual(len(subs), 1, subs)
            return json.loads(Path(self.home, "state", subs[0], "install.json").read_text()), subs[0]
        meta1, sub = install_meta()
        r = self.cli("install")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        meta2, sub2 = install_meta()
        self.assertEqual(sub, sub2, "socket namespace must be stable")
        self.assertEqual(meta1["hook_index"], meta2["hook_index"])
        # Saved bindings recorded.
        self.assertIn("prefix:S", meta2.get("saved_keys", {}))
        # Daemon singleton: two starters -> same live pid in pidfile.
        pidfile = os.path.join(self.home, "state", sub, "daemon.pid")
        def daemon_pid():
            with open(pidfile) as f:
                return f.read().strip().split()[0]
        self.cli("daemon-start")
        time.sleep(1.0)
        pid1 = daemon_pid()
        self.assertTrue(pid1.isdigit(), pid1)
        self.cli("daemon-start")
        time.sleep(1.0)
        pid2 = daemon_pid()
        self.assertEqual(pid1, pid2)
        self.assertEqual(T("list-panes", "-a", "-F", "#{pane_id}").stdout.split().count("%0"), 1)
        ps = subprocess.run(["ps", "-p", pid1, "-o", "command="],
                            capture_output=True, text=True).stdout
        self.assertIn("tmux-agents", ps)
        # Provider integrations merged non-destructively.
        for prov in ("claude", "codex", "opencode"):
            rr = self.cli("integrate", prov)
            self.assertEqual(rr.returncode, 0, rr.stderr + rr.stdout)
        cl = json.loads(Path(self.claude).read_text())
        stop_cmds = [h["command"] for g in cl["hooks"]["Stop"]
                     for h in g["hooks"]]
        self.assertIn("sentinel-cmd", stop_cmds)
        from lib.adapters import hook_command
        self.assertIn(hook_command("claude"), stop_cmds)
        oc = json.loads(Path(self.opencode).read_text())
        self.assertTrue(any(isinstance(p, dict) and "tmux-agents" in
                            str(p.get("package", "")) for p in oc["plugins"]))
        # Optional diff is owned infrastructure too; uninstall removes it and
        # private code checkpoints, and restores the user's previous v bind.
        r = self.cli("diff", "--pane", "%0")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(len(T("list-panes", "-a", "-F", "#{pane_id}").stdout.split()), 3)
        private = Path(self.home, "state", sub, "diff")
        private.mkdir(exist_ok=True)
        (private / "checkpoint.json.gz").write_bytes(b"owned checkpoint")
        # Uninstall: hooks/keys/sidebars gone, sentinels preserved.
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        time.sleep(3.5)  # past the poll interval: nothing may recreate
        panes = T("list-panes", "-a", "-F", "#{pane_id}").stdout.split()
        self.assertEqual(panes, ["%0"], panes)
        self.assertFalse(private.exists())
        hooks = T("show-hooks", "-g").stdout
        self.assertNotIn("tmux-agents", hooks)
        self.assertIn("after-new-window[5]", hooks)
        keys = T("list-keys", "-T", "prefix", "Q").stdout
        self.assertIn("sentinel", keys)
        self.assertNotIn("tmux-agents", T("list-keys").stdout)
        self.assertIn("prior-diff-key", T("list-keys", "-T", "prefix", "v").stdout)
        with open(self.conf) as f:
            self.assertNotIn("tmux-agents", f.read())
        # Provider entries removed, sentinels kept.
        cl = json.loads(Path(self.claude).read_text())
        stop_cmds = [h["command"] for g in cl["hooks"]["Stop"]
                     for h in g["hooks"]]
        self.assertEqual(stop_cmds, ["sentinel-cmd"])
        oc = json.loads(Path(self.opencode).read_text())
        self.assertEqual(oc["plugins"], [])
        for window, name, _ in TMUX_OPTIONS:
            flags = ["-g", "-w"] if window else ["-g"]
            self.assertEqual(T("show-options", *flags, "-v", name).stdout.rstrip("\n"), original_theme[name], name)

    def test_narrow_collapse_restore(self):
        self.cli("install")
        time.sleep(3.0)
        self.assertEqual(len(T("list-panes", "-a", "-F",
                               "#{pane_id}").stdout.split()), 2)
        first = T("list-windows", "-t", "inst", "-F",
                  "#{window_index}").stdout.split()[0]
        T("resize-window", "-t", "inst:%s" % first, "-x", "60", "-y", "20")
        time.sleep(0.5)
        self.assertIn("60", T("list-windows", "-a", "-F",
                              "#{window_width}").stdout.split())
        self.cli("ensure")
        time.sleep(1.0)
        self.assertEqual(len(T("list-panes", "-a", "-F",
                               "#{pane_id}").stdout.split()), 1)
        T("resize-window", "-t", "inst:%s" % first, "-x", "200", "-y", "50")
        time.sleep(0.5)
        self.cli("ensure")
        time.sleep(2.0)
        self.assertEqual(len(T("list-panes", "-a", "-F",
                               "#{pane_id}").stdout.split()), 2)
        self.cli("uninstall")


if __name__ == "__main__":
    unittest.main()
