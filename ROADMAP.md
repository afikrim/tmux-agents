# Next milestones

Implement and verify each milestone independently, with a separate commit.

1. **Implemented:** Conversation history and resume — searchable metadata by project/provider;
   jump to a verified live pane or resume the exact saved provider conversation.
2. **Next:** Attention inbox — questions, approvals and unread completions across projects.
3. Optional Git worktree per new agent — separate branch and checkout per task.
4. Integration health — hook connectivity, last event and unknown-status reasons.
5. Project defaults — provider, directory, launch arguments and worktree preference.
6. Optional desktop notifications — return to the exact agent needing attention.

## Resizing

Add a configurable sidebar minimum width (proposed default: 24 terminal cells),
with the current 32-cell default width. Clamp manual resizing to the minimum
while leaving enough room for the main terminal. Preserve the user's preferred
width across window changes, collapse safely on narrow terminals, and keep tab
insets, text clipping, hover and mouse hit targets aligned after every resize.
The Agents section should continue reserving the bottom third of the sidebar.
