# Next milestones

Implement and verify each milestone independently, with a separate commit.

1. **Implemented:** Conversation history and resume — searchable metadata by project/provider;
   jump to a verified live pane or resume the exact saved provider conversation.
2. **Implemented:** Attention inbox — questions, approvals and unread completions across projects.
3. **Implemented:** Optional Git worktree per new agent — separate branch and checkout
   per task. Linux verification caught and corrected tmux 3.3 single-command quoting.
4. **Implemented:** Integration health — hook configuration/delivery, last event and unknown-status reasons.
5. **Implemented:** Project defaults — provider, directory, launch arguments and worktree preference.
6. **Implemented:** Optional desktop notifications — return to the exact agent needing attention.

## Resizing

**Implemented:** Configurable sidebar minimum (24 columns), preferred width
(32 by default), and private per-server persistence. Manual resizing is
clamped on the daemon's reconciliation pass; automatic narrowing/collapse,
diff panes and zoom preserve the preferred width. Main content retains at
least 39 columns. Native tab insets and mouse maps track actual geometry;
the Agents section keeps its bottom third.

## Action details

**Implemented:** Native tool targets in agent captions, such as Editing app.py,
Reading README.md and Working on app.py after a file tool completes. Only
bounded filename/scope basenames are retained; generic captions remain when
the harness supplies no safe target.
