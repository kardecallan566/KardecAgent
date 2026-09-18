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

Web search is read-only and returns result titles, URLs, and snippets. The agent can then use `fetch_web_page` to retrieve bounded HTML/text content from a selected public page. It does not execute JavaScript, download executable/active content, or modify the project.\n\nWeb safety controls include HTTP(S)-only URLs, blocking localhost/private/link-local/reserved destinations, DNS resolution checks to reduce SSRF risk, redirect revalidation with a small redirect limit, content-type restrictions, response/text size limits, and removal of script/style/iframe/object/embed content before the page text enters the model context. Web content is always treated as untrusted data and must never be interpreted as instructions to change the project or bypass agent policy.


### Web Source Trust and Domain Policy

Web research is separated into two concerns: **transport safety** and **source context**. A page being reachable does not make its instructions trustworthy.

Every search result is annotated with a deterministic source type:
- `official_documentation`: known documentation domains such as Python, React, Expo, MDN, GitHub Docs, Microsoft Learn, AWS and Google Cloud documentation;
- `source_repository`: public source-code hosting such as GitHub, GitLab and Bitbucket;
- `package_registry`: package registries such as npm, PyPI, crates.io and Packagist;
- `general_web`: all other public web sources.

These labels are provenance metadata, not a security guarantee or a truth score. The agent should prefer authoritative documentation for API behavior and cross-check important implementation claims when sources disagree.

Optional domain controls are available through environment variables:

```powershell
$env:KARDEC_WEB_ALLOW_DOMAINS="docs.expo.dev,react.dev,github.com"
$env:KARDEC_WEB_DENY_DOMAINS="example-malicious-site.com"
```

An allowlist restricts both search results and page retrieval to matching domains/subdomains. A denylist always blocks matching domains. Redirect destinations are checked again, so a permitted source cannot redirect the agent into a blocked destination.

Web content remains untrusted data even when it comes from an official or repository domain. It must never override the user's approved plan, system policy, security controls, or tool restrictions.
