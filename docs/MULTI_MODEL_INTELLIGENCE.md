# Multi-Model Intelligence

The capability catalog inventories model clients and proposes a model for each
logical subtask using deterministic matching against explicitly declared
provider metadata.

## Capability metadata

Clients may expose a `model_capabilities` or `capabilities` dictionary with:
- `strengths`: coding, reasoning, testing, review, documentation, vision
- `context_window`: positive integer
- `tool_calling`: boolean
- `reasoning_level`: basic, standard, advanced, expert
- `latency_tier`: fast, balanced, slow

Missing or invalid values remain unknown. The catalog does not infer capability
from a model name or claim that a model supports tools/context sizes without
provider evidence.

## Routing and safety

The orchestrator records a recommendation for each subtask in the task journal.
This first increment is advisory: it does not instantiate clients, change the
active model, send task data to another provider, or start extra inference.
The configured primary client continues execution. Any future model switch
must use the existing explicit model-switch approval callback. Recommendations
carry `requires_approval=true` and are telemetry, not authorization.

## Validation

Unit tests cover metadata normalization, capability matching, unknown metadata,
unavailable clients, and invalid provider declarations.
