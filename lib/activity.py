"""Bounded activity labels and unread lifecycle metadata, never message content."""
import re
import json
import unicodedata

ACTIONS = frozenset(("Thinking", "Reading files", "Editing files", "Searching code",
                     "Running command", "Browsing web", "Delegating work", "Compacting context"))
ATTENTION = frozenset(("needs-input", "question", "needs-approval", "done"))


def hook_action(event, payload):
    name = str(event).lower()
    if name in ("precompact", "postcompact"):
        return "Compacting context"
    if name in ("userpromptsubmit", "posttooluse"):
        return "Thinking"
    if name != "pretooluse":
        return ""
    tool = str(payload.get("tool_name") or payload.get("tool") or "").lower()
    for pattern, action in (
        (r"web|fetch|browse", "Browsing web"),
        (r"edit|write|patch|replace|create_file", "Editing files"),
        (r"search|grep|glob|find|list_dir", "Searching code"),
        (r"read|open_file|view_file", "Reading files"),
        (r"bash|shell|exec|command|terminal|stdin", "Running command"),
        (r"agent|task|delegate", "Delegating work"),
    ):
        if re.search(pattern, tool):
            return action
    return "Thinking"


def safe_target(value):
    """A bounded filename, never a directory, URL, control sequence or content."""
    if not isinstance(value,str) or len(value)>4096 or '://' in value:
        return ''
    if any(unicodedata.category(c).startswith('C') for c in value): return ''
    name=value.replace('\\','/').rstrip('/').rsplit('/',1)[-1].strip()
    if name in ('','.','..') or len(name.encode('utf-8'))>256: return ''
    return name


def hook_target(event,payload):
    """Extract only file/scope basenames from native tool inputs in memory."""
    if str(event).lower() not in ('pretooluse','posttooluse'): return ''
    action=hook_action('PreToolUse',payload)
    if action not in ('Reading files','Editing files','Searching code'): return ''
    forwarded=safe_target(payload.get('tool_target'))
    if forwarded:return forwarded
    tool=str(payload.get('tool_name') or payload.get('tool') or '').lower()
    data=payload.get('tool_input',payload.get('input',{}))
    patch_text=data if isinstance(data,str) else ''
    if isinstance(data,str):
        try:data=json.loads(data)
        except (ValueError,RecursionError):data={}
    if isinstance(data,dict):
        for key in ('file_path','filePath','path','filename','file_name','notebook_path'):
            name=safe_target(data.get(key))
            if name:return name
        patch_text=data.get('patch',data.get('input',patch_text))
    if 'apply_patch' in tool and isinstance(patch_text,str):
        match=re.search(r'^\*\*\* (?:Update|Add|Delete) File: ([^\r\n]+)$',patch_text[:65536],re.M)
        if match:return safe_target(match.group(1))
    return ''


def caption(status, action="", source="", target=""):
    if status == "running":
        target=safe_target(target)
        verb={'Reading files':'Reading','Editing files':'Editing','Searching code':'Searching'}
        if target and action in verb:return verb[action]+' '+target
        if target and action=='Thinking':return 'Working on '+target
        return action if action in ACTIONS else "Working" + (" · observed" if source == "inferred" else "")
    return {"question": "Need input · question", "needs-input": "Need input · decision",
            "needs-approval": "Need approval · review", "done": "Finished · review changes",
            "idle": "(－ω－) zzZ", "unknown": "Waiting for status"}.get(status, "Waiting for status")


def notice(previous, status, generation, event_token="", viewed=False, completed=False):
    """Persist one unread flag per agent generation, acknowledge only real focus.

    A repeated poll/event is not a new notice; a later stop with the same status
    is. Running again retains an unseen notice until the user visits the pane.
    """
    same = previous.get("generation") == generation
    unread = bool(previous.get("unread")) if same else False
    key = (status + ":" + str(event_token)) if status in ATTENTION else ""
    if status == "idle" and completed:
        key = "completed:" + str(event_token)
    if key and not event_token and same and previous.get("status") == status:
        key = previous.get("notice_key") or key
    if key and (not same or key != previous.get("notice_key")):
        unread = True
    if viewed:
        unread = False
    return {"generation": generation, "notice_key": key, "unread": unread}
