"""Status lifecycle + hook mapping + settings merge tests.

Reviewer reproductions covered:
- exit supersedes fresh needs-approval event
- blank/unbound events rejected; stale agent binding rejected
- numbered prose never fires; real widgets fire
- Stop-while-alive -> needs-input; Interrupt -> needs-input
- unknown payloads dropped (never running)
"""
import os
import sys
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from lib import adapters, status as st


class ExitSupersedesTest(unittest.TestCase):
    def test_exit_beats_fresh_approval_event(self):
        ev = {"kind": "needs-approval", "ts": time.time(), "ok_gen": True,
              "had_agent": True}
        s, src = st.classify("codex", ev, "", False, "running", False, True)
        self.assertEqual((s, src), ("done", "process"))

    def test_exit_shell_idle(self):
        s, src = st.classify("", None, "", False, "", False, False)
        self.assertEqual((s, src), ("idle", "process"))

    def test_exit_after_agent_is_done_not_idle(self):
        s, _ = st.classify("codex", None, "", False, "running", False, False)
        self.assertEqual(s, "done")


class EventBindingTest(unittest.TestCase):
    def test_unbound_event_ignored(self):
        ev = {"kind": "needs-input", "ts": time.time(), "ok_gen": False,
              "had_agent": True}
        s, src = st.classify("codex", ev, "plain", True, "running", False, False)
        self.assertNotEqual(s, "needs-input")

    def test_future_timestamp_rejected(self):
        ev = {"kind": "needs-input", "ts": time.time() + 3600, "ok_gen": True,
              "had_agent": True}
        s, _ = st.classify("codex", ev, "plain", True, "running", False, True)
        self.assertNotEqual(s, "needs-input")

    def test_stale_event_expires(self):
        ev = {"kind": "needs-approval", "ts": time.time() - 8000,
              "ok_gen": True, "had_agent": True}
        s, src = st.classify("codex", ev, "plain", True, "running", False, True)
        self.assertEqual((s, src), ("unknown", ""))

    def test_attention_persists_while_bound(self):
        # A still-bound, still-live approval waits longer than the old
        # 10-minute cutoff instead of silently dropping.
        ev = {"kind": "needs-approval", "ts": time.time() - 3600,
              "ok_gen": True, "had_agent": True}
        s, src = st.classify("codex", ev, "plain", True, "running", False, True)
        self.assertEqual((s, src), ("needs-approval", "event"))

    def test_bound_stop_means_needs_input(self):
        ev = {"kind": "needs-input", "ts": time.time(), "ok_gen": True,
              "had_agent": True}
        s, src = st.classify("codex", ev, "", True, "running", False, True)
        self.assertEqual((s, src), ("needs-input", "event"))

    def test_running_event_requires_binding(self):
        ev = {"kind": "running", "ts": time.time(), "ok_gen": False,
              "had_agent": True}
        s, src = st.classify("codex", ev, "plain", True, "", False, False)
        self.assertNotEqual(src, "event")

    def test_live_process_no_evidence_is_unknown(self):
        s, src = st.classify("codex", None, "some output", True, "", False, False)
        self.assertEqual((s, src), ("unknown", ""))

    def test_observed_activity_is_running_inferred(self):
        s, src = st.classify("codex", None, "some output", True, "", True, False)
        self.assertEqual((s, src), ("running", "inferred"))


class ScreenStrictnessTest(unittest.TestCase):
    def test_reviewer_numbered_prose_no_fire(self):
        text = "1. First improvement\n2. Second improvement\n› "
        self.assertIsNone(st.screen_attention(text))

    def test_prose_question_mark_no_fire(self):
        self.assertIsNone(st.screen_attention("How are you? Really?\n$ "))

    def test_blank_padding_does_not_hide_widget(self):
        text = "Approve execution? [y/n]: \n\n\n\n\n\n\n\n\n\n"
        self.assertEqual(st.screen_attention(text), "needs-approval")

    def test_scrollback_widget_outside_tail_ignored(self):
        old = "Approve? [y/n]: y\n" + "\n".join("filler %d" % i for i in range(30))
        self.assertIsNone(st.screen_attention(old))

    def test_question_needs_widget_and_mark(self):
        text = "Which one?\n❯ 1. apply\n  2. skip\n"
        self.assertEqual(st.screen_attention(text), "question")

    def test_yn_without_verb_is_question(self):
        self.assertEqual(st.screen_attention("Continue? [y/n]: "), "question")


class HookMappingTest(unittest.TestCase):
    def test_claude_codex_shapes(self):
        for agent in ("claude", "codex"):
            self.assertEqual(adapters.hook_kind(agent, "SessionStart", {}), "needs-input")
            self.assertEqual(adapters.hook_kind(agent, "UserPromptSubmit", {}), "running")
            self.assertEqual(adapters.hook_kind(agent, "PreToolUse", {}), "running")
            self.assertEqual(adapters.hook_kind(agent, "PermissionRequest", {}),
                             "needs-approval")
            self.assertEqual(adapters.hook_kind(agent, "Stop", {}), "needs-input")
            self.assertEqual(adapters.hook_kind(agent, "SessionEnd", {}), "done")
            self.assertEqual(adapters.hook_kind(agent, "Interrupt", {}), "needs-input")

    def test_notification_parsed_not_fabricated(self):
        self.assertEqual(adapters.hook_kind(
            "claude", "Notification", {"message": "Waiting for user input"}),
            "needs-input")
        self.assertEqual(adapters.hook_kind(
            "claude", "Notification", {"message": "Permission prompt shown"}),
            "needs-approval")
        self.assertEqual(adapters.hook_kind("claude", "Notification",
                                            {"message": "Build finished ok"}), "")

    def test_unknown_dropped_never_running(self):
        self.assertEqual(adapters.hook_kind("codex", "PreToolUseX", {}), "")
        self.assertEqual(adapters.hook_kind("codex", "", {}), "")
        self.assertEqual(adapters.hook_kind("opencode", "whatever", {}), "")

    def test_opencode_catalog_versions(self):
        self.assertEqual(adapters.OPENCODE_MAP["permission.asked"], "PermissionRequest")
        self.assertEqual(adapters.OPENCODE_MAP["session.execution.succeeded"], "Stop")
        self.assertEqual(adapters.OPENCODE_MAP["form.created"], "FormCreated")
        self.assertEqual(adapters.hook_kind("opencode", "FormCreated", {}), "question")

    def test_version_shaped_payloads(self):
        # Claude 2.1.x hook payload shape.
        p = {"hook_event_name": "PermissionRequest", "session_id": "abc",
             "cwd": "/tmp", "tool_name": "Bash"}
        self.assertEqual(adapters.hook_kind("claude", p["hook_event_name"], p),
                         "needs-approval")
        # Codex 0.169 hook payload shape.
        p = {"hook_event_name": "Stop", "session_id": "xyz", "cwd": "/tmp"}
        self.assertEqual(adapters.hook_kind("codex", p["hook_event_name"], p),
                         "needs-input")


class MergeTest(unittest.TestCase):
    CMD = adapters.hook_command("claude")

    def test_merge_preserves_existing(self):
        existing = '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}'
        out = adapters.merge_settings(existing, ["Stop", "SessionStart"], self.CMD)
        import json
        root = json.loads(out)
        stop_cmds = [h["command"] for g in root["hooks"]["Stop"] for h in g["hooks"]]
        self.assertIn("other", stop_cmds)
        self.assertIn(self.CMD, stop_cmds)
        self.assertIn("SessionStart", root["hooks"])

    def test_merge_idempotent(self):
        out = adapters.merge_settings("{}", ["Stop"], self.CMD)
        self.assertIsNone(adapters.merge_settings(out, ["Stop"], self.CMD))

    def test_remove_only_ours(self):
        out = adapters.merge_settings('{"a": 1}', ["Stop"], self.CMD)
        back, n = adapters.remove_command(out, self.CMD)
        import json
        root = json.loads(back)
        self.assertEqual(n, 1)
        self.assertNotIn("Stop", root.get("hooks", {}))
        out2 = adapters.merge_settings(
            '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "keep"}]}]}}',
            ["Stop"], self.CMD)
        back2, n2 = adapters.remove_command(out2, self.CMD)
        root2 = json.loads(back2)
        cmds = [h["command"] for g in root2["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(cmds, ["keep"])
        self.assertEqual(n2, 1)

    def test_jsonc_strip(self):
        raw = '{\n// comment\n"a": 1, /* x */ "b": "http://e.c/\\"//\\""\n}'
        import json
        self.assertEqual(json.loads(adapters.strip_jsonc(raw))["a"], 1)

    def test_hook_command_gated_on_tmux_pane(self):
        self.assertIn("TMUX_PANE", self.CMD)
        self.assertIn("agent-event", self.CMD)


class SocketNamespaceTest(unittest.TestCase):
    def test_realistic_tmux_env_parses_socket_path(self):
        import os
        from lib import tmuxutil as tu
        old = os.environ.get("TMUX")
        try:
            os.environ["TMUX"] = "/private/tmp/tmux-502/default,123,0"
            tu._sid_cache.clear()
            prefix, sid = tu.resolve_socket()
            self.assertEqual(prefix, ["-S", "/private/tmp/tmux-502/default"])
            self.assertTrue(sid.startswith("S-"))
            # Must NOT resolve to the trailing pane index.
            self.assertNotIn(" 0", " ".join(prefix))
        finally:
            if old is None:
                os.environ.pop("TMUX", None)
            else:
                os.environ["TMUX"] = old
            tu._sid_cache.clear()

    def test_cli_flags_roundtrip(self):
        from lib import tmuxutil as tu
        self.assertEqual(tu.cli_socket_flags(["-L", "x"]), ["--socket", "x"])
        self.assertEqual(tu.cli_socket_flags(["-S", "/p"]), ["--socket-path", "/p"])
        self.assertEqual(tu.cli_socket_flags([]), [])


class HookKindExtraTest(unittest.TestCase):
    def test_pretooluse_question_tools(self):
        self.assertEqual(adapters.hook_kind(
            "claude", "PreToolUse", {"tool_name": "AskUserQuestion"}), "question")
        self.assertEqual(adapters.hook_kind(
            "codex", "PreToolUse", {"tool_name": "request_user_input"}), "question")
        self.assertEqual(adapters.hook_kind(
            "claude", "PreToolUse", {"tool_name": "Bash"}), "running")

    def test_notification_types(self):
        self.assertEqual(adapters.hook_kind(
            "claude", "Notification", {"notification_type": "permission_prompt"}),
            "needs-approval")
        self.assertEqual(adapters.hook_kind(
            "claude", "Notification", {"notification_type": "idle_prompt"}),
            "needs-input")
        # Bare Notification means the agent waits: never dropped.
        self.assertEqual(adapters.hook_kind("claude", "Notification", {}),
                         "needs-input")

    def test_auto_approval_no_attention(self):
        self.assertEqual(adapters.hook_kind(
            "claude", "PermissionRequest",
            {"tool_name": "Bash", "decision": "allow"}), "running")

    def test_opencode_idle_maps_needs_input(self):
        self.assertEqual(adapters.hook_kind("opencode", "Notification", {}),
                         "needs-input")


class StoreRoundtripTest(unittest.TestCase):
    def test_append_read_bind(self):
        import tempfile
        from lib import store
        d = tempfile.mkdtemp(prefix="ta-store-")
        paths = {"dir": d, "state": os.path.join(d, "s.json"),
                 "events": os.path.join(d, "e.jsonl")}
        try:
            rec = store.append_event(paths, "%9", "needs-input", agent="codex",
                                     agent_pid=111, agent_start=222.0,
                                     socket_id="S-abc")
            self.assertEqual(rec["kind"], "needs-input")
            self.assertEqual(oct(os.stat(paths["events"]).st_mode & 0o777), "0o600")
            evs = store.read_events(paths, max_age=30)
            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0]["apid"], 111)
            # Malformed lines are skipped, never fatal.
            with open(paths["events"], "a") as f:
                f.write("not json\n")
            self.assertEqual(len(store.read_events(paths, max_age=30)), 1)
            # Rotation keeps working set, no stray files.
            for _ in range(4500):
                store.append_event(paths, "%9", "running", agent="codex")
            self.assertFalse(os.path.exists(paths["events"] + ".rot"))
            self.assertFalse(os.path.exists(paths["events"] + ".new"))
            self.assertLess(os.path.getsize(paths["events"]), 512 * 1024)
            self.assertTrue(len(store.read_events(paths, max_age=30)) > 0)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class ExistingTerminalTest(unittest.TestCase):
    def test_idle_prompt_and_working_footer(self):
        for agent in ("codex", "claude"):
            self.assertEqual(st.classify(agent, None, "Done.\n› \ncontext left", True,
                                        "", False, False), ("needs-input", "screen"))
            self.assertEqual(st.classify(agent, None, "› task\nesc to interrupt", True,
                                        "", False, False), ("running", "screen"))
