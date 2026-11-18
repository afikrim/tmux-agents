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
            for background in ('bg','panel','selected','hover'):
                a,b=sorted((lum(theme.PALETTE[foreground]),lum(theme.PALETTE[background])))
                self.assertGreaterEqual((b+.05)/(a+.05),4.5,(foreground,background))

    def test_project_and_agent_sections_have_separate_hit_regions_and_no_hints(self):
        sessions = [{"id":"$1", "name":"one", "attached":True,"windows":[
            {"id":"@1","name":"work","panes":[{"id":"%1","agent":"codex","status":"running"}]}]},
            {"id":"$2","name":"two","windows":[]}]
        rows = ui.build_rows(sessions)
        lines, offsets, header, body, hits = ui.frame(rows,sessions,3,0,32,20,return_hits=True)
        plain = [ANSI.sub("", line) for line in lines]
        self.assertIn("Projects", plain[0])
        divider = next(i for i,line in enumerate(plain,1) if "──" in line)
        self.assertTrue(all(y < divider for y,i in hits.items() if rows[i]["kind"] == "session"))
        self.assertTrue(all(y > divider for y,i in hits.items() if rows[i]["kind"] == "pane"))
        self.assertNotIn("enter open", "\n".join(plain))
        self.assertEqual(ui.move_selection(rows,1,'j'),3)
        self.assertEqual(ui.move_selection(rows,3,'k'),1)

    def test_label_controls_cannot_escape_the_frame(self):
        self.assertEqual(ui.fit('a\x1bb\x85c',4), 'abc ')

    def test_agents_reserve_bottom_third_and_scroll_to_selected_window(self):
        sessions = [{"id":"$1", "name":"work", "attached":True, "windows":[
            {"id":"@%d" % index, "name":"task", "panes":[
                {"id":"%%%d" % index, "agent":"codex", "status":"running"}]}
            for index in range(25)]}]
        rows = ui.build_rows(sessions)
        last = len(rows) - 1
        lines, offsets, _, _, hits = ui.frame(rows,sessions,last,0,32,20,return_hits=True)
        agent_hits = {y:i for y,i in hits.items() if rows[i]["kind"] == "pane"}
        divider = next(y for y,line in enumerate(lines,1) if '──' in ANSI.sub('',line))
        self.assertEqual(divider, 15)
        self.assertEqual(sorted(agent_hits), [17,18])
        self.assertEqual(agent_hits[18], last)
        self.assertGreater(offsets['agents'], 0)
        sessions[0]['windows'] = sessions[0]['windows'][:1]
        rows = ui.build_rows(sessions)
        lines, _, _, _, hits = ui.frame(rows,sessions,2,0,32,20,return_hits=True)
        self.assertEqual([y for y,i in hits.items() if rows[i]['kind'] == 'pane'], [17,18])
        self.assertIn('Agents / work', ANSI.sub('',lines[15]))
        one_agent = sessions[0]['windows']
        for height in (12,20,30,60):
            for windows in (one_agent, []):
                sessions[0]['windows'] = windows
                rows = ui.build_rows(sessions)
                lines, _, _, _, hits = ui.frame(rows,sessions,0,0,32,height,return_hits=True)
                divider = height-height//3+1
                self.assertIn('──', ANSI.sub('',lines[divider-1]))
                self.assertIn('Agents / work', ANSI.sub('',lines[divider]))
                self.assertEqual(len(lines), height)
                if not windows:
                    self.assertIn('No agents', ANSI.sub('',lines[divider+1]))
                for y,line in enumerate(lines,1):
                    if not ANSI.sub('',line).strip():
                        self.assertNotIn(y, hits)

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

    def test_hover_scrolls_without_replacing_active_or_keyboard_selection(self):
        row = {'kind':'pane', 'ref':'%2', 'name':'ABCDEFGHIJKLMNOPQRSTUVWXYZ extra long',
               'agent':'codex', 'status':'running'}
        start = ui.row_line(row, False, 32, False, hovered=True)
        later = ui.row_line(row, False, 32, False, hovered=True, hover_elapsed=3)
        self.assertIn(theme.bg('hover'), start)
        self.assertNotEqual(ANSI.sub('', start), ANSI.sub('', later))
        self.assertEqual(ANSI.sub('', start)[-10:], ANSI.sub('', later)[-10:])
        self.assertNotIn('>', ANSI.sub('', later))
        row['current'] = True
        active = ui.row_line(row, True, 32, False)
        self.assertIn('>', ANSI.sub('', active))
        self.assertIn(theme.bg('panel'), active)
        self.assertIn(theme.bg('selected'), ui.row_line(row, True, 32, True, hovered=True))

    def test_hover_hit_testing_rejects_drag_release_wheel_and_non_rows(self):
        rows = [{'kind':'session','ref':'$1'}, {'kind':'pane','ref':'%1'}]
        hits = {2:0, 20:1}
        for button in (35, 39, 43, 51):
            self.assertEqual(ui.hover_target(rows,hits,32,('mouse',button,5,20,False)), '%1')
        for key in (('mouse',32,5,20,False), ('mouse',35,5,20,True),
                    ('mouse',64,5,20,False), ('mouse',35,0,20,False),
                    ('mouse',35,33,20,False), ('mouse',35,5,1,False),
                    ('mouse',35,5,19,False), 'j'):
            self.assertIsNone(ui.hover_target(rows,hits,32,key))
