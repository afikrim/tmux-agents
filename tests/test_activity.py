import tempfile
import unittest
from pathlib import Path

from lib import activity, store, ui


class ActivityTest(unittest.TestCase):
    def test_captions_are_safe_metadata_and_attention_overrides_action(self):
        for tool, expected in (("Bash","Running command"), ("Read","Reading files"),
                               ("apply_patch","Editing files"), ("Grep","Searching code"),
                               ("web_fetch","Browsing web")):
            action = activity.hook_action("PreToolUse", {"tool_name":tool,"tool_input":{"command":"secret"}})
            self.assertEqual(action,expected)
            self.assertEqual(activity.caption("running",action),expected)
            self.assertIn("Need input",activity.caption("question",action))
        self.assertIn("Need approval",activity.caption("needs-approval"))
        self.assertIn("zzZ",activity.caption("idle"))
        self.assertEqual(activity.caption("running","private command"),"Working")
        with tempfile.TemporaryDirectory() as directory:
            paths={"dir":directory,"events":str(Path(directory)/"events.jsonl")}
            store.append_event(paths,"%1","running",action="private command")
            self.assertNotIn("private command",Path(paths['events']).read_text())

    def test_unread_acknowledgement_new_turns_and_generation_reset(self):
        def step(previous,status,token="",viewed=False,generation="pid:1"):
            return dict(status=status,**activity.notice(previous,status,generation,token,viewed))
        state=step({},"running")
        self.assertFalse(state['unread'])
        state=step(state,"needs-input",100)
        self.assertTrue(state['unread'])
        state=step(state,"needs-input",100,True)
        self.assertFalse(state['unread'])
        state=step(state,"needs-input",100)
        self.assertFalse(state['unread'])
        state=step(state,"needs-input")  # Native event expires; not a new notice.
        self.assertFalse(state['unread'])
        state=step(state,"needs-input",101)
        self.assertTrue(state['unread'])
        state=step(state,"running")
        self.assertTrue(state['unread'])
        state=step(state,"running",generation="pid:2")
        self.assertFalse(state['unread'])
        self.assertTrue(step(state,"done",generation="pid:2")['unread'])

    def test_project_counts_count_windows_and_unread_targets_hidden_split(self):
        session={"id":"$1","name":"project","windows":[
            {"id":"@1","panes":[{"id":"%1","agent":"codex","status":"running","current":True},
                                    {"id":"%2","agent":"claude","status":"question","unread":True}]},
            {"id":"@2","panes":[{"id":"%3","status":"idle","unread":True}]},
            {"id":"@3","panes":[{"id":"%4","status":"unknown"}]}]}
        summary=ui.project_summary(session)
        self.assertEqual((summary['running'],summary['idle'],summary['unread']),(1,1,2))
        self.assertEqual(summary['unread_target']['ref'],'%2')
        rows=ui.build_rows([session])
        lines,_,_,_,hits=ui.frame(rows,[session],0,0,32,24,return_hits=True)
        y=next(iter(hits.unread))
        start,end=hits.unread[y]
        self.assertEqual(ui.click_target(rows,hits,start,y)['ref'],'%2')
        self.assertEqual(ui.click_target(rows,hits,end,y)['win_id'],'@1')
        self.assertEqual(ui.click_target(rows,hits,1,y)['ref'],'$1')
        self.assertIsNone(ui.click_target(rows,hits,1,y+1))  # Spacing is inert.
        rows[0]['unread_target']=None
        self.assertIsNone(ui.click_target(rows,hits,start,y))

    def test_finished_turn_is_idle_but_completion_still_becomes_unread(self):
        initial=activity.notice({},'idle','agent:1')
        self.assertFalse(initial['unread'])
        completed=activity.notice(initial,'idle','agent:1',100,completed=True)
        self.assertTrue(completed['unread'])
        seen=activity.notice(completed,'idle','agent:1',100,viewed=True,completed=True)
        self.assertFalse(seen['unread'])
        self.assertFalse(activity.notice(seen,'idle','agent:1',100,completed=True)['unread'])
        self.assertTrue(activity.notice(seen,'idle','agent:1',101,completed=True)['unread'])
