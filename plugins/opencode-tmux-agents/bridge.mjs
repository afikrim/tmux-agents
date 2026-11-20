// Extract a filename locally. Raw tool inputs never leave this process.
export function toolTarget(tool, input) {
  if (!/read|edit|write|patch|replace|create_file|search|grep|glob|find|list_dir/i.test(tool)) return "";
  const basename = value => {
    if (typeof value !== "string" || value.length > 4096 || value.includes("://") || /\p{C}/u.test(value)) return "";
    const name = value.replaceAll("\\", "/").replace(/\/+$/, "").split("/").at(-1).trim();
    return !name || name === "." || name === ".." || Buffer.byteLength(name, "utf8") > 256 ? "" : name;
  };
  let patch = typeof input === "string" ? input : "";
  if (typeof input === "string") {
    try { input = JSON.parse(input); } catch { input = null; }
  }
  if (input && typeof input === "object" && !Array.isArray(input)) {
    for (const field of ["file_path", "filePath", "path", "filename", "file_name", "notebook_path"]) {
      const name = basename(input[field]);
      if (name) return name;
    }
    patch = input.patch ?? input.input ?? input.command ?? patch;
  }
  if (/apply_patch/i.test(tool) && typeof patch === "string") {
    const match = patch.slice(0, 65536).match(/^\*\*\* (?:Update|Add|Delete) File: ([^\r\n]+)$/m);
    if (match) return basename(match[1]);
  }
  return "";
}

function safeDetail(value) {
  if (typeof value !== 'string' || !value.trim() || value.length>120 || /\p{C}/u.test(value)) return '';
  if (/:\/\/|(^|\W)([.~]?\/|[A-Za-z]:\\)/.test(value)) return '';
  return value.trim().replace(/\b(token|password|secret|api[_-]?key)\s*[:=]\s*\S+/gi,'$1=<redacted>');
}

export function toolDetail(tool,input) {
  if (typeof tool!=='string')return '';
  if (typeof input==='string') {try {input=JSON.parse(input);} catch {return '';}}
  if (!input || typeof input!=='object' || Array.isArray(input)) return '';
  const native=tool.includes('__') ? '' : tool.split('.').at(-1).toLowerCase();
  if (['bash','powershell','shell','shell_command','exec_command','command','terminal','write_stdin',
       'agent','task','spawn_agent','delegate','delegate_task'].includes(native)) return safeDetail(input.description);
  if (/web|fetch|browse/i.test(tool) && typeof input.url==='string') {
    try {return new URL(input.url).hostname.slice(0,120);} catch {}
  }
  return '';
}

// Passive status bridge. Only the session visible in this TUI may update its pane.
export function installBridge(ctx, report) {
  const waiting=new Map(),sink=report;
  report=(hook,sid,...args)=>{
    if (waiting.has(sid) && !['PermissionRequest','FormCreated','Stop','Interrupt'].includes(hook)) return;
    sink(hook,sid,...args);
  };
  const listen=(name,handler)=>{
    try {const stop=ctx.data.on(name,handler); return typeof stop==='function' ? stop : ()=>{};}
    catch {return ()=>{};} // one unsupported compatibility alias must not block the bridge
  };
  const visible = () => {
    const route = ctx.ui.router.current();
    return route?.type === "session" ? route.sessionID : undefined;
  };
  const events = {
    "session.execution.started": "UserPromptSubmit",
    "session.execution.succeeded": "Stop",
    "session.execution.failed": "Stop",
    "session.execution.interrupted": "Interrupt",
    "session.idle": "Stop",
    "permission.asked": "PermissionRequest",
    "permission.replied": "PostToolUse",
    "form.created": "FormCreated",
    "form.replied": "PostToolUse",
    "form.cancelled": "PostToolUse",
    'session.next.compaction.started': 'PreCompact',
    'session.next.compaction.ended': 'PostCompact',
    'session.next.compaction.started.1': 'PreCompact',
    'session.next.compaction.ended.1': 'PostCompact',
    'session.next.compaction.ended.2': 'PostCompact',
    'session.compacted':'PostCompact',
    'question.asked':'FormCreated',
    'question.replied':'PostToolUse',
    'question.rejected':'PostToolUse',
    'session.next.step.started':'ThinkingStarted',
    'session.next.step.started.1':'ThinkingStarted',
    'session.next.reasoning.started':'ThinkingStarted',
    'session.next.reasoning.started.1':'ThinkingStarted',
    'session.next.text.started':'ResponseStarted',
    'session.next.text.started.1':'ResponseStarted',
  };
  const names = new Map(), active = new Map();
  const stops = Object.entries(events).map(([name, hook]) =>
    listen(name, event => {
      const data=event?.data ?? event?.properties ?? event?.syncEvent?.data;
      const sid = data?.sessionID ?? data?.sessionId;
      if (sid && sid === visible()) {
        if (hook==='PermissionRequest' || hook==='FormCreated') {
          waiting.set(sid,hook);
          if (waiting.size>128)waiting.delete(waiting.keys().next().value);
        }
        if (['permission.replied','form.replied','form.cancelled','question.replied','question.rejected'].includes(name)) waiting.delete(sid);
        if (name==='session.idle' && waiting.has(sid))return;
        if (["UserPromptSubmit", "Stop", "Interrupt"].includes(hook)) {
          waiting.delete(sid);
          for (const key of names.keys()) if (key.startsWith(`${sid}:`)) names.delete(key);
          for (const key of active.keys()) if (key.startsWith(`${sid}:`)) active.delete(key);
        }
        report(hook, sid);
      }
    })
  );
  stops.push(listen('session.status',event=>{
    const data=event?.data ?? event?.properties;
    const sid=data?.sessionID;
    if (!sid || sid!==visible()) return;
    if (data.status?.type==='idle') {
      if (waiting.has(sid))return;
      for (const key of names.keys()) if (key.startsWith(`${sid}:`)) names.delete(key);
      for (const key of active.keys()) if (key.startsWith(`${sid}:`)) active.delete(key);
      report('Stop',sid);
    } else if (data.status?.type==='retry') report('Retrying',sid);
    else if (data.status?.type==='busy' && ![...active.values()].some(item=>item.sid===sid)) report('ThinkingStarted',sid);
  }));
  // Tool event schemas publish names separately from calls. Keep only bounded
  // IDs/names/filename basenames, never inputs, outputs or progress content.
  const onTool = (name, phase) => listen(name, event => {
    const data = event?.data ?? event?.properties ?? event?.syncEvent?.data;
    const sid = data?.sessionID ?? data?.sessionId;
    const id=data?.callID ?? data?.id;
    if (!sid || sid !== visible() || typeof id !== "string") return;
    const key = `${sid}:${id}`;
    if (phase === "name") {
      if (typeof data.name === "string") names.set(key, data.name.slice(0, 80));
      if (names.size > 128) names.delete(names.keys().next().value);
      return;
    }
    if (phase === "call") {
      const rawTool=names.get(key) ?? data.tool ?? data.name ?? "";
      const tool=typeof rawTool==='string' ? rawTool.slice(0,120) : '';
      const target = toolTarget(tool, data.input);
      const detail = toolDetail(tool,data.input);
      active.set(key, {sid, tool, target, detail});
      if (active.size > 128) active.delete(active.keys().next().value);
      if (detail) report("PreToolUse",sid,tool,target,detail);
      else if (target) report("PreToolUse", sid, tool, target);
      else report("PreToolUse", sid, tool);
      return;
    }
    names.delete(key);
    if (!active.has(key)) return; // Late completion must not revive a stopped turn.
    const finished = active.get(key);
    active.delete(key);
    const remaining = [...active.values()].filter(item => item.sid === sid).at(-1);
    if (remaining?.detail) report('PreToolUse',sid,remaining.tool,remaining.target,remaining.detail);
    else if (remaining?.target) report("PreToolUse", sid, remaining.tool, remaining.target);
    else if (remaining) report("PreToolUse", sid, remaining.tool);
    else if (phase==='failed') report('PostToolUseFailure',sid,finished.tool,finished.target,finished.detail);
    else if (finished.detail) report('PostToolUse',sid,finished.tool,finished.target,finished.detail);
    else if (finished.target) report("PostToolUse", sid, finished.tool, finished.target);
    else if (finished.tool) report('PostToolUse',sid,finished.tool);
    else report("PostToolUse", sid);
  });
  for (const [name, phase, version] of [
    ["session.tool.input.started", "name", 1], ["session.tool.called", "call", 1],
    ["session.tool.success", "end", 2], ["session.tool.failed", "failed", 2],
  ]) {
    // Both native catalogue spellings are supported by OpenCode v2 builds.
    const next=name.replace('session.tool.','session.next.tool.');
    for (const prefix of [name,next]) {
      for (const suffix of new Set(['',`.${version}`,'.1'])) stops.push(onTool(prefix+suffix,phase));
    }
  }
  return () => stops.forEach(stop => stop());
}
