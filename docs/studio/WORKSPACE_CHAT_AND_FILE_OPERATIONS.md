# Studio workspace files, task history and chat transcript

## Workspace file operations

The Explorer supports creating files and folders using paths relative to the
workspace. Nested paths are created safely: missing parent folders are created
inside the workspace, while absolute paths, traversal components and symlinked
parent directories are rejected. Existing destinations are never overwritten by
the create commands.

Saving an open file writes a complete temporary sibling file and then replaces
the destination. On Windows, the previous file is moved to a temporary backup
and restored if replacement fails. File reads and writes remain capped at 1 MB.
The editor keeps its dirty indicator until the save command succeeds.

## Task history

Completed, verified, failed and max-iteration tasks can be removed from the
Studio task list with the X control. This only removes the task from the
workspace's locally persisted history; it does not delete project files,
journals, checkpoints or task data from disk. Recoverable and active tasks do
not expose the removal control.

## Chat transcript

New chat messages store an ISO-8601 creation timestamp. The transcript groups
messages by local calendar date and displays each message's local time. Older
date groups start collapsed; the newest date stays expanded. Users can expand
or collapse any day. Legacy messages without timestamps are retained in a
separate "Histórico sem data" group.

The transcript scrolls to its newest message when messages arrive or the Agent
panel is reopened. The transcript remains bounded to the latest 100 messages,
matching the existing workspace persistence policy.
