"""Status classification with honest inferred/unknown fallback (v2).

Priority per pane:
  1. process exit SUPSEDES everything: no agent process -> done/idle.
  2. explicit lifecycle event, only if fresh, timestamp-sane, socket-matched
     and bound to the CURRENT agent identity (pid + start time). Blank
     bindings are rejected; stale events never leak across agents.
  3. strict screen widgets -> needs-approval / question (source 'screen').
  4. observed screen activity between polls -> running (source 'inferred').
  5. otherwise -> unknown. A live process alone NEVER proves working.

Done detection uses the previous snapshot: a pane whose agent vanished after
being in an active state is done; a pane that never had an agent is idle.
"""
import time

EVENT_TTL_RUNNING = 600
# Attention that is still bound to a live agent persists: a needs-input at
# 03:00 is still needs-input at 04:30 unless the agent moved on. Without a
# lifecycle monitor the daemon can only decay this to unknown on new
# evidence; see README "status honesty".
EVENT_TTL_ATTENTION = 7200
EVENT_TTL_DONE = 600
CLOCK_SKEW = 60
SCREEN_TAIL_LINES = 8

ACTIVE_STATES = ("running", "needs-input", "needs-approval", "question")

import re

_YN_RE = re.compile(r"\[\s*y\s*/\s*n\s*\]|\(\s*y\s*/\s*n\s*\)|\byes\s*/\s*no\b", re.I)
_OPTION_LINE_RE = re.compile(r"^\s*(?:[>❯»›]\s*)?(?:\(?[1-9][).:»\]]\s+\S|\[\s*[x ]\s*\])")
_APPROVE_VERB_RE = re.compile(
    r"\b(approv\w*|allow(\s+execution|\s+command)?|confirm(\s+(execution|action))?|permission|proceed\?)", re.I)
_SELECTOR_RE = re.compile(r"[❯›▶►]")


def _tail_lines(text, n=SCREEN_TAIL_LINES):
    lines = [l for l in (text or "").splitlines() if l.strip() != ""]
    return lines[-n:]


def screen_attention(text):
    """Classify visible tail. Returns 'needs-approval'|'question'|None.

    Strong cues only:
      approval: approval verb AND a decision widget ([y/n] or >=2 options).
      question: '?' AND >=2 option lines AND a selector glyph, or [y/n]
        without approval verbs.
    Ordinary numbered prose never fires (requires '?' / widget cues).
    """
    tail = _tail_lines(text)
    if not tail:
        return None
    joined = "\n".join(tail)
    has_yn = bool(_YN_RE.search(joined))
    options = [l for l in tail if _OPTION_LINE_RE.match(l)]
    has_verb = bool(_APPROVE_VERB_RE.search(joined))
    has_q = "?" in joined
    has_sel = bool(re.search(r"^\s*[❯›▶►>]\s*[1-9][).]\s", joined, re.M))
    if has_verb and (has_yn or (len(options) >= 2 and has_sel)):
        return "needs-approval"
    if has_yn and not has_verb:
        return "question"
    if has_q and len(options) >= 2 and has_sel:
        return "question"
    return None


def event_usable(ev, now, kind=None):
    """Timestamp sanity + freshness. Rejects future/negative/stale.

    Running/done signals expire fast; still-bound attention (needs-input /
    needs-approval / question) persists while the agent is alive to avoid
    dropping a long-pending approval. Stale cross-agent use is prevented by
    the pid+start binding checked by the caller, not by the clock.
    """
    if not ev:
        return False
    ts = ev.get("ts", 0)
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return False
    if ts <= 0 or ts > now + CLOCK_SKEW:
        return False
    kind = kind if kind is not None else (ev.get("kind") if ev else None)
    ttl = EVENT_TTL_ATTENTION if kind in ("idle", "needs-input", "needs-approval",
                                          "question") else EVENT_TTL_RUNNING
    if now - ts > ttl:
        return False
    return True


def unknown_reason(event, bound_ok, now=None):
    """Explain missing evidence without inspecting or retaining terminal content."""
    now=time.time() if now is None else now
    if not event:
        return 'No usable lifecycle event and no recognized screen/activity signal'
    if not bound_ok:
        return 'Last lifecycle event is not bound to this agent process'
    if not event_usable(event,now):
        return 'Last lifecycle event expired or has an invalid timestamp'
    return 'No current recognized lifecycle, screen or activity signal'


def classify(agent, event, screen_text, alive, prev, screen_changed,
             bound_ok, now=None):
    """Return (status, source).

    agent: agent name or ''. event: dict or None. alive: agent proc alive.
    prev: previous status string or ''. screen_changed: tail hash moved.
    bound_ok: event bound to current agent identity + socket.
    source: 'event'|'process'|'screen'|'inferred'|'' for unknown.
    """
    now = now if now is not None else time.time()
    kind = event.get("kind") if event else None
    fresh = event_usable(event, now, kind) and bool(bound_ok)

    # 1. Exit supersedes every active signal. A pane whose agent vanished
    # stays done while the (dead) pane persists; vanished panes are dropped
    # from state by the caller.
    if not alive:
        if agent or (event and event.get("had_agent")) or \
                (prev in ACTIVE_STATES) or prev == "done":
            return "done", "process"
        return "idle", "process"

    # 2. Explicit bound event.
    if fresh and kind in ("needs-input", "needs-approval", "question"):
        return kind, "event"
    if fresh and kind == "idle":
        return "idle", "event"

    # 3. Strict screen widgets.
    attn = screen_attention(screen_text or "")
    if attn:
        return attn, "screen"

    # A passive composer can remain painted while a native tool is executing.
    # Do not let that idle footer hide a current, identity-bound running event.
    if fresh and kind == "running":
        return "running", "event"

    # Existing interactive sessions may predate installed hooks. Recognize
    # their current composer/footer, never scrollback or arbitrary prose.
    if agent in ("codex", "claude"):
        tail = "\n".join(_tail_lines(screen_text or "", 10))
        if re.search(r"esc to interrupt|esc to cancel|ctrl.c to interrupt", tail, re.I):
            return "running", "screen"
        if re.search(r"^\s*[›❯](?:\s|$)", tail, re.M):
            return "idle", "screen"

    # 5. Observed activity.
    if screen_changed:
        return "running", "inferred"

    # 6. Honest unknown. (prev done + new agent with no signal yet -> running?
    #    No: a fresh agent with zero evidence is still unknown.)
    if prev == "done" and not agent:
        return "idle", "process"
    return "unknown", ""
