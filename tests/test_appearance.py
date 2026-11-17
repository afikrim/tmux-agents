import re
import unittest
from lib import ui, theme

ANSI = re.compile(r"\x1b\[[0-9;]*m")

class AppearanceTest(unittest.TestCase):
    def test_frames_fit_short_narrow_and_wide_terminals(self):
        sessions = [{"id":"$1", "name":"工具-development", "windows":[
            {"id":"@1", "index":"1", "name":"資料 é", "panes":[
                {"id":"%1", "agent":"opencode", "status":"needs-approval", "source":"event"}]}]}]
        rows = ui.build_rows(sessions)
        for width, height in ((12,6),(24,12),(32,57),(40,24)):
            lines, top, header, body = ui.frame(rows,sessions,2,0,width,height)
            self.assertEqual(len(lines),height)
            self.assertTrue(all(ui.cell_width(ANSI.sub("",line)) == width for line in lines), (width,height))
            self.assertLessEqual(header+body,height)

    def test_palette_foregrounds_have_readable_contrast(self):
        def lum(h):
            rgb=[int(h[i:i+2],16)/255 for i in (1,3,5)]
            rgb=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
            return sum(v*c for v,c in zip(rgb,(.2126,.7152,.0722)))
        for foreground in ('fg','muted','accent','amber','rose','blue'):
            for background in ('bg','panel','selected'):
                a,b=sorted((lum(theme.PALETTE[foreground]),lum(theme.PALETTE[background])))
                self.assertGreaterEqual((b+.05)/(a+.05),4.5,(foreground,background))

    def test_session_spacing_is_not_a_keyboard_stop(self):
        rows=ui.build_rows([{"id":"$1","name":"one","windows":[]},
                            {"id":"$2","name":"two","windows":[]}])
        self.assertEqual(rows[1]["kind"],"spacer")
        self.assertEqual(ui.move_selection(rows,0,'j'),2)
        self.assertEqual(ui.move_selection(rows,2,'k'),0)

    def test_label_controls_cannot_escape_the_frame(self):
        self.assertEqual(ui.fit('a\x1bb\x85c',4), 'abc ')

    def test_focused_title_scrolls_with_fixed_status_and_unfocused_stays_still(self):
        row = {'kind': 'pane', 'ref': '%1', 'name': 'ABCDEFGHIJKLMNOQRSTUVWXYZ',
               'agent': 'codex', 'status': 'needs-approval', 'source': 'event'}
        start = ANSI.sub('', ui.row_line(row, True, 32, True, 0))
        later = ANSI.sub('', ui.row_line(row, True, 32, True, 3))
        self.assertNotEqual(start, later)
        self.assertEqual(start[-11:], later[-11:])
        self.assertIn('Approval=', later)
        self.assertEqual(ANSI.sub('', ui.row_line(row, True, 32, False, 0)),
                         ANSI.sub('', ui.row_line(row, True, 32, False, 3)))
        self.assertEqual(ui.scroll_text('short', 10, 5), 'short     ')
        self.assertEqual(ui.scroll_text(row['name'], 8, .8), row['name'][:8])
        end_time = 1 + (ui.cell_width(row['name']) - 8) / 5
        self.assertEqual(ui.scroll_text(row['name'], 8, end_time + .2), row['name'][-8:])

    def test_scrolling_preserves_wide_and_combining_characters(self):
        for text in ('工具開発資料ABCDEFGHIJK', 'école abcdefghijklmnop'):
            for tick in range(100):
                shown = ui.scroll_text(text, 8, tick / 10)
                self.assertEqual(ui.cell_width(shown), 8)
                self.assertFalse(shown.startswith('\u0301'))
