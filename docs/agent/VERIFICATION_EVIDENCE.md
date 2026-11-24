# Completion verification and evidence

The agent may finish an approved plan only after:

1. Every approved plan step is complete.
2. It supplies exactly one non-empty evidence item for every approved completion criterion.
3. The existing automated verification runner reports at least one available check and no failed available checks.
4. Sensitive and high-risk plans also pass the independent security review.

## Criterion-linked evidence

New tool calls should use explicit objects:

```json
{
  "tool": "finish",
  "arguments": {
    "reason": "The approved work is complete.",
    "criteria_evidence": [
      {
        "criterion": "The application starts successfully",
        "evidence": "Started the application and confirmed the main window opened."
      }
    ]
  }
}
```

The criterion must match an approved completion criterion (case-insensitive), exactly once. Missing, duplicate, or unapproved criteria are rejected. Legacy lists of strings remain supported for compatibility; each string is matched to the corresponding criterion by plan order and the list must have exactly the same length.

Evidence supplied by the model is an auditable explanation, not independent proof by itself. Automated checks remain separate and are recorded with their availability and pass/fail/skip status. An unavailable check is not counted as a failure, but the task cannot be verified when every check is unavailable.

## Compatibility

- The existing check runner remains the single implementation for tests, type checks, lint, build, validation, security, and dependency audit.
- The report is additive: it includes a summary and normalized per-check status while retaining the existing check details.
- No task journal or snapshot schema changes are required.
