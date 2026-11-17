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
  // IDs/names, never inputs, outputs, progress text, or message content.
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
      active.set(key, {sid, tool});
      if (active.size > 128) active.delete(active.keys().next().value);
      report("PreToolUse", sid, tool);
      return;
    }
    names.delete(key);
    if (!active.has(key)) return; // Late completion must not revive a stopped turn.
    active.delete(key);
    const remaining = [...active.values()].filter(item => item.sid === sid).at(-1);
    if (remaining) report("PreToolUse", sid, remaining.tool);
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
