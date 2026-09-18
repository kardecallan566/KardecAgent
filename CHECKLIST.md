# KardecAgent — Implementation Checklist

Source of truth for implementation progress.

Legend: [ ] Not started | [~] In progress | [x] Completed | [!] Blocked

## M0 — Foundation

### Repository
- [x] Create GitHub repository
- [x] Add project README
- [x] Add Python .gitignore
- [x] Define architecture and principles
- [x] Create implementation checklist

### Python foundation
- [x] Create pyproject.toml
- [x] Create package structure
- [x] Add CLI entry point
- [x] Add configuration
- [x] Add logging
- [x] Add initial tests

### LLM
- [x] OpenAI-compatible client
- [x] Configurable base URL/model/API key
- [x] Timeout
- [x] Response normalization
- [x] Structured tool-call parsing
- [x] Malformed-response recovery
- [ ] Streaming

### Project tools
- [x] Project-root validation
- [x] Filesystem escape protection
- [x] Project scanner
- [x] Ignore generated/dependency directories
- [x] Read/write files
- [x] Text search
- [ ] Symbol-aware search
- [x] Language detection
- [ ] Context extraction

### Terminal
- [x] Project-root execution
- [x] Timeout
- [x] Output limits
- [x] Basic dangerous-command protection
- [x] Configurable allowlist
- [ ] Process cancellation
- [ ] Resource limits
- [ ] Interactive commands

### Agent loop
- [x] Task state
- [x] Execution events
- [x] Observe/decide/act loop skeleton
- [x] Iteration limit
- [x] Tool dispatch
- [x] Structured execution results
- [x] Execution logging
- [x] Real model-driven tool selection
- [x] Completion detection
- [x] Retry/recovery
- [x] Test-failure feedback loop

### Tests
- [x] Configuration
- [x] Root security
- [x] Filesystem
- [x] Search
- [x] Terminal policy
- [x] Agent state
- [x] Mocked end-to-end LLM test
- [ ] Real local-model end-to-end test

### M0 exit criteria
- [ ] Contact local model
- [ ] Inspect real project
- [ ] Modify real project
- [ ] Run project tests
- [ ] Interpret failures
- [ ] Correct failures
- [ ] Repeat until tests pass
- [ ] Stop at configurable maximum iterations
- [ ] Produce final execution report

## M1 — Reliable Coding Loop
- [x] Structured tool schema
- [x] Tool-call validation
- [ ] Patch-based editing
- [ ] File-change verification
- [x] Automatic test command discovery
- [x] Typecheck/lint/build detection
- [ ] Test-result parsing
- [ ] Error summarization
- [x] Git status/diff
- [x] Pre-task checkpoint (wired into the agent loop)
- [ ] Post-task checkpoint
- [ ] Rollback (safety/confirmation layer required before exposure)
- [ ] Task interruption/resume
- [ ] Persistent task logs

## M2 — Planning & Verification
- [x] Explicit planning phase
- [x] Structured execution plan
- [x] Human approval gate before implementation
- [x] Approved plan passed to execution context
- [ ] Stored plan
- [x] Plan progress
- [ ] Completion criteria
- [ ] Reviewer phase
- [ ] Reviewer findings
- [ ] Reviewer → coder feedback
- [ ] Final verification
- [ ] Final report

## M3 — Multi-Agent
- [ ] Planner role
- [ ] Coder role
- [ ] Tester role
- [ ] Reviewer role
- [ ] Shared task context
- [ ] Role prompts
- [ ] Agent handoffs
- [ ] Failure routing
- [ ] Context/execution tracking

## M4 — Persistent Memory
- [ ] Project profile
- [ ] Architecture memory
- [ ] Coding conventions
- [ ] Decisions
- [ ] Known issues
- [ ] Task history
- [ ] Memory retrieval/update/validation

## M5 — Developer Interfaces
- [ ] Local HTTP API
- [ ] Web dashboard
- [ ] Live events
- [ ] Task queue/cancellation
- [ ] Project selector
- [ ] Git diff viewer
- [ ] VS Code extension

## M6 — Advanced Autonomy
- [ ] Context ranking
- [ ] Model routing
- [ ] Context compression
- [ ] Failure classification
- [ ] Recovery strategies
- [ ] Evaluation benchmarks
- [ ] Long-running task recovery
- [ ] Safe parallel tasks
- [ ] Human approval gates

## Early-version non-goals
- [ ] Never give the model unrestricted OS access
- [ ] Avoid cloud LLM dependency
- [ ] Avoid complex UI before the core loop is reliable
- [ ] Avoid multi-agent complexity before single-agent reliability
- [ ] Avoid premature inference optimization


## M2 implementation notes

- [x] Plan tracker with sequential active-step state.
- [x] Mutating tool calls require a plan step.
- [x] Agent rejects actions outside the active approved step.
- [x] Agent requires explicit step completion with evidence.
- [x] Agent rejects finalization until all approved steps are completed.
- [x] Git checkpoint moved to after explicit plan approval.
- [ ] Plan deviation flow with a second user approval gate.
