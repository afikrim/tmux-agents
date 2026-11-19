"""Attention items across projects, ordered by urgency without changing read state."""
WAITING = {'needs-approval': 'Needs approval', 'question': 'Question', 'needs-input': 'Need input'}


def entries(sessions, query=''):
    rows = []
    for session in sessions:
        for window in session.get('windows', []):
            for pane in window.get('panes', []):
                status = pane.get('status')
                if not pane.get('agent') or not (status in WAITING or pane.get('unread')):
                    continue
                label = WAITING.get(status, 'Unread completion')
                detail = '%s / %s / %s' % (label, session['name'], pane['agent'])
                name = pane.get('display_name') or pane['agent']
                if all(word in (name+' '+detail).casefold() for word in query.casefold().split()):
                    rows.append(dict(id=pane['id'], name=name, detail=detail,
                        current=False, generation=pane.get('generation',''),
                        priority=0 if status=='needs-approval' else 1 if status in WAITING else 2))
    return sorted(rows, key=lambda r:(r['priority'],r['detail'].casefold(),r['id']))
