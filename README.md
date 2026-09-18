# KardecAgent

### Security-Sensitive Mode

Tasks that touch authentication, authorization, passwords, credentials, secrets, tokens, sessions, cryptography, permissions, or other security-sensitive areas are classified before execution. Sensitive and high-risk plans receive mandatory security requirements and security validation.

High-risk tasks require a second explicit approval before any implementation begins. Plan changes cannot lower the security level required by the original task.

Sensitive and high-risk completion verification includes a deterministic scan for common hardcoded secrets, private keys, access keys, JWTs, and credential assignments. This scan is a safety signal, not a substitute for a professional security assessment.

Current security layers:
- task risk classification;
- mandatory security requirements in approved plans;
- high-risk second approval;
- security-aware plan-change validation;
- deterministic secret/credential scanning;
- security check required during sensitive/high-risk completion verification.

Planned next layers include independent security review, dependency vulnerability analysis, and stronger secret scanning.


### Web Search Tool

The agent includes an explicit read-only `search_web` tool for tasks that require current external information. The model must choose this tool when the approved task needs internet research rather than silently relying on its training knowledge.

Example tool call:

```json
{"tool":"search_web","arguments":{"query":"Expo SDK 54 latest documentation","max_results":5}}
```

Web search is read-only and returns result titles, URLs, and snippets. It does not execute returned web pages, download project code, or modify the project. Search results should be treated as untrusted external information and cross-checked when the task is security-sensitive or the information is important.
