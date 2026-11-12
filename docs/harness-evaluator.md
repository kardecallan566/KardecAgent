# Harness Evaluator v0

The evaluator provides deterministic, inspectable metrics for structured verification output. It does not ask the model to grade itself.

## Input

`evaluate_verification({"checks": [{"name": "tests", "passed": true}], "errors": []})`

Each check must be an object with a boolean `passed`. Invalid entries are ignored and counted in metadata. Missing/empty valid checks never count as success.

## Metrics

- `score`: passed valid checks / total valid checks (0–1).
- `passed`: all valid checks passed, at least one valid check exists, and no execution errors were reported.
- `failures`: names of failed checks.
- `errors`: execution errors from the input.
- `metadata.invalid_checks_ignored`: malformed entries excluded from the denominator.

A score of 1.0 with execution errors is still a failed evaluation. This is a baseline signal, not proof of semantic correctness; task-specific acceptance tests remain essential.
