# tmux-agents

An always-visible, Corgi-inspired workspace sidebar inside tmux. It lists
projects (tmux sessions) above agent windows, with live status for Codex, Claude Code
and OpenCode. Your agents keep their own interactive terminals.

## Controls

| Control | Action |
| --- | --- |
| Alt-e | Focus the sidebar in this window |
| Ctrl-b, s | Search and switch projects in a popup |
| Ctrl-b, S | Hide/show the sidebar in this window |
| Ctrl-b, ? | Open the shortcut reference popup |
| Ctrl-b, a | Open the new-agent menu |
| Ctrl-b, v | Toggle the right-hand diff pane |
| d (in sidebar) | Toggle diff for the selected terminal |
| Click a row / Enter | Switch to the project or agent window |
| Hover a row | Highlight it and scroll its long title, keeping terminal focus |
| j/k, arrows, mouse wheel | Move through projects and agent windows |
| g/G, Page Up/Down | Navigate a long tree |
| x (in sidebar) | Start an agent in the selected workspace |
| r / Shift-r (in sidebar) | Rename selection / its window |
| ? (in sidebar or diff) | Open the shortcut popup |
| q / Escape | Return focus to the previous terminal |

The sidebar has two sections: **Projects** on top and **Agents** below.
Moving through projects previews only that project's windows; Enter switches
to the project. Each window appears once, labelled with its active agent,
conversation, or foreground program. Normal tmux splits remain accessible
through tmux's pane controls. The agents section sits against the bottom and
is capped at half the sidebar height, including its heading. Longer lists
scroll within that space; projects use the space above. Both sections scroll
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
right-aligned status text, focus-aware selection, Powerline status segments,
and a footer anchored to the pane bottom. Theme values are saved at install
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
