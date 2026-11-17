// tmux-agents OpenCode v2 TUI plugin.
// Subscribes to session/permission/form lifecycle events (same catalog the
// TUI itself uses) and reports metadata-only status to the tmux-agents event
// log via the bundled hook script. Never sends pane content or message text.
//
// Install: `tmux-agents integrate opencode` (backs up opencode.jsonc first).
// Shape mirrors the proven @opencode/plugin TUI pattern.

import { Plugin } from "@opencode/plugin/tui";
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { installBridge } from "./bridge.mjs";

// Resolve at runtime so moving the installation never retains another machine's path.
const HOOK_SCRIPT = fileURLToPath(new URL("../../hooks/agent-event", import.meta.url));

function report(hookEvent: string, sessionID: string): void {
  try {
    const pane = (globalThis as any)?.process?.env?.TMUX_PANE ?? "";
    if (!pane) return;
    const child = spawn("python3", [HOOK_SCRIPT, "--agent", "opencode"], {
      stdio: ["pipe", "ignore", "ignore"],
    });
    child.on("error", () => {});
    child.stdin.on("error", () => {});
    child.stdin.write(
      JSON.stringify({ hook_event_name: hookEvent, session_id: sessionID, tmux_pane: pane }),
    );
    child.stdin.end();
  } catch {
    // Status reporting must never break the agent session.
  }
}

export default Plugin.define({
  id: "tmux-agents",
  setup(ctx) {
    if (process.env.TMUX_AGENTS_PLUGIN_PROBE) {
      writeFileSync(process.env.TMUX_AGENTS_PLUGIN_PROBE, JSON.stringify({loaded: true, version: ctx.app.version, hookScript: HOOK_SCRIPT}));
    }
    return installBridge(ctx, report);
  },
});
