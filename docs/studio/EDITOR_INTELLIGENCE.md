# Studio editor intelligence

The current Studio editor remains lightweight and dependency-free. It adds local completions and actionable diagnostics on top of the existing syntax-highlighted editing surface.

## Completion behavior

- Suggestions appear after typing at least two identifier characters.
- `Ctrl+Space` opens suggestions explicitly.
- `ArrowUp` / `ArrowDown` changes the selected suggestion.
- `Tab` or `Enter` accepts the selected suggestion; `Escape` closes the list.
- Suggestions include common language keywords for Python, JavaScript/TypeScript, Rust and Go, declarations found in the open file, and names of files in the current project.
- Completion is local and deterministic; it does not call an LLM or transmit source code.

This is an initial lexical completion provider, not a full language server. It does not yet resolve imports, types, overloads, project-wide symbols, or semantic references.

## Diagnostics

- JSON files are parsed while editing; syntax errors report a line and column immediately.
- The existing Ruff and ESLint actions are parsed into navigable diagnostics when their output includes line/column data.
- The Problems panel groups diagnostics with severity, source, code and location. Selecting a diagnostic moves the editor cursor to that position.
- Diagnostics from a previous file version are cleared when the source changes, so stale findings are not presented as current.
- Formatting and lint fixes remain explicit user actions; the editor never applies automatic fixes in the background.

## Next language-service work

A full LSP integration is still a separate milestone. It should use project-local tool discovery, explicit user settings, bounded subprocess lifetimes, cancellation and diagnostics versioning before enabling semantic completion or background analysis.

## Rename Symbol semântico

O editor Python oferece Rename Symbol com Jedi por meio de `F2`:

- O arquivo ativo precisa estar salvo antes da análise, evitando misturar buffer não persistido com o conteúdo real do workspace.
- Jedi calcula as referências e produz uma prévia multi-arquivo; o editor mostra os caminhos afetados e o diff antes de solicitar confirmação.
- A aplicação exige uma confirmação explícita. Cancelar descarta a prévia sem gravar arquivos.
- O Core limita a prévia a dez operações pendentes, valida identificadores Python e restringe todos os caminhos ao workspace.
- Cada arquivo recebe um SHA-256 durante a prévia. Se qualquer arquivo mudar antes da confirmação, a aplicação é recusada e uma nova prévia deve ser gerada.
- Os arquivos são preparados em temporários no mesmo diretório e substituídos somente depois das validações. Em caso de falha durante a substituição, o Core tenta restaurar os arquivos já substituídos.
- O recurso é exclusivo para arquivos Python e depende do extra opcional `studio` (Jedi).

Atalho: posicione o cursor sobre o nome do símbolo e pressione `F2`. Use `Esc`/Cancelar na confirmação do sistema para não aplicar a alteração.
