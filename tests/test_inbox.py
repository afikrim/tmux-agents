import unittest
from lib import inbox, popups, ui


class InboxTest(unittest.TestCase):
    def sessions(self):
        return [dict(id='$1',name='backend',windows=[dict(id='@1',panes=[
            dict(id='%1',agent='codex',status='idle',unread=True,display_name='Completed task',generation='1:10'),
            dict(id='%2',agent='claude',status='question',unread=False,display_name='Design choice',generation='2:10'),
            dict(id='%3',agent='opencode',status='needs-approval',unread=True,display_name='Approve build',generation='3:10'),
            dict(id='%4',agent='',status='needs-approval',unread=True),
            dict(id='%5',agent='codex',status='idle',unread=False)])])]

    def test_priority_dedup_and_agent_only(self):
        sessions=self.sessions()
        rows=inbox.entries(sessions)
        self.assertEqual([r['id'] for r in rows],['%3','%2','%1'])
        self.assertTrue(sessions[0]['windows'][0]['panes'][0]['unread'])
        self.assertEqual(rows[0]['generation'],'3:10')

    def test_search_projects_provider_and_title(self):
        self.assertEqual([r['id'] for r in inbox.entries(self.sessions(),'backend claude choice')],['%2'])
        self.assertEqual(inbox.entries(self.sessions(),'absent'),[])

    def test_empty_and_small_frames(self):
        import re
        for width,height in ((24,6),(78,28)):
            for rows in ([],inbox.entries(self.sessions())):
                lines,_,hits=popups.frame(rows,'attention','',0,0,width,height)
                self.assertEqual(len(lines),height)
                self.assertTrue(all(ui.cell_width(re.sub(r'\x1b\[[0-9;]*m','',line))==width for line in lines))
                self.assertTrue(all(0<=index<len(rows) for index in hits.values()))
