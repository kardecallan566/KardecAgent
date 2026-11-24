# Bounded agent retry policy

The agent may resume an already-approved plan after a failed execution only when the recovery manager confirms that the workspace is consistent.

## Defaults and configuration

- `max_retries`: 1 retry per approved task or logical subtask by default.
- Set `KARDECAGENT_MAX_RETRIES=0` to disable automatic retries.
- The legacy prefix `KARDEC_MAX_RETRIES` is also accepted.
- A retry after `MAX_ITERATIONS` receives a small additional iteration budget, capped at twice the configured base limit. Other retry categories retain the base limit.

## Retry gates

A retry is denied when:
- the task did not end in `failed` or `max_iterations`;
- recovery did not complete, reported an error, or found workspace conflicts;
- the failure is a plan, recovery-conflict, or unknown failure;
- the retry budget is exhausted; or
- a `run_command` failure is not classified as transient.

Transient command failures may be retried only after successful workspace recovery. This does not prove that external side effects were reversed; the command policy therefore remains conservative.

## Audit trail

The journal records the retry decision, reason, attempt number, failure category, and next iteration limit. Retry start/finish events remain separate so a resumed task can enforce the same bounded budget.

Retries always reuse the previously approved plan and do not request a new approval or expand its scope.
