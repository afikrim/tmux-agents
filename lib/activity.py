"""Bounded activity labels and unread lifecycle metadata, never message content."""
import re

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


def caption(status, action="", source=""):
    if status == "running":
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
