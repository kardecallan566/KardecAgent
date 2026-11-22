# Suporte a linguagens e qualidade de código

O Studio identifica a linguagem principalmente pela extensão do arquivo e usa os manifests do projeto para reconhecer stacks e workspaces mistos. A identificação não instala runtimes, compiladores ou linters.

## Detecção de projetos

A análise reconhece, entre outras, Python, JavaScript, TypeScript, Rust, Go, Java, Kotlin, C#, C, C++, PHP, Ruby, Swift, Dart, Scala, Lua, Shell, HTML, CSS, SQL, Vue, Svelte e Markdown. Manifestos como `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `composer.json`, `Gemfile`, `pubspec.yaml` e `CMakeLists.txt` ajudam a identificar frameworks, comandos e linguagens adicionais.

## Executar arquivo

O comando **Executar** usa o runtime/compilador instalado no computador. O suporte integrado inclui Python, JavaScript, TypeScript (Node compatível), JSX/TSX (com `tsx` instalado no projeto), Java, Go, Rust, PHP, Ruby, C, C++, Kotlin, Swift, Dart, Lua, Shell, Perl, R, Scala, Elixir e PowerShell.

A execução continua protegida por confirmação: o programa pode acessar arquivos e recursos disponíveis ao usuário do sistema. Arquivos de cabeçalho, configurações, HTML e SQL não são tratados como programas independentes.

## Lint e correção

- Python: Ruff (`ruff check` / `ruff check --fix`).
- JavaScript e TypeScript: ESLint.
- Go: `gofmt` (diagnóstico por diff e correção com `-w`).
- Rust: `rustfmt --check` / `rustfmt`.
- C e C++: `clang-format`.
- Java: Google Java Format.
- PHP: `php -l` / PHP-CS-Fixer.
- Ruby: RuboCop.
- Shell: ShellCheck / shfmt.
- HTML, CSS, JSON, YAML e Markdown: Prettier.

O Studio procura ESLint e Prettier locais em `node_modules` e Ruff em `.venv` antes de usar ferramentas no PATH. Se a ferramenta não estiver instalada, a saída informa o que falta; o Studio não baixa dependências automaticamente.

**Lint** apenas analisa o arquivo aberto. **Corrigir** pede confirmação e só então executa o modo de correção/formatador. O resultado aparece no painel Output. A IDE não altera silenciosamente arquivos durante a análise.
