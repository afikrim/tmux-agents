---
name: tmux agents — Ink / Mint
description: A calm agent workspace with clear attention states and Powerline chrome.
colors:
  bg: "#11151f"
  panel: "#1b2332"
  selected: "#283b4a"
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
Mode: Operate. Keep the Corgi-style sessions/windows/agents hierarchy and
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
Fixed-width left rail; one blank line between sessions; four lines of header;
footer anchored to the bottom. Short terminals collapse header/footer chrome.
Measure Unicode text in terminal cells. Sidebar click coordinates follow the
same header/body geometry used to render the frame.

## Elevation & Depth
Use solid tonal surfaces. The active window and focused row have distinct
backgrounds. Render complete frames without clearing the screen between polls.

## Shapes
Square terminal geometry with Powerline separators in the tmux status bar.
No emoji-based navigation or dependency on decorative sidebar icons.

## Components
Session headings are bold, pane IDs stay internal, and status labels
align to the right. The idle sidebar highlights current context; the focused
sidebar highlights the keyboard target. Spacer rows and window-name context rows are not keyboard stops. Keyboard
navigation targets sessions and real terminal panes; window rows remain
available for mouse selection.

## Do's and Don'ts
- Do preserve status-source confidence and all navigation controls.
- Do restore the previous tmux palette on uninstall.
- Do retain readable contrast for muted text and selected states.
- Don't restyle agent-owned interactive interfaces or change agent settings.
