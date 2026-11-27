# Adaptive Intelligence Engine

The first milestone adds a deterministic decision layer that measures task
complexity, recommends decomposition and parallelism, ranks Skill metadata for
a work item, and proposes bounded recovery actions after failures.

## Safety and authority boundaries

- Assessment is local and deterministic; task content is not sent to a second classifier.
- Complexity is a recommendation, not permission to run tools or broaden scope.
- Skill ranking reads metadata only. SkillOrchestrator remains the only path for loading instructions and requires explicit approval.
- Model escalation is only a request. The existing model-switch approval callback remains mandatory; this engine cannot switch, download, or install models.
- Replanning must remain within the approved task scope. Verification repair is restricted to that scope.
- No autonomous self-modification, dependency installation, or external code execution is introduced.

## Signals and outputs

Assessment combines task-language signals, approved plan step count, and project
snapshot size. It returns a score, low/medium/high level, factors, risk
indicators, decomposition recommendation, and bounded parallelism (1–4).
It does not infer hardware capacity, energy use, token usage, or model context
windows when those values are not measured.

Failure recommendations distinguish repeated lack of progress, verification
failure, provider/context/capability limits, tool/protocol errors, and
insufficient evidence. Skill ranking is metadata-only; the current
consent-gated SkillOrchestrator continues to enforce activation and dependency
fingerprints.

## Next integration milestone

Wire assessment into task and subtask lifecycle events; use per-subtask Skill
ranking before the existing approval gate; surface reasons and measured
evidence in Studio; then add end-to-end tests for approval, refusal, replanning,
and model escalation. This PR establishes the isolated decision core before
changing execution behavior.
