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
- [ ] Language detection
- [ ] Context extraction

### Terminal
- [x] Project-root execution
- [x] Timeout
- [x] Output limits
- [x] Basic dangerous-command protection
- [ ] Configurable allowlist
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
- [ ] Completion detection
- [ ] Retry/recovery
- [ ] Test-failure feedback loop

### Tests
- [x] Configuration
- [x] Root security
- [x] Filesystem
- [x] Search
- [x] Terminal policy
- [x] Agent state
- [ ] Mocked end-to-end LLM test
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
- [ ] Structured tool schema
- [ ] Tool-call validation
- [ ] Patch-based editing
- [ ] File-change verification
- [x] Automatic test command discovery
- [x] Typecheck/lint/build detection
- [ ] Test-result parsing
- [ ] Error summarization
- [ ] Git status/diff
- [ ] Pre-task checkpoint
- [ ] Post-task checkpoint
- [ ] Rollback
- [ ] Task interruption/resume
- [ ] Persistent task logs

## M2 — Planning & Verification
- [ ] Explicit planning phase
- [ ] Stored plan
- [ ] Plan progress
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
