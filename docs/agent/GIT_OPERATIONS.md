# Git workflow tools and approval policy

KardecAgent can inspect and manage the Git repository in the active workspace.

## Read-only operations

- `git_status`: working tree and current branch status
- `git_diff`: unstaged diff
- `git_log`: recent commit history
- `git_branches`: local and remote branches
- `git_remotes`: configured remotes
- `git_pr_status`: GitHub CLI pull-request status

Read-only operations do not require a plan step or a separate confirmation.

## Mutating operations

The following operations require an active approved plan step and a separate, operation-specific high-risk approval from the user:

- `git_fetch`, `git_pull`, `git_push`
- `git_create_branch`, `git_checkout`
- `git_commit`, `git_merge`
- `git_create_pr`, `git_merge_pr`

The approval prompt identifies the exact operation and its arguments. Approval of the development plan is not Git authorization. If the user rejects or does not grant approval, the operation is not executed.

## Safety constraints

- Pull uses `--ff-only`; conflicts are not silently resolved.
- Push never uses force or force-with-lease.
- Commits stage only explicitly selected paths. If unrelated changes are already staged, the commit is refused rather than including them.
- Branch names and refs are validated and passed as process arguments, never through a shell.
- Git mutations are disabled inside scoped subtasks.
- PR creation and PR merge use the authenticated GitHub CLI (`gh`). If it is unavailable or unauthenticated, the tool reports the error and does not claim success.
- Merge operations rely on repository permissions and branch protection; KardecAgent does not bypass checks or protections.

## Execution map

The Studio execution map consumes runtime events. It displays the approved plan, completed and upcoming steps, subtask states, action start/result/error events, affected file paths, and verification evidence. Event details are curated to avoid exposing file contents or raw command output in the activity timeline.
