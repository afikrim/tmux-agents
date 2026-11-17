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
  const stops = Object.entries(events).map(([name, hook]) =>
    ctx.data.on(name, event => {
      const sid = event?.data?.sessionID ?? event?.data?.sessionId;
      if (sid && sid === visible()) report(hook, sid);
    })
  );
  return () => stops.forEach(stop => stop());
}
