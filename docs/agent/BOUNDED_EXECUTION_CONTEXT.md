# Bounded execution context

Long agent tasks can accumulate many assistant/tool turns. The execution loop now bounds the conversation sent to the model so older turns do not grow without limit.

## Retention policy

- The initial system instructions and approved task context are always retained.
- The 18 most recent conversation messages are retained verbatim.
- Older turns are replaced by a compact summary built from the execution journal.
- The summary includes current approved-plan progress and up to 10 recent evidence events (tool results, tool errors, verification failures and completed-step evidence).
- Individual evidence excerpts are capped at 900 characters and the serialized summary is capped at 10,000 characters.
- Compaction occurs only after the conversation exceeds 26 messages.

The summary explicitly tells the model to re-read project files when exact older contents are needed. It is not treated as a source of truth for file contents or a replacement for the execution journal.

## Safety and behavior

Compaction changes only the in-memory prompt sent to the model. It does not delete, rewrite or truncate persisted task events, tool outputs, user messages or project files. The approved plan, plan-step tracker, workspace scope, approval gates and verification rules remain unchanged.

This is a bounded-context safeguard, not a guarantee against every model-provider context limit. The original task context can itself be large, and provider-specific token limits still apply.
