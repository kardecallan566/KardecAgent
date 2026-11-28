# Adaptive Execution

PR #56 connects the deterministic Adaptive Intelligence Engine to the approved
task and subtask lifecycle.

## Execution flow

1. After the parent plan receives explicit user approval, assess its complexity
   using task signals, approved plan steps, and the bounded project snapshot.
2. Record the assessment in the task event journal.
3. Allow the assessment to recommend decomposition in addition to the existing
   project-size/plan-size heuristic. Decomposition still has to map every
   subtask to approved parent plan steps and validated project-relative scopes.
4. Before scheduling, rank Skills against each subtask objective. Load
   instructions only when the existing explicit Skill approval callback returns
   approval. Rejection or missing approval leaves the Skill instructions out
   of the model context.
5. Set the scheduler's parallelism ceiling from the adaptive assessment,
   bounded by the configured limit and hard maximum of four. Existing
   dependency checks, disjoint-scope checks, and non-overlapping plan-step
   checks remain mandatory.
6. On a failed subtask, record a recovery recommendation as telemetry only.
   The current bounded retry/recovery policy remains authoritative; this
   recommendation does not execute tools, expand scope, or silently retry.

## Approval and safety

- Parent plan approval remains mandatory before this flow starts.
- Subtask Skills require explicit approval independently; a metadata match is
  never permission to load instructions.
- Skill content remains untrusted reference material.
- Model switching continues to use the existing model-switch approval
  callback. Adaptive scheduling does not switch models.
- Resume does not invent or restore Skill consent across Core sessions.
- No autonomous plan expansion, self-modification, dependency installation,
  or tool execution is introduced by the adaptive policy.

## Observability

New task events include `adaptive_assessment`,
`adaptive_execution_assessed`, per-subtask Skill selection/approval/load
events, and `adaptive_recovery_recommendation`. They contain the decision
factors and effective parallelism so Studio can render them in the later
Intelligence Dashboard milestone.

## Validation coverage

Tests cover assessment-triggered decomposition, adaptive concurrency caps,
per-subtask Skill approval, and refusal keeping Skill instructions unloaded.
GitHub Actions results must be checked before declaring CI passed.
