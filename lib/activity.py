"""Bounded activity labels and unread lifecycle metadata, never message content."""
import re
import json
import unicodedata
import shlex
from urllib.parse import urlsplit

ACTIONS = frozenset(("Thinking", "Reading files", "Editing files", "Searching code",
                     "Running command", "Browsing web", "Delegating work", "Compacting context",
                     'Responding','Retrying','Updating tasks','Planning'))
ATTENTION = frozenset(("needs-input", "question", "needs-approval", "done"))


def hook_action(event, payload):
    name = str(event).lower()
    if name == "precompact":
        return "Compacting context"
    if name == 'subagentstart': return 'Delegating work'
    if name == 'responsestarted':return 'Responding'
    if name == 'retrying':return 'Retrying'
    if name in ("userpromptsubmit", "posttooluse", 'posttoolusefailure',
                'postcompact', 'subagentstop','thinkingstarted','premodelrequest','postmodelrequest'):
        return "Thinking"
    if name != "pretooluse":
        return ""
    tool = str(payload.get("tool_name") or payload.get("tool") or "").lower()
    if re.search(r'todo|task_?(?:update|create|list|get)|(?:update|create)_task',tool):return 'Updating tasks'
    if re.search(r'plan_?mode|update_plan|enterplan|exitplan',tool):return 'Planning'
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
    if str(event).lower() not in ('pretooluse','posttooluse','posttoolusefailure'): return ''
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
        patch_text=data.get('patch',data.get('input',data.get('command',patch_text)))
    if 'apply_patch' in tool and isinstance(patch_text,str):
        match=re.search(r'^\*\*\* (?:Update|Add|Delete) File: ([^\r\n]+)$',patch_text[:65536],re.M)
        if match:return safe_target(match.group(1))
    return ''


def safe_tool(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,120}',value) else ''


def safe_detail(value):
    if not isinstance(value,str) or not value.strip() or len(value)>120: return ''
    if any(unicodedata.category(c).startswith('C') for c in value): return ''
    # Descriptions are UI summaries, not a second path/URL/argument channel.
    if re.search(r'://|(?<!\w)(?:[.~]?/|[A-Za-z]:\\)',value): return ''
    return re.sub(r'(?i)\b(token|password|secret|api[_-]?key)\s*[:=]\s*\S+',r'\1=<redacted>',value.strip())


def tool_label(tool):
    tool=safe_tool(tool)
    if not tool:return ''
    parts=[p for p in re.split(r'__|[./:]',tool) if p]
    parts=[p for p in parts if p not in ('mcp','functions','tools')]
    def words(text):
        return re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',text).replace('_',' ').replace('-',' ').capitalize()
    return ' · '.join(words(p) for p in parts[-2:])


def command_label(command):
    if not isinstance(command,str) or len(command)>8192:return ''
    try:args=shlex.split(command)
    except ValueError:return ''
    while args and (args[0] in ('env','rtk','proxy') or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=',args[0])):
        args.pop(0)
    if not args:return ''
    program=safe_target(args[0])
    if not safe_tool(program):return ''
    if program.startswith('python') and len(args)>2 and args[1]=='-m' and safe_tool(args[2]):
        program=args[2]
    elif program in ('git','go','npm','pnpm','yarn','cargo','docker') and len(args)>1 and re.fullmatch(r'[a-z][a-z-]{0,24}',args[1]):
        program+=' '+args[1]
    return safe_detail(program)


def hook_metadata(event,payload):
    name=str(event).lower()
    if name in ('subagentstart','subagentstop'):
        return dict(tool='Agent',phase='start' if name=='subagentstart' else 'end',
                    detail=safe_detail(payload.get('agent_type','')))
    if name not in ('pretooluse','posttooluse','posttoolusefailure'):return {}
    tool=safe_tool(payload.get('tool_name') or payload.get('tool') or '')
    phase={'pretooluse':'start','posttooluse':'end','posttoolusefailure':'failed'}[name]
    action=hook_action('PreToolUse',payload)
    data=payload.get('tool_input',payload.get('input',{}))
    if isinstance(data,str):
        try:data=json.loads(data)
        except (ValueError,RecursionError):data={}
    data=data if isinstance(data,dict) else {}
    detail=''
    native=tool.rsplit('.',1)[-1].lower() if '__' not in tool else ''
    described=native in ('bash','powershell','shell','shell_command','exec_command','command',
                         'terminal','write_stdin','agent','task','spawn_agent','delegate','delegate_task')
    if action in ('Running command','Delegating work') and described:
        detail=safe_detail(payload.get('tool_detail','')) or safe_detail(data.get('description',''))
        if not detail and action=='Running command':
            detail=command_label(data.get('command',data.get('cmd','')))
        if not detail and action=='Delegating work':detail=safe_detail(data.get('subagent_type',''))
    elif action=='Browsing web':
        try:
            host=urlsplit(data.get('url','')).hostname or ''
            detail=host if re.fullmatch(r'[A-Za-z0-9.:-]{1,120}',host) else ''
        except (ValueError,TypeError,AttributeError):pass
        detail=detail or safe_detail(payload.get('tool_detail',''))
    return dict(tool=tool,phase=phase,detail=detail)


def caption(status, action="", source="", target="", tool='', detail='', phase=''):
    if status == "running":
        target=safe_target(target)
        raw_tool=tool
        tool=tool_label(tool); detail=safe_detail(detail)
        if phase=='failed' and tool:return 'Tool failed · '+tool
        verb={'Reading files':'Reading','Editing files':'Editing','Searching code':'Searching'}
        if target and action in verb:return verb[action]+' '+target
        if target and action=='Thinking':return 'Working on '+target
        if phase=='end' and tool:return 'Thinking · '+(detail or tool)
        if detail:
            return {'Running command':'Running · ','Delegating work':'Delegating · ',
                    'Browsing web':'Browsing '}.get(action,'')+detail
        if tool and action=='Thinking':return tool
        if tool and action in verb and raw_tool.lower() not in (
                'read','edit','write','apply_patch','multiedit','glob','grep','find','list_dir','notebookedit'):
            return tool
        if tool and action in ('Running command','Delegating work','Browsing web'):
            return {'Running command':'Running · ','Delegating work':'Delegating · ',
                    'Browsing web':''}[action]+tool
        return action if action in ACTIONS else "Working" + (" · observed" if source in ("inferred",'screen') else "")
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
