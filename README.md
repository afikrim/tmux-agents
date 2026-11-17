# tmux-agents

An always-visible, Corgi-inspired workspace sidebar inside tmux. It lists
sessions, windows and agent panes, with live status for Codex, Claude Code
and OpenCode. Your agents keep their own interactive terminals.

## Controls

| Control | Action |
| --- | --- |
| Alt-e | Focus the sidebar in this window |
| Ctrl-b, S | Hide/show the sidebar in this window |
| Ctrl-b, a | Open the new-agent menu |
| Click a row / Enter | Switch to that session, window or pane |
| j/k, arrows, mouse wheel | Move through the tree |
| g/G, Page Up/Down | Navigate a long tree |
| x (in sidebar) | Start an agent in the selected workspace |
| r / Shift-r (in sidebar) | Rename selection / its window |
| q / Escape | Return focus to the previous terminal |

Window names remain visible as context labels; Up/Down, j/k and the mouse
wheel skip them and move between sessions and agent/shell panes.

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

Focus the sidebar with Alt-e, select a row, and press `r`. Edit its name,
then press Enter to save or Escape to cancel. Ctrl-U clears the field.
Saving an empty conversation name restores its automatic title. Session and
window names cannot be empty. Press Shift-r (`R`) to rename the selected
conversation’s window while keeping window labels skipped by arrow keys.

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
| Debian / Ubuntu | `sudo apt install python3 tmux procps locales` |
| Fedora / RHEL | `sudo dnf install python3 tmux procps-ng glibc-locale-source` |
| Arch | `sudo pacman -S python tmux procps-ng` |

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
