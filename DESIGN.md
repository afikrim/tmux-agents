---
name: tmux agents — Ink / Mint
description: A calm agent workspace with clear attention states and Powerline chrome.
colors:
  bg: "#11151f"
  panel: "#1b2332"
  selected: "#283b4a"
  hover: "#223140"
  fg: "#e4eaf4"
  muted: "#96a6bd"
  line: "#38465d"
  accent: "#9ce0c0"
  amber: "#f2c38b"
  rose: "#f2a7b5"
  blue: "#a8c9f5"
typography:
  body:
    fontFamily: "Monaspace Neon"
    fontSize: "15pt"
    fontWeight: 400
    lineHeight: 1.1
  heading:
    fontFamily: "Monaspace Neon"
    fontSize: "15pt"
    fontWeight: 700
    lineHeight: 1.1
spacing:
  inset: "1ch"
  group: "1lh"
  window: "3ch"
  pane: "5ch"
---

## Overview
Mode: Operate. Keep the two-section project/agent layout and
make waiting agents easy to find. Borrow the segmented status-line vocabulary
of Powerlevel10k and the restrained pastel contrast seen in Awesome tmux themes.

## Colors
Ink is the base, panels identify the current window, and the stronger selection
surface appears only when keyboard focus is in the sidebar. Mint identifies
working/current context; warm amber and rose identify questions and approvals.
Status meaning is always written in text; color is supplemental.

## Typography
Monaspace Neon provides the coding face, with weight rather than size changes
for hierarchy. Nerd Font symbols supply Powerline separators through kitty's
symbol fallback. Actual Powerlevel10k requires zsh; keep the existing shell
unless the user explicitly chooses to switch it.

## Layout
Fixed-width left rail with project sessions above the selected project's agent
panes. The agents block reserves the bottom third of the sidebar height,
including its heading and divider, even for short or empty lists. Tiny terminals
retain enough room for one agent; projects use the space above. Each section scrolls independently. Each
project has a title, counts beneath it and one blank row between projects.
Each live agent pane has a title and supporting activity caption, with one blank
row between entries and a stable
real-pane target for activation. Section headings and a
single divider carry the hierarchy. Shortcut hints live in a searchable popup,
not permanent footers. Native centered popups inherit the Ink / Mint palette
and terminal font. Mouse coordinates use explicit rendered-row maps.
Measure Unicode text in terminal cells.

## Elevation & Depth
Use solid tonal surfaces. The active window and focused row have distinct
backgrounds. Render complete frames without clearing the screen between polls.

## Shapes
Square terminal geometry. A native window-tab strip sits above main content,
with blank space above the sidebar and optional diff pane. Insets follow actual
panel widths and disappear when hidden or zoomed. No session label or clock.
Active tabs use the selected surface and mint text; other tabs use muted ink.
Tab labels follow the focused terminal: agent conversation title, program name,
or shell directory. Side-panel focus keeps the last terminal label; underlying
window names remain unchanged.
Tabs have a fixed 24-cell width; only the focused tab's long title scrolls,
pausing at both ends. Side-panel focus preserves the selected terminal title.
Navigation uses text. The user-requested sleeping kaomoji `(－ω－) zzZ` identifies idle captions.

## Components
Project names identify sessions; counts below use ▶ running and ● idle,
then a middle-dot separator and unread count only when nonzero.
Unread counts are underlined when actionable and open the first unread terminal.
Agent entries show conversation/program titles, right-aligned status, and a
subordinate current-action caption. Only the current project gets the active
background; project keyboard preview keeps a cursor marker, and hover is temporary.
Agent keyboard selection gets the focused background. Agent titles scroll when focused and
clipped. A project cursor previews that project's agents before activation.
Project activation focuses a real terminal in its active window, remembering
the last terminal when a side panel held focus.
Shell/editor panes are excluded. Splits hosting separate agents each get an
entry; counts use those same agent panes. New project + agent is a compact native
popup that creates the session and its first agent window in one action.
Pointer hover uses a subtler background and scrolls clipped titles without
moving keyboard selection, switching projects, or taking terminal focus.
Ctrl-b s opens the project picker; ? opens contextual shortcut help. These
popups search and scroll without creating real panes.

## Do's and Don'ts
- Do preserve status-source confidence and all navigation controls.
- Do restore the previous tmux palette on uninstall.
- Do retain readable contrast for muted text and selected states.
- Don't restyle agent-owned interactive interfaces or change agent settings.
