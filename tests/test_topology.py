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
            {"id": "%1", "agent": "codex", "status": "needs-approval",
             "source": "event", "current": True, "selected": False},
            {"id": "%9", "agent": "", "status": "idle",
             "source": "process", "current": False, "selected": True},
        ]}]}]


class RowsTest(unittest.TestCase):
    def test_stable_ids_survive_punctuation(self):
        rows = ui.build_rows(sess())
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds, ["session", "window", "pane", "pane"])
        self.assertEqual(rows[0]["target"], ("session", "$1"))
        self.assertEqual(rows[1]["target"], ("window", "@1"))
        self.assertIn("dev: main", rows[0]["label"])
        self.assertIn("a b:c", rows[1]["label"])

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
        self.assertEqual(ui.activation_tmux_args(rows[1]),
                         ["select-window", "-t", "@1"])
        self.assertEqual(ui.activation_tmux_args(rows[2]),
                         ["select-pane", "-t", "%1"])

    def test_keyboard_skips_visible_window_names_both_directions(self):
        rows = ui.build_rows(sess())
        self.assertEqual(rows[1]["kind"], "window")
        for key in ("j", "down"):
            self.assertEqual(ui.move_selection(rows, 0, key), 2)
        for key in ("k", "up"):
            self.assertEqual(ui.move_selection(rows, 2, key), 0)
        self.assertEqual(ui.move_selection(rows, 3, "j"), 3)
        self.assertEqual(ui.move_selection(rows, 0, "k"), 0)
        self.assertEqual(ui.move_selection(rows, 1, "j"), 2)
        self.assertEqual(ui.move_selection(rows, 1, "k"), 0)

    def test_keyboard_skips_labels_across_sessions(self):
        sessions = sess() + [{"id":"$2", "name":"next", "windows":[
            {"id":"@2", "index":"1", "name":"visible-name", "panes":[
                {"id":"%2", "agent":"claude", "status":"idle"}]}]}]
        rows = ui.build_rows(sessions)
        second = next(i for i, row in enumerate(rows) if row["ref"] == "$2")
        self.assertEqual(ui.move_selection(rows, 3, "j"), second)
        self.assertEqual(ui.move_selection(rows, second, "j"), second + 2)
        self.assertEqual(ui.move_selection(rows, second + 2, "k"), second)
        self.assertEqual(ui.move_selection(rows, second, "k"), 3)
        self.assertEqual(ui.move_selection(rows, 0, "G"), len(rows) - 1)

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
