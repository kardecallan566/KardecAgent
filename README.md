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
