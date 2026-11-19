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
    patch = input.patch ?? input.input ?? patch;
  }
  if (/apply_patch/i.test(tool) && typeof patch === "string") {
    const match = patch.slice(0, 65536).match(/^\*\*\* (?:Update|Add|Delete) File: ([^\r\n]+)$/m);
    if (match) return basename(match[1]);
  }
  return "";
}

// Passive status bridge. Only the session visible in this TUI may update its pane.
export function installBridge(ctx, report) {
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
  };
  const names = new Map(), active = new Map();
  const stops = Object.entries(events).map(([name, hook]) =>
    ctx.data.on(name, event => {
      const sid = event?.data?.sessionID ?? event?.data?.sessionId;
      if (sid && sid === visible()) {
        if (["UserPromptSubmit", "Stop", "Interrupt"].includes(hook)) {
          for (const key of names.keys()) if (key.startsWith(`${sid}:`)) names.delete(key);
          for (const key of active.keys()) if (key.startsWith(`${sid}:`)) active.delete(key);
        }
        report(hook, sid);
      }
    })
  );
  // Tool event schemas publish names separately from calls. Keep only bounded
  // IDs/names/filename basenames, never inputs, outputs or progress content.
  const onTool = (name, phase) => ctx.data.on(name, event => {
    const data = event?.data;
    const sid = data?.sessionID ?? data?.sessionId;
    if (!sid || sid !== visible() || typeof data?.id !== "string") return;
    const key = `${sid}:${data.id}`;
    if (phase === "name") {
      if (typeof data.name === "string") names.set(key, data.name.slice(0, 80));
      if (names.size > 128) names.delete(names.keys().next().value);
      return;
    }
    if (phase === "call") {
      const tool = names.get(key) ?? "";
      const target = toolTarget(tool, data.input);
      active.set(key, {sid, tool, target});
      if (active.size > 128) active.delete(active.keys().next().value);
      if (target) report("PreToolUse", sid, tool, target);
      else report("PreToolUse", sid, tool);
      return;
    }
    names.delete(key);
    if (!active.has(key)) return; // Late completion must not revive a stopped turn.
    const finished = active.get(key);
    active.delete(key);
    const remaining = [...active.values()].filter(item => item.sid === sid).at(-1);
    if (remaining?.target) report("PreToolUse", sid, remaining.tool, remaining.target);
    else if (remaining) report("PreToolUse", sid, remaining.tool);
    else if (finished.target) report("PostToolUse", sid, finished.tool, finished.target);
    else report("PostToolUse", sid);
  });
  for (const [name, phase, version] of [
    ["session.tool.input.started", "name", 1], ["session.tool.called", "call", 1],
    ["session.tool.success", "end", 2], ["session.tool.failed", "end", 2],
  ]) {
    // Both native catalogue spellings are supported by OpenCode v2 builds.
    stops.push(onTool(name, phase), onTool(`${name}.${version}`, phase));
  }
  return () => stops.forEach(stop => stop());
}
