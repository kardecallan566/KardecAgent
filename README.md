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

Independent security review and dependency vulnerability analysis are also enforced for sensitive/high-risk completion. Stronger secret scanning remains a future hardening layer.


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
\n\n### Terminal Safety\n\nTerminal execution does not invoke a shell. Commands are parsed into an argv list, shell operators such as `&&`, `||`, `;`, pipes, redirection, and cmd escaping are rejected outside quoted arguments, and the executable must pass the configured development-command allowlist. Shell executables such as PowerShell, cmd, bash, and WSL are not allowed through the terminal tool. Timeouts and bounded stdout/stderr remain enabled.\n

## Logical Subtasks

Large approved plans can be decomposed into logical subtasks after the single parent approval gate. Subtasks reuse the same loaded LLM instead of spawning additional model processes.

The flow is:

1. Create the parent plan.
2. Ask the user for explicit approval.
3. Create the post-approval Git checkpoint.
4. Decompose only when the task/project is large enough.
5. Validate dependencies, plan-step references, and project-relative scopes.
6. Execute subtasks sequentially with inherited parent context.
7. Prevent subtasks from changing the parent plan.
8. Verify the complete project after all subtasks finish.

This keeps the initial implementation simple and avoids loading multiple 27B models into memory. Parallel execution is intentionally deferred until the sequential orchestration path is reliable.

Each subtask receives:
- parent task and approved parent plan;
- its own objective and completion criteria;
- the approved parent plan steps it is allowed to implement;
- an explicit project-relative scope;
- the shared project context.

A subtask never receives a new approval gate. If it needs work outside the approved parent plan, the parent execution must stop and request a new plan approval.


### Scoped command side-effect remediation

When a logical subtask runs a command, KardecAgent records the pre-command working-tree state and detects files changed by the command. If a command changes a path outside the subtask scope, execution is rejected and the agent attempts safe automatic remediation.

Remediation is deliberately conservative:
- pre-existing files are restored byte-for-byte from the pre-command snapshot, preserving user changes;
- deleted pre-existing files are recreated from their exact snapshot;
- newly created regular files inside the project root are removed;
- paths involving symlinks, special files, or Git rename/copy operations are not automatically reverted and are reported as remediation failures;
- remediation never uses `git reset --hard` or `git checkout`, so it cannot discard unrelated user work.

Scoped command remediation currently applies to Git repositories, where tracked files and pre-existing working-tree changes are snapshotted before the command.

### Persistent tasks and resume

Approved tasks are persisted locally under `.kardecagent/tasks/` using atomic JSON writes plus an append-only, hash-chained execution journal. The persisted record contains the approved plan, execution events, current status and, for decomposed work, the logical subtask board. The journal is authoritative when it is ahead of the snapshot, including the execution cursor: active parent plan step, active subtask and that subtask's current plan step.

Subtask lifecycle events persist a board snapshot and cursor metadata, so a crash between snapshot writes does not lose which logical subtask was active. If execution is interrupted while a subtask is running, that subtask is returned to `pending` when the state is loaded, because its completion was not durably recorded; the recovered cursor still identifies the interrupted subtask and exact subtask step for resume. Completed subtasks remain completed and their dependencies are preserved. Resume therefore prioritizes the journal's active subtask and starts its approved subplan at the recovered step, without creating a new plan or approval gate.

Use the CLI with:

```powershell
kardec-agent resume --project D:\path\to\project --task "implement feature"
```

The persistence file is written atomically and remains inside the project. It should be treated as local execution state, not as a substitute for Git history or backups.


### Formal execution state machine

Task execution is controller-owned and follows a validated state machine. Direct model actions cannot choose task status transitions.

Normal completion flow:

`PENDING -> RUNNING -> VERIFYING -> VERIFIED -> COMPLETED`

Failure and recovery flow:

`RUNNING -> FAILED/MAX_ITERATIONS -> RECOVERING -> RESUMING -> RUNNING`

Recovery may instead end in `FAILED` when workspace conflicts, invalid audit data, or rollback errors prevent a safe resume. Invalid transitions are rejected by `TaskState.transition()`, and every valid transition is recorded as a `status_changed` event for persistence and auditability.

Recovered executions resume only from the last consistently completed approved plan step. Subtasks use the same state machine and can perform one deterministic retry after audited changes are safely rolled back.


## Primeira versão testável

A primeira versão pode ser validada localmente sem modificar o projeto usando os comandos abaixo.

### 1. Instalar

```powershell
cd D:/caminho/para/KardecAgent
python -m venv .venv
./.venv/Scripts/Activate.ps1
pip install -e ".[dev]"
```

### 2. Verificar ambiente

```powershell
kardec-agent doctor --project D:/caminho/para/seu-projeto
```

O `doctor` verifica o projeto detectado e faz uma chamada mínima ao servidor Ollama local. Ele não executa ferramentas de implementação nem altera arquivos.

O padrão do runtime é `http://127.0.0.1:11434` e `qwen2.5-coder:3b`. O modelo pode ser alterado com `KARDECAGENT_LLM_MODEL` e o endereço do Ollama com `KARDECAGENT_OLLAMA_BASE_URL`.

### 3. Executar uma tarefa real

```powershell
kardec-agent run --project D:/caminho/para/seu-projeto --task "adicione um teste simples para validar o projeto"
```

O agente cria o plano primeiro e mostra o plano para aprovação. **Sem aprovação explícita, nenhuma execução de implementação começa.**

### 4. Inspecionar tarefas persistidas

```powershell
kardec-agent tasks --project D:/caminho/para/seu-projeto
```

### 5. Retomar uma execução interrompida

```powershell
kardec-agent resume --project D:/caminho/para/seu-projeto --task "adicione um teste simples para validar o projeto"
```

O resume reutiliza o plano aprovado e o journal persistido; não cria uma nova aprovação. Se uma recuperação estava em andamento quando o processo caiu, a transação de recovery é retomada antes da execução continuar.

### 6. Rodar a suíte automatizada

```powershell
pytest -q
```

A suíte cobre planejamento/aprovação, execução de ferramentas, escopo de subtarefas, persistência/journal, recuperação, rollback seguro e comandos básicos da CLI.


## Smoke test da primeira versão

Além da suíte automatizada, o repositório contém um projeto mínimo em `examples/first-version-fixture` para validar o fluxo completo contra um projeto real no disco.

1. Instale o KardecAgent no ambiente virtual:

```powershell
pip install -e ".[dev]"
```

2. Verifique o projeto e a conexão com o modelo local:

```powershell
kardec-agent doctor --project examples/first-version-fixture
```

3. Execute uma tarefa. O agente deve primeiro mostrar o plano e aguardar sua aprovação antes de alterar qualquer arquivo:

```powershell
kardec-agent run --project examples/first-version-fixture --task "crie feature.py com uma constante VALUE igual a 42"
```

4. Para listar tarefas persistidas:

```powershell
kardec-agent tasks --project examples/first-version-fixture
```

5. Para continuar uma tarefa persistida interrompida:

```powershell
kardec-agent resume --project examples/first-version-fixture --task "crie feature.py com uma constante VALUE igual a 42"
```

A suíte `tests/test_first_version_integration.py` também cobre, com um LLM determinístico, aprovação antes da execução, alteração real de arquivos, verificação, rollback de mudanças não concluídas e retomada a partir do próximo passo aprovado.
