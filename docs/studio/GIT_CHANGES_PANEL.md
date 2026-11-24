# Studio Git changes panel

The **Alterações** panel uses native Tauri commands to inspect and manage the current workspace's local Git index.

## Supported actions

- Read branch name, ahead/behind counters and changed-file status from `git status --short --branch`.
- Select a file and inspect staged and unstaged diffs separately.
- Stage or unstage an individual file.
- Create a local commit with an explicit message.

## Safety boundaries

- Git is invoked directly with argv; no shell command string is constructed.
- File paths must be relative to the canonical repository root. Absolute paths, traversal components and symlinks resolving outside the workspace are rejected.
- Diff output is capped at 250,000 characters.
- Commits are explicit user actions. The panel does not push, fetch, pull, or contact a remote.
- The panel expects the selected workspace to be the repository root, not an arbitrary subdirectory.

## Current limitations

This is the first local Git milestone. It does not yet provide branch creation/switching, merge/rebase, conflict resolution, remote operations, commit history visualization, or a full graphical diff editor. Untracked files must be staged before Git can produce a normal diff for them.
