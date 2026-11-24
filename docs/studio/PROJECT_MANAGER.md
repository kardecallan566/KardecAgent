# Studio project manager

The Studio keeps a local list of the ten most recently opened project folders.

## Behavior

- Opening an existing project or creating a new one adds it to the recent list.
- Each record stores the absolute workspace path, display name and last-opened timestamp in local application storage.
- The Projects activity view provides quick actions to open a folder, create a project, switch to a recent workspace, and remove an entry from the recent list.
- Removing a recent entry does not delete any project files. If it was the auto-restored workspace, the startup pointer is cleared as well.
- Switching projects reuses the existing workspace guard: active agent tasks block the switch, and unsaved editor changes require explicit confirmation before discarding.

The recent-project list is local to the Studio installation and is not synchronized or sent to the model provider.
