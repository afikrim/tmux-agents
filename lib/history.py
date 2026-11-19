"""Private, bounded conversation metadata. Provider transcripts stay with providers."""
import fcntl
import hashlib
import json
import os
import re
import time

from . import store, ui
from .tmuxutil import state_root

LIMIT = 2000
PROVIDERS = ('codex', 'claude', 'opencode')


def conversation_id(payload):
    value = payload.get('session_id') or payload.get('thread_id') or ''
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}', value) else ''


def key(provider, ident):
    return hashlib.sha256((provider+':'+ident).encode()).hexdigest()[:24]


def path():
    return os.path.join(state_root(), 'history.json')


def read():
    try:
        with open(path()) as f:
            data = json.load(f)
        return [r for r in data if isinstance(r, dict) and r.get('provider') in PROVIDERS
                and conversation_id({'session_id':r.get('conversation')})
                and r.get('id') == key(r['provider'], r['conversation'])]
    except (OSError, ValueError, TypeError):
        return []


def remember(provider, ident, *, title, directory, project, pane, socket, pid, start, refresh=False):
    if provider not in PROVIDERS or not conversation_id({'session_id':ident}):
        return
    store.ensure_dir(state_root())
    lock = os.open(path()+'.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = read()
        rid = key(provider, ident)
        old = next((r for r in records if r['id'] == rid), {})
        if refresh and (not old or old.get('pid') != pid or old.get('start') != start
                        or old.get('socket') != socket or old.get('pane') != pane):
            return
        record = dict(id=rid, provider=provider, conversation=ident,
                      title=ui.san(title, 0).strip()[:120] or provider,
                      directory=directory, project=ui.san(project, 0)[:120],
                      pane=pane, socket=socket, pid=pid, start=start,
                      updated=old.get('updated', time.time()) if refresh else time.time())
        if record['title'] == provider and old.get('title'):
            record['title'] = old['title']
        if record == old:
            return
        records = [r for r in records if r['id'] != rid]+[record]
        records.sort(key=lambda r:r.get('updated', 0), reverse=True)
        store.atomic_write(path(), records[:LIMIT])
    finally:
        os.close(lock)


def resume_args(record):
    provider, ident = record['provider'], record['conversation']
    if provider not in PROVIDERS or not conversation_id({'session_id':ident}):
        raise ValueError('Invalid conversation metadata')
    return {'codex':['--no-daemon', 'resume', ident],
            'claude':['--resume', ident], 'opencode':['--session', ident]}[provider]


def entries(query=''):
    words = query.casefold().split()
    rows = []
    for record in sorted(read(), key=lambda r:r.get('updated', 0), reverse=True):
        seen = time.strftime('%b %d %H:%M', time.localtime(record.get('updated', 0)))
        detail = '%s / %s / %s / %s' % (record['provider'], record.get('project', ''), seen, record.get('directory', ''))
        title = record.get('title') or record['provider']
        if all(word in (title+' '+detail).casefold() for word in words):
            rows.append(dict(id=record['id'], name=title, detail=detail, current=False))
    return rows
