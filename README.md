# tmux-agents

An always-visible, Corgi-inspired workspace sidebar inside tmux. It lists
projects (tmux sessions) above individual agent panes, with live status for Codex, Claude Code
and OpenCode. Your agents keep their own interactive terminals. A compact
window-tab strip sits above the main content, aligned after the sidebar.
It includes every window, so shell/editor windows remain easy to reach.
The session label and clock stay hidden. Uninstall restores the previous bar.

## Controls

| Control | Action |
| --- | --- |
| Alt-e | Focus the sidebar in this window |
| Ctrl-b, s | Search and switch projects in a popup |
| Ctrl-b, S | Hide/show the sidebar in this window |
| Ctrl-b, ? | Open the shortcut reference popup |
| Ctrl-b, H / h in sidebar | Search conversation history; open or resume |
| Ctrl-b, I / i in sidebar | Open the attention inbox across projects |
| Ctrl-b, N | Visit the next agent needing attention |
| Ctrl-b, a | Open the new-agent menu |
| Ctrl-b, v | Toggle the right-hand diff pane |
| Click a top tab / Ctrl-b, n or p | Switch windows, including shells and editors |
| d (in sidebar) | Toggle diff for the selected terminal |
| Click a row / Enter | Switch to the project or exact agent pane |
| Hover a row | Highlight it and scroll its long title, keeping terminal focus |
| j/k, arrows, mouse wheel | Move through projects and individual agents |
| g/G, Page Up/Down | Navigate a long tree |
| x (in sidebar) | Start an agent in the selected workspace |
| r / Shift-r (in sidebar) | Rename selection / its window |
| ? (in sidebar or diff) | Open the shortcut popup |
| q / Escape | Return focus to the previous terminal |

The sidebar has two sections: **Projects** on top and **Agents** below.
Each project has a title, a counts line, and a blank row separating it from the
next project. Counts use **▶** for running agents and **●** for idle/waiting
agents, followed by **· N unread** only when there are unread agents.
Unknown status is excluded from running/idle totals. Click the
underlined **unread** count to open the first unread terminal in that project,
including an unread split within a window. Zero unread is hidden.

Unread means a new completion, question, input request, or approval you have
not visited. Visiting the real terminal in an attached client marks it read;
hovering, previewing a project, and detached active panes do not. The same
notice stays read across polls; a later turn can become unread again. Tracking
starts when this version runs and does not reconstruct conversation history.

### Attention inbox

**Ctrl-b, I** (or **i** in the sidebar) lists live agents needing approval,
asking a question or carrying an unread result, across all projects. Approvals
come first, then questions/input requests, then other unread results. Search by
title, provider, project or attention type. Enter visits the exact agent pane,
including splits. **Ctrl-b, N** cycles through agents needing attention without
opening the popup. Opening/searching the inbox does not mark items read; the
normal real-terminal visit does. Resolved requests disappear on refresh.
An exited or replaced agent is rejected rather than opening an unrelated pane.

### Desktop notifications (opt-in, off by default)

No desktop alerts are sent unless you opt in per tmux server. When enabled,
the daemon shows a generic alert for a newly unread native attention event
(explicit question, typed input decision, or approval) and for an unseen
completion reported by a native hook while the agent is still alive. Plain
idle, unknown, process exits, screen-only prompts, manual events and repeated
polls never alert. Enabling and daemon startup skip existing backlog.
Displaying an alert does not mark it read; opening its real terminal does.

```sh
tmux-agents notify --enable            # opt in for this server
tmux-agents notify --status            # show opt-in state and backend
tmux-agents notify --list --json       # recent alert routing IDs
tmux-agents notify --open --id ID       # use an ID from --list
tmux-agents notify --disable           # opt out again
```

Linux uses `notify-send` with an Open action where the desktop supports it;
macOS uses `terminal-notifier` with a click callback to the exact agent pane.
Install the native backend first (Linux: your distribution's `libnotify`
package providing `notify-send` with action support; macOS:
`brew install terminal-notifier`). Your desktop must allow notifications.
No desktop packages are installed automatically. Headless
machines and missing backends degrade silently: the sidebar and daemon keep
working. Nothing is spawned while disabled; at most three notifier processes
run at once, with a two-minute action timeout. If all slots are busy, pending
events are reconsidered while still current. Alert text is generic wording;
no titles, prompts, tool output, or directories are included. Opening uses
the same exact-generation check as the inbox, the same socket and state
root, and a saved attached-client identity. The most recently active client
in the agent's project is preferred, otherwise the most recently active client
on that server. Clicking never falls back to another client. No attached client
means no alert. Stale panes, replaced agents, detached or replaced clients and
other sockets fail safely. Opening selects the tmux pane; you may still need to
bring your terminal app to the foreground. Linux actions depend on the desktop
notification service. `--list`/`--open` also work while a saved route is retained
(up to 50 recent routes). Disabling stops pending workers and rejects callbacks.

### Conversation history

**Ctrl-b, H** (or **h** in the sidebar) opens searchable conversation history.
Search by title, project, directory or provider, then press Enter. A verified
live conversation opens its exact pane; a closed one resumes in a new window
in its recorded project and directory. A removed project session is recreated.
Escape closes the popup. A missing directory or agent executable produces an
error before a window is created.

History starts collecting when native hooks report a conversation ID. Existing
agents appear after their next supported hook event; there is no transcript
scan or import. Titles and nicknames update while the agent runs. Conversation
messages, prompts, tool output and credentials are never copied into the index.
The provider remains responsible for retaining its native conversation data.

The private index is `history.json` under the tmux-agents state root, shared
across this machine's tmux sockets and retained across server restarts. It keeps
the 2,000 most recently observed conversations and survives uninstall. The index
is local metadata, not a backup or a way to transfer provider chats to another
machine. The CLI equivalents are `tmux-agents history --list` and
`tmux-agents resume RECORD_ID` (the opaque record ID comes from the list).

Each agent entry has an activity caption below its title. Native hooks supply
bounded labels such as **Reading files**, **Editing files**, **Running command**,
or **Thinking**; **Need input** and **Need approval** take priority. Idle terminals
show `(－ω－) zzZ`. Missing tool metadata falls back to **Working** or an honest
unknown status. No prompts, command arguments, tool output, or chat text are
stored for these captions. Existing OpenCode sessions need a restart to load
the expanded tool-event bridge; their lifecycle status captions still work.

Moving through projects previews only that project's agents; Enter or clicking
switches to the project and focuses its main terminal, even if its sidebar was
previously focused. The active-project highlight stays on the current session;
hover highlights other projects temporarily. Each pane hosting Codex, Claude, or OpenCode gets its own
entry, including multiple agents split inside one window. Plain shell/editor
panes are omitted. Agent entries have a blank row between them; click or Enter
selects the exact agent pane. The top tabs include all windows, including
ordinary shells/editors. The tab strip leaves blank space above the sidebar
and optional diff pane, using their actual widths, and follows hiding, resizing,
and zooming. With the sidebar hidden it starts at the left edge. Long window
lists scroll around the active tab using tmux's native overflow handling.
Tab labels follow each window's focused terminal: the conversation title for
agents (or its nickname), the program name for apps, and the current directory
name for shells. Focusing the sidebar or diff keeps the last terminal's label.
Labels update with the status poll; the underlying tmux window name is preserved.
Tabs occupy 24 columns each. Long titles scroll in the focused tab with a pause
at each end; other tabs remain still. Focusing a side panel keeps the same title.
The agents section reserves the bottom third of the sidebar height, including
its heading and divider, even when there are few or no agents. Tiny terminals
keep enough room for one agent. Longer lists scroll within that space;
projects use the space above. Both sections scroll
independently. The selector follows the active window after mouse navigation;
keyboard browsing stays independent while the sidebar is focused.

Mouse hover uses standard SGR all-motion reporting, supported by Kitty and
other compatible terminals through tmux, with no Kitty configuration needed.
Hovering keeps your current project, window and keyboard selection unchanged.
The subtle hover background is separate from the active-window arrow. Hover
clears on blank sidebar space, sidebar input, resize or window changes.
Tmux does not reliably forward a pane-leave event: the last hover can remain
when the pointer leaves the sidebar until one of those events occurs.

Permanent shortcut hints are hidden from the navigation and diff panes. Press **?**
inside either panel, or **Ctrl-b, ?** anywhere, for the searchable shortcut
popup. **Ctrl-b, s** opens the matching project picker: type to search, use
arrows to select, Enter to switch, and Escape to cancel.

The launcher opens a **new window** in the selected workspace's directory.
In the new-agent menu (**Ctrl-b a** or **x**), choose **New project + agent**
with **n** to create a new tmux session and its first agent together. Set the
project name, folder, and agent in the popup; Tab moves fields, left/right
chooses the agent, and Enter on **Create project + agent** submits. Escape
cancels. A tmux session always needs a window, so the first window runs the
agent directly rather than creating a placeholder shell.

The same action is available from the CLI:

```sh
tmux-agents launch --project my-project --directory ~/code/my-project codex
```

### Project defaults

Open the agent menu (**Ctrl-b a** or sidebar **x**) and choose **p** to edit
the project's preferred provider, launch folder, arguments and worktree mode.
Tab moves between fields; arrows choose the provider; arrows/Space toggle
worktree mode. Enter on **Save project defaults** saves; Escape cancels.
**Reset saved defaults** clears only that project's preferences.

Choose **d** in the agent menu to launch the saved default. The menu shows the
preferred provider and whether it will use a worktree. From the CLI, omit the
provider to use the saved choice (Codex when no preference exists):

```sh
tmux-agents launch
tmux-agents defaults                           # open the editor
tmux-agents defaults --project app --provider claude --directory ~/code/app --worktree on
tmux-agents defaults --project app --args-json '["--model","MODEL_NAME"]'
tmux-agents defaults --project app --show
tmux-agents defaults --project app --reset
```

Explicit launch options override saved values. **--no-worktree** forces a shared
checkout; **--worktree** forces isolation. An empty saved folder follows the last
focused terminal. Explicit trailing agent arguments replace saved arguments;
**--no-default-args** suppresses saved arguments, and **--no-defaults** ignores
all preferences for one launch. Saved arguments apply only when launching the
preferred provider, so another provider does not receive incompatible flags.
Changing the saved provider clears its old arguments unless replacement arguments
are supplied in the same update. The editor keeps separate argument drafts while
you switch providers, so switching back restores that provider's in-progress text.
The editor accepts normal quoted argument text; the CLI accepts an argv JSON
array. Both pass arguments literally, without shell evaluation.

Defaults are keyed by project/session name and stored privately in
`project-defaults.json` under the tmux-agents state root. Same-name projects on
this machine's tmux servers share them. They persist across restarts/uninstall;
repository files are never imported as launch settings. Sidebar project rename
moves its defaults, refusing to overwrite another saved project's preferences.
A direct native tmux rename uses the new name's defaults; the old saved entry
remains available. Missing saved folders and invalid settings stop launch with
an error; `--no-defaults` allows a one-off launch while settings are repaired.

Saving preferences does not change running agents. New-project form choices are
one-off choices and do not overwrite saved defaults or inherit hidden arguments.
History resume keeps its recorded conversation and directory, ignoring defaults.

### Optional agent worktrees

In the agent menu, **c / a / o** launches Codex / Claude / OpenCode in a shared checkout;
**C / A / O** launches that provider in a new Git worktree. In the new-project
form, select the Agent field and press **w** or Space to toggle worktree mode.
It is off by default.

```sh
tmux-agents launch --worktree codex
tmux-agents launch --worktree --branch agents/auth-fix claude
tmux-agents launch --project review --directory ~/code/app --worktree opencode
```

Worktree mode requires Git and at least one commit. Each agent gets a new branch
and checkout from the current `HEAD`; staged, unstaged, ignored and untracked
files stay in the original checkout. A tracked current subdirectory is preserved.
Branches default to unique `agents/PROVIDER-…` names; a custom branch must be new.
Checkouts live outside your repository, under the tmux-agents state root's
`worktrees/` directory. The diff pane follows the agent's checkout normally.

Closing an agent or uninstalling tmux-agents keeps its worktree and branch.
Use `git worktree list` to inspect them and Git's normal merge/remove commands
when finished. A failed terminal launch rolls back an untouched new checkout;
if it has changed, it is retained and its path is reported. No force removal,
reset, stash or automatic merge is performed.

When its last real terminal exits, the owned navigation pane is removed so
tmux closes the empty window. Other running panes and terminals deliberately
retained with `remain-on-exit` are preserved.
It preserves existing terminal input. Standard tmux windows, splits, mouse
selection and your existing shortcuts continue to work.

## Conversation names and renaming

Conversation rows prefer your custom name, then the agent's terminal title,
then the CLI name. Long names scroll horizontally while their row is selected
and the sidebar is focused; the status stays fixed. Scrolling pauses at each
end and resets when you select another row. Automatic titles update while you work, when the agent
publishes them. Codex conversation titles are supported this way; Claude and
OpenCode also work when they publish terminal titles. This does not read
conversation files or generate titles with a model.

Ordinary terminal rows show the foreground program, such as **Neovim**,
**Vim**, `htop`, or `python3`. They return to `shell` when the program exits.
A custom sidebar name still takes priority.

Focus the sidebar with Alt-e, select a row, and press `r`. Edit its name,
then press Enter to save or Escape to cancel. Ctrl-U clears the field.
Saving an empty conversation name restores its automatic title. Session and
window names cannot be empty. Press Shift-r (`R`) to rename the selected
conversation’s window.

Custom conversation names label the tmux pane; they do not rename the chat
inside the agent. They remain until reset or that pane is closed, including
when the sidebar is restarted. You can also rename from the CLI:

```sh
tmux-agents rename "Fix checkout flow"           # current pane
tmux-agents rename --pane %1 "Investigate login"
tmux-agents rename --pane %1 --reset            # automatic title
tmux-agents rename --session '$1' "backend"
tmux-agents rename --window @2 "tests"
```

## Optional diff pane

Press **Ctrl-b, v** to toggle a read-only diff pane on the right, or select
an agent in the navigation and press **d**. Navigation stays on the left and
the agent stays in the middle. The window must be at least 120 columns wide.
No diff pane opens automatically.

The default is **Latest turn**, when a checkpoint is available, with an
explicit **Working tree** fallback. Once you enable this feature for a
terminal, its native `UserPromptSubmit` events checkpoint the checkout at
turn start. Claude/Codex hooks and OpenCode 2.x execution events feed this
signal; Codex hook trust is still required. Existing in-progress turns cannot
be reconstructed retroactively.

Inside the diff pane:

| Key | Action |
| --- | --- |
| Tab | Switch latest-turn / full working-tree diff |
| b | Take a manual checkpoint; show changes from now |
| o | Choose a Git repository; blank restores the terminal's current directory |
| j/k, Page Up/Down, g/G | Scroll vertically |
| h/l or Left/Right | Pan long lines when wrapping is off |
| w | Toggle soft wrapping (remembered per window) |
| r | Refresh now (otherwise updates automatically) |
| q / Escape | Close the diff pane |

The view follows the real terminal selected in the same window. Its default
repository comes from that terminal's working directory. If your agent was
started above several repositories, use **o** to choose one, or:

```sh
tmux-agents diff --directory /path/to/checkout
```

This compares tracked and non-ignored untracked files without stashing,
staging, committing, or changing Git's index. A turn diff describes changes
to the **checkout since its checkpoint**; simultaneous edits by other agents
or people in the same checkout are included too.

Checkpoints contain local copies of dirty files in the private per-server
state directory (`diff/`, files mode 0600). They start only after you opt in
by opening a diff pane, replace the previous checkpoint each turn, and are
deleted on uninstall. Capture is limited to 300 paths, 1 MiB per file and
16 MiB of content; oversized files and submodules are explicitly reported as
omitted, and binary changes get a summary. Git is needed only for this feature.
Closing the last real terminal removes both owned side panels; neither keeps
an empty agent window alive.

## Status

`RUNNING`, `INPUT`, `APPROVAL`, `QUESTION`, `DONE`, `IDLE`, `UNKNOWN` use text
and terminal palette colors. A source mark follows each agent status:

**Need input** is reserved for explicit harness questions, forms, and typed
decision requests. A completed turn, ordinary prompt, session start, or
interruption is **Idle** `(－ω－) zzZ`. Permission requests remain **Need approval**.
Completed turns can still become unread while you are away; unread does not
mean the agent requires a decision.

- `=`: native agent lifecycle event.
- `#`: current terminal prompt/footer detection.
- `~`: observed terminal activity; this is an estimate.
- `?`: no reliable status signal yet.

A live process alone does not imply that it is working. Event state is bound
to the tmux server, pane, agent PID and process start time. Exited processes
clear active requests; ordinary numbered answers are not decision prompts.
Nothing stores conversation text: polling captures terminal text in memory,
then retains status metadata and a short activity hash only.

## Install (portable Linux)

Download the installer archive and matching SHA256 file from
[GitHub Releases](https://github.com/afikrim/tmux-agents/releases/latest).

Copy the release archive to the Linux machine, verify it, extract it, and run:

```sh
sha256sum -c tmux-agents-<version>.tar.gz.sha256
tar -xzf tmux-agents-<version>.tar.gz
cd tmux-agents-<version>
./install.sh --integrate codex claude opencode
```

Omit `--integrate ...` to skip agent hooks, or name only the agents you use.
`install.sh` forwards flags (e.g. `--prefix PATH`) to `scripts/install.py`.

Integration appends handlers to `~/.claude/settings.json` and
`~/.codex/hooks.json`, preserving existing handlers. OpenCode uses a local TUI
plugin registered in `~/.config/opencode/cli.json`; provider/server
configuration is left alone. Every changed file gets a timestamped backup.

**Codex:** open `/hooks` and trust the new tmux-agents handlers. The installer
does not bypass trust or change model, sandbox or approval settings. New Codex
windows launched here use `--no-daemon`, so their hook environment belongs to
the correct pane. **Claude / OpenCode:** newly started terminals load the
integration; existing terminals may need a restart/resume. OpenCode reports
only the session currently visible in its terminal.

### Dependencies

- Python 3.9+, tmux 3.3+, `ps` (procps), and a UTF-8 locale.
- The native OpenCode plugin targets OpenCode 2.x; older versions use screen
  detection without the plugin.
- The agent CLIs (`codex`, `claude`, `opencode`) are optional; only the ones
  you integrate need to be installed.

| Distro | Command |
| --- | --- |
| Debian / Ubuntu | `sudo apt install python3 tmux procps locales git` |
| Fedora / RHEL | `sudo dnf install python3 tmux procps-ng glibc-locale-source git` |
| Arch | `sudo pacman -S python tmux procps-ng git` |

Use a UTF-8 locale (e.g. `export LANG=C.UTF-8`) so the sidebar renders
correctly.

### Where files go

User-local only; nothing outside your home directory (and the install prefix):

- Runtime copy: `~/.local/share/tmux-agents`
  (or `$XDG_DATA_HOME/tmux-agents` when `XDG_DATA_HOME` is set).
- Launcher: `~/.local/bin/tmux-agents` (a symlink into the runtime copy).
- tmux config: one managed block in `~/.tmux.conf`
  (or `$XDG_CONFIG_HOME/tmux/tmux.conf` when that file is already in use);
  the previous file is backed up with a timestamp first.
- State: `~/.local/state/tmux-agents/<server-id>/`
  (or `$XDG_STATE_HOME/tmux-agents/<server-id>/`).

The installed copy is independent of the extracted archive: you can delete
the archive directory after installing. Custom location:

```sh
./install.sh --prefix ~/apps
# runtime ~/apps/share/tmux-agents, launcher ~/apps/bin/tmux-agents
```

Upgrades are safe: the installer refuses a runtime directory that is a
symlink or that does not look like a previous install, and it keeps the
previous runtime until installation succeeds. Copy or agent-integration
failures restore the prior runtime and provider settings. If tmux rejects
the configuration, the new runtime and any previous runtime backup are kept
for repair: fix the reported config error and rerun the installer, or run
`tmux-agents uninstall`. If the launcher directory is not on `PATH`, the installer prints
the exact `export PATH=...` line to add (e.g. in `~/.profile`); open a new
shell afterwards.

### Existing vs cold tmux

- tmux already running: the installer sources the config, starts the daemon,
  and the sidebar appears in existing and new windows without restarting
  agents. Windows narrower than 72 columns hide the sidebar until resized.
- No tmux server (cold machine): the installer only saves the configuration
  and reports `no server was started`. Start tmux normally; the config hooks
  save the current theme/keys on first launch and start the daemon.

Check the machine any time with:

```sh
tmux-agents doctor
```

Doctor reports prerequisites, this tmux server's daemon/snapshot health, provider
hook/plugin configuration, the last retained lifecycle/hook event, and each live
agent's status source. Unknown states explain missing signals, expired events,
process mismatches or a snapshot still waiting for a new event.

**Configured** means the expected handlers/plugin entries exist. **Observed**
hook delivery means a native hook event was recorded; per-agent confirmation also
requires that event to match its current process. Historical events from another
process, manually injected test events and legacy events of unknown origin do
not confirm that the current agent's hooks work. Configured Codex hooks may still
need trust approval in `/hooks`; doctor does not inspect or bypass that approval.
With no retained hook event, delivery stays **unverified** until the next native
event arrives. Existing OpenCode sessions may need a restart to load plugin changes.

```sh
tmux-agents doctor --json               # structured diagnostic report
tmux-agents doctor --strict             # also fail on runtime/integration warnings
tmux-agents doctor --dependencies-only  # installation prerequisites only
```

Doctor reads metadata and configuration structure without copying configuration
contents, prompts or terminal text into its report. It does not start a tmux
server/daemon, launch agents, change focus, or repair settings automatically.
Required missing dependencies return a nonzero exit status; optional unconfigured
providers do not fail the normal check. `--strict` additionally fails on reported
runtime/integration warnings. Installers keep using the prerequisites-only check.

## CLI

```sh
./bin/tmux-agents status
./bin/tmux-agents launch codex
./bin/tmux-agents launch --window @3 claude -- --resume
./bin/tmux-agents --socket my-server status
```

State lives in `~/.local/state/tmux-agents/<socket-id>/`. The namespace is the
same for `-L`, `-S` and inherited `$TMUX` when they identify the same server.
`daemon-stop` stops only this server's verified sidebar daemon.

## Remove

```sh
tmux-agents uninstall
```

This stops the daemon, removes sidebar panes and owned hooks, restores saved
key bindings/window rename settings and the saved tmux theme, and removes only
this project's agent handlers. Running agent/shell panes stay alive.
`--keep-integrations` retains the native handlers. A tombstone prevents queued
hooks from recreating the UI; reinstall clears it.

`uninstall` deliberately leaves files behind; delete them when you no longer
want the tool on this machine:

- Runtime copy: `~/.local/share/tmux-agents`
  (or `$XDG_DATA_HOME/tmux-agents`, or `<prefix>/share/tmux-agents`).
- Launcher symlink `~/.local/bin/tmux-agents` — remove only the symlink that
  points into the runtime above, and leave the `bin` directory itself.
- Per-server state: `~/.local/state/tmux-agents`
  (or `$XDG_STATE_HOME/tmux-agents`).
- Timestamped tmux config backups next to your `tmux.conf`
  (`.pre-tmux-agents-*`); keep any you may still want to restore from.
- The optional Kitty profile below lives outside tmux entirely; `uninstall`
  never touches terminal configuration, so delete any lines you added
  yourself if you no longer want them.

## Validation

```sh
python3 -m unittest discover -s tests -v
node tests/opencode-bridge.mjs
```

Tests use private tmux sockets and temporary configuration/state. They exercise
real PTY keyboard/mouse navigation, cross-session selection, safe launch argv
and cwd, parallel creation, hide/show, resize, hook payloads, process exit,
namespace identity, install/uninstall and preservation of existing settings.
The OpenCode bridge is tested against a simulated event host; module loading
was separately verified with installed OpenCode 2.0.27 without a model request.
Native model-driven end-to-end turns are not part of these checks.

Inspired by [Corgi](https://github.com/tals/corgi). See LICENSE-NOTICE.

## Appearance (optional)

Ink / Mint coordinates the sidebar and tmux chrome: grouped workspaces,
right-aligned status text, focus-aware selection, and a bottom-anchored agents
list. The native status line provides an aligned window-tab strip above the
main content, with no session label or clock. Theme values are saved at install
and restored on uninstall. No whole-screen clear is sent on normal updates.

A matching terminal profile ships at `extras/kitty-tmux-agents.conf` (also in
the installed runtime) and is strictly opt-in: the installer never edits shell
or terminal configuration. To try it, append that file to your Kitty config
and reload Kitty:

```sh
cat ~/.local/share/tmux-agents/extras/kitty-tmux-agents.conf >> ~/.config/kitty/kitty.conf
# reload Kitty (default: Ctrl+Shift+F5); delete the appended block to revert
```

Fonts are not bundled or downloaded: install a Nerd Font yourself (your
distro's font packages or a manual download, e.g. Monaspace Nerd Font) and set
`font_family` in the profile to the font you installed. Your shell is left
alone (fish stays fish); Powerlevel10k is zsh-only, so consider it only if you
use zsh.

References: [Awesome tmux](https://github.com/rothgar/awesome-tmux),
[Powerlevel10k](https://github.com/romkatv/powerlevel10k),
[Monaspace](https://github.com/githubnext/monaspace).
