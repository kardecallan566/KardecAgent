# KardecAgent

O **KardecAgent** é um agente local para desenvolvimento de software. Ele recebe uma tarefa em linguagem natural, analisa o projeto, propõe um plano e, somente após a aprovação do usuário, executa mudanças controladas no diretório do projeto.

O agente foi desenvolvido para trabalhar com modelos locais via [Ollama](https://ollama.com/), mantendo o código, o histórico da execução e os arquivos do usuário no ambiente local.

## Principais capacidades

- Detecta a linguagem, o framework e os comandos disponíveis no projeto.
- Cria um plano de execução antes de modificar arquivos.
- Executa operações de leitura, escrita, patch, comandos permitidos e verificações.
- Divide planos grandes em subtarefas sequenciais com escopo definido.
- Persiste tarefas aprovadas e permite retomar execuções interrompidas.
- Mantém checkpoints Git e realiza recuperação com rollback seguro quando necessário.
- Aplica controles adicionais para tarefas que envolvem credenciais, autenticação, permissões ou outros dados sensíveis.
- Consulta fontes externas por meio de ferramentas de busca e leitura com controles de segurança.

## Requisitos

- Python 3.11 ou superior.
- [Ollama](https://ollama.com/) em execução local.
- Um modelo compatível instalado no Ollama, como `qwen2.5-coder:3b`.

## Instalação

No Windows PowerShell:

```powershell
cd D:/caminho/para/KardecAgent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

No Linux ou macOS:

```bash
cd /caminho/para/KardecAgent
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Baixe o modelo escolhido e confirme que o Ollama está disponível:

```bash
ollama pull qwen2.5-coder:3b
ollama serve
```

O padrão do projeto é `http://127.0.0.1:11434` com o modelo `qwen2.5-coder:3b`. Para alterar esses valores, use `KARDECAGENT_OLLAMA_BASE_URL` e `KARDECAGENT_LLM_MODEL`.

## Uso básico

Verifique o ambiente sem alterar arquivos:

```bash
kardec-agent doctor --project /caminho/para/seu-projeto
```

Execute uma tarefa:

```bash
kardec-agent run \
  --project /caminho/para/seu-projeto \
  --task "adicione um teste simples para validar o projeto"
```

O agente exibirá o plano e aguardará a aprovação. Sem aprovação explícita, nenhuma implementação será iniciada.

Para listar tarefas persistidas:

```bash
kardec-agent tasks --project /caminho/para/seu-projeto
```

Para retomar uma tarefa aprovada que foi interrompida ou atingiu o limite de iterações:

```bash
kardec-agent resume \
  --project /caminho/para/seu-projeto \
  --task "adicione um teste simples para validar o projeto"
```

A retomada reutiliza o plano aprovado e o estado persistido. Ela não cria uma nova aprovação.

## Testes

Instale as dependências de desenvolvimento e execute:

```bash
pytest -q
```

A suíte cobre planejamento, aprovação, execução de ferramentas, escopo de subtarefas, persistência, retomada, recuperação, rollback, segurança e comandos da CLI.

Também existe um projeto mínimo em `examples/first-version-fixture` para validar o fluxo completo:

```bash
kardec-agent doctor --project examples/first-version-fixture
kardec-agent run \
  --project examples/first-version-fixture \
  --task "crie feature.py com uma constante VALUE igual a 42"
```

## Estrutura resumida

- `src/kardecagent/`: código do agente e da CLI.
- `tests/`: testes automatizados.
- `examples/`: projetos de exemplo para validação.
- `.kardecagent/tasks/`: estado local de tarefas persistidas dentro do projeto analisado.

## Segurança e limites

O usuário mantém o controle sobre o início da implementação. A execução usa escopos aprovados, comandos controlados e validações de conclusão. Tarefas sensíveis recebem requisitos adicionais e podem exigir uma segunda aprovação.

O estado persistido ajuda a retomar uma execução, mas não substitui Git, backups ou revisão profissional de segurança.

## Licença

Este projeto é distribuído sob a licença MIT.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<!-- O projeto usa a licença declarada em pyproject.toml. -->
