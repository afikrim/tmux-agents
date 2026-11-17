"""UI row model / navigation / input parsing tests (pure, no tmux)."""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from lib import ui


def sess():
    return [{"id": "$1", "name": "dev: main", "attached": True, "windows": [
        {"id": "@1", "index": "1", "name": "a b:c", "active": True, "panes": [
            {"id": "%1", "agent": "codex", "status": "needs-approval", "source": "event", "current": True},
            {"id": "%9", "agent": "", "status": "idle", "source": "process", "current": False}]},
        {"id": "@2", "index": "2", "name": "Terminal", "panes": [
            {"id": "%2", "agent": "", "status": "idle", "source": "process"}]}]}]


class RowsTest(unittest.TestCase):
    def test_stable_ids_survive_punctuation(self):
        rows = ui.build_rows(sess())
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds, ["session", "section", "pane", "pane"])
        self.assertEqual(rows[0]["target"], ("session", "$1"))
        self.assertEqual(rows[2]["win_id"], "@1")
        self.assertIn("dev: main", rows[0]["label"])
        self.assertIn("a b:c", rows[2]["window_name"])
        self.assertNotIn("%9", [row["ref"] for row in rows])

    def test_source_marks_visible(self):
        rows = ui.build_rows(sess())
        self.assertIn("=", rows[2]["label"])   # event
        self.assertIn("APPROVAL", rows[2]["label"])
        self.assertIn("[IDLE]", rows[3]["label"])  # idle/process: no src mark
        self.assertNotIn("~", rows[3]["label"])
        self.assertNotIn("#", rows[3]["label"])

    def test_source_marks_all(self):
        for status, src, mark in [("running", "inferred", "~"),
                                  ("running", "event", "="),
                                  ("question", "screen", "#"),
                                  ("unknown", "", "?")]:
            s = [{"id": "$1", "name": "s", "attached": False, "windows": [
                {"id": "@1", "index": "1", "name": "w", "active": True,
                 "panes": [{"id": "%1", "agent": "codex", "status": status,
                            "source": src, "current": False}]}]}]
            self.assertIn(mark, ui.build_rows(s)[2]["label"], (status, src))

    def test_activation_uses_stable_ids(self):
        rows = ui.build_rows(sess())
        self.assertEqual(ui.activation_tmux_args(rows[0]),
                         ["switch-client", "-t", "$1"])
        self.assertEqual(ui.activation_tmux_args(rows[2]),
                         ["select-pane", "-t", "%1"])

    def test_keyboard_skips_section_labels_both_directions(self):
        rows = ui.build_rows(sess())
        self.assertEqual(rows[1]["kind"], "section")
        for key in ("j", "down"):
            self.assertEqual(ui.move_selection(rows, 0, key), 2)
        for key in ("k", "up"):
            self.assertEqual(ui.move_selection(rows, 2, key), 0)
        self.assertEqual(ui.move_selection(rows, 3, "j"), 3)
        self.assertEqual(ui.move_selection(rows, 0, "k"), 0)
        self.assertEqual(ui.move_selection(rows, 1, "j"), 2)
        self.assertEqual(ui.move_selection(rows, 1, "k"), 0)

    def test_project_preview_scopes_agents_and_keeps_projects_first(self):
        sessions = sess() + [{"id":"$2", "name":"next", "windows":[
            {"id":"@3", "name":"visible-name", "panes":[
                {"id":"%3", "agent":"claude", "status":"idle"}]}]}]
        rows = ui.build_rows(sessions)
        self.assertEqual([row["kind"] for row in rows[:3]], ["session", "session", "section"])
        self.assertEqual([row["win_id"] for row in rows if row["kind"] == "pane"], ["@1", "@2"])
        preview = ui.build_rows(sessions, "$2")
        self.assertEqual([row["ref"] for row in preview if row["kind"] == "pane"], ["%3"])
        self.assertEqual(ui.move_selection(preview, 1, "j"), 3)
        self.assertEqual(ui.move_selection(preview, 3, "k"), 1)
        self.assertEqual(ui.move_selection(preview, 0, "G"), 3)

    def test_cursor_tracks_current_only_when_unfocused_and_survives_reordering(self):
        rows = ui.build_rows(sess())
        self.assertEqual(ui.selection_index(rows,'%2',True),3)
        self.assertEqual(ui.selection_index(rows,'%2',False),2)
        reordered = [rows[0],rows[1],rows[3],rows[2]]
        self.assertEqual(ui.selection_index(reordered,'%2',True),2)
        self.assertEqual(ui.selection_index(reordered,'%2',False),3)

    def test_scroll_window(self):
        rows = ui.build_rows(sess()) * 10
        lines, top = ui.render_rows(rows, 35, 0, 10, 40)
        self.assertEqual(len(lines), 10)
        self.assertGreater(top, 0)
        for line in lines:
            import re
            plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
            self.assertLessEqual(ui.cell_width(plain), 40)


class KeysTest(unittest.TestCase):
    def test_motion_packets_survive_every_read_boundary_and_escape_still_works(self):
        packet = '\x1b[<35;12;24M'
        for split in range(1,len(packet)):
            decoder = ui.InputDecoder()
            self.assertEqual(decoder.feed(packet[:split]), [])
            self.assertEqual(decoder.feed(packet[split:]), [('mouse',35,12,24,False)])
        decoder = ui.InputDecoder()
        self.assertEqual(decoder.feed('j\x1b'), ['j'])
        self.assertEqual(decoder.feed(flush=True), ['esc'])
        self.assertEqual(decoder.feed('\x1b[<35;12;'), [])
        self.assertEqual(decoder.feed(flush=True), [])
        self.assertEqual(decoder.feed('k'), ['k'])
    def test_arrows_and_vim(self):
        self.assertEqual(ui.parse_input("\x1b[A"), ["up"])
        self.assertEqual(ui.parse_input("\x1b[B"), ["down"])
        self.assertEqual(ui.parse_input("j"), ["j"])
        self.assertEqual(ui.parse_input("\r"), ["enter"])
        self.assertEqual(ui.parse_input("\x1b"), ["esc"])
        self.assertEqual(ui.parse_input("\x1b[5~"), ["pageup"])
        self.assertEqual(ui.parse_input("\x1b[6~"), ["pagedown"])

    def test_mouse_sgr(self):
        keys = ui.parse_input("\x1b[<0;5;3M")
        self.assertEqual(keys, [("mouse", 0, 5, 3, False)])
        keys = ui.parse_input("\x1b[<0;5;3m")
        self.assertEqual(keys, [("mouse", 0, 5, 3, True)])

    def test_move_cursor(self):
        self.assertEqual(ui.move_cursor(10, 0, "j"), 1)
        self.assertEqual(ui.move_cursor(10, 0, "k"), 0)
        self.assertEqual(ui.move_cursor(10, 9, "j"), 9)
        self.assertEqual(ui.move_cursor(10, 3, "G"), 9)
        self.assertEqual(ui.move_cursor(10, 3, "g"), 0)


if __name__ == "__main__":
    unittest.main()
