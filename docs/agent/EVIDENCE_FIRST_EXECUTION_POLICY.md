# Evidence-first execution and investigation policy

These rules are mandatory for the planner, orchestrator, specialist roles, recovery path, and runtime tools.

## 1. Diagnose only the reported problem

- Start from the user's requested outcome and the exact observed failure.
- Inspect the smallest relevant set of files, logs, configuration, and runtime state.
- Separate confirmed facts from hypotheses. Every diagnosis must cite an observable signal (error, failing test, exit code, state transition, or reproducible behavior).
- Do not investigate unrelated warnings, perform broad scans, or generate speculative root-cause reports.

## 2. Dependency changes require proof and approval

- Never install, upgrade, downgrade, remove, or download a package as a diagnostic experiment.
- First inspect the existing manifest, lockfile, environment, and available commands.
- A dependency change is justified only when a concrete missing, incompatible, or security-blocking dependency is demonstrated and the change is necessary for the approved task.
- Explain the evidence, exact package, version constraint, scope, and impact, then request approval before changing dependency manifests or the environment.
- Prefer the project's existing package manager and lockfile. Never switch package managers or regenerate an entire lockfile to fix an unrelated issue.

## 3. No diagnostic clutter

- Do not create diagnostic scripts, reports, snapshots, temporary configs, or test files unless they directly verify the requested behavior.
- Prefer existing logs, execution journal entries, targeted commands, and temporary artifacts outside the project workspace.
- Remove temporary artifacts created during an approved investigation. Preserve user files and pre-existing changes.

## 4. Stop unproductive loops

- Each investigation cycle must state: hypothesis, smallest discriminating check, observed result, and next action.
- Never repeat the same command with the same inputs unless the environment or relevant files changed and that change is recorded.
- After two failed attempts against the same hypothesis, abandon it or request human input; do not keep retrying with superficial variations.
- Set a finite investigation budget: at most 5 diagnostic actions before reassessing the plan, and at most 2 repair attempts per root-cause hypothesis.
- Stop when the evidence is sufficient, the approved objective is met, or a blocker requires user input. Do not pursue perfection outside the requested scope.

## 5. Repair and verification

- Make the smallest reversible change that addresses the confirmed cause.
- Run the narrowest relevant test first, then the project's standard checks when appropriate.
- Add tests only for requested behavior or a reproduced regression; do not generate generic test suites.
- If a check is unavailable, explain why. Never claim verification that did not run.
- If a repair fails twice, preserve evidence, stop, and report the exact blocker and safe next step.

## 6. Runtime and process safety

- Treat Ollama and the KardecAgent Core as long-lived shared services, not disposable child processes of a project test.
- Project execution, test, debug, and build commands must not terminate unrelated services or process trees.
- Track only processes started by the task/runtime manager; stop only owned processes by their recorded process identity.
- Never use broad process-kill commands or terminate by executable name alone.
- Runtime start/stop, health, logs, and restart actions must be explicit, scoped, observable, and recoverable.

## 7. Required journal fields

For every diagnostic or repair action, record a concise event containing:
- action and purpose;
- hypothesis or requirement;
- command/check identifier (not an unbounded raw log);
- outcome and relevant evidence;
- whether the action changed files or dependencies;
- remaining investigation budget and reason to continue/stop.

A task is not allowed to claim success merely because commands exited successfully; acceptance criteria and relevant tests must pass.

## 8. Prevent duplicate side effects

- The executor records a deterministic signature for every successful tool action.
- An identical `append_file` or `run_command` call is blocked when the most recent successful workspace mutation is the same action.
- A different successful workspace mutation establishes new state and permits a justified retry of the same command or append operation.
- Read-only actions and different commands are unaffected.
- When blocked, the model receives the prior action category and is instructed to use recorded evidence, inspect the outcome, or make a justified change before trying again.
- This guard is a loop-prevention measure, not a substitute for user approval, command sandboxing, or idempotency guarantees for external systems.
