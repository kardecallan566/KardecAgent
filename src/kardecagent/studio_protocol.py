from __future__ import annotations

import json
import re
import sys
import threading
import uuid
from pathlib import Path
from datetime import datetime, timezone

from .agent.loop import AgentLoop
from .agent.plan import ExecutionPlan
from .agent.state import AgentEvent
from .agent.persistence import PersistenceError, TaskStore, task_id
from .studio.language import python_semantic_request
from .studio.code_actions import python_code_actions
from .studio.refactor import RenamePreviewManager


MAX_MESSAGE_BYTES = 64 * 1024
MAX_SEMANTIC_MESSAGE_BYTES = 6_200_000
MAX_EVENT_BYTES = 256 * 1024
MAX_INSTRUCTION_LENGTH = 8000
MAX_SEEN_COMMAND_IDS = 1000
RUNTIME_HEARTBEAT_INTERVAL_SECONDS = 5.0
_WRITE_LOCK = threading.Lock()


class StudioProtocolError(ValueError):
    pass


_PT_MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def _local_date_context() -> dict[str, str]:
    now = datetime.now().astimezone()
    return {
        "iso_datetime": now.isoformat(timespec="seconds"),
        "date_pt_br": f"{now.day:02d} de {_PT_MONTHS[now.month - 1]} de {now.year}",
        "timezone": now.tzname() or "fuso horário local",
    }


def _repair_mojibake(value: str) -> str:
    """Repair repeated UTF-8/Windows-1252 decoding errors, including mixed text."""
    markers = ("Ã", "Â", "â€", "ðŸ", "Æ’", "â‚¬")
    proper_accents = set("áàãéêíóôõúüçÁÀÉÊÍÓÔÕÚÜÇ")
    
    def repair_fragment(fragment: str) -> str:
        current = fragment
        for _ in range(6):
            score = sum(current.count(marker) for marker in markers)
            if not score:
                break
            try:
                candidate = current.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                fragments: list[str] = []
                pending: list[str] = []
                for char in current:
                    if char in proper_accents:
                        if pending:
                            fragments.append(repair_fragment("".join(pending)))
                            pending = []
                        fragments.append(char)
                    else:
                        pending.append(char)
                if pending:
                    fragments.append(repair_fragment("".join(pending)))
                return "".join(fragments)
            if sum(candidate.count(marker) for marker in markers) >= score:
                break
            current = candidate
        return current

    # Preserve correctly decoded Portuguese accents as boundaries so one
    # already-correct character cannot block repair of adjacent corrupted text.
    pieces: list[str] = []
    fragment = []
    for char in value:
        if char in proper_accents:
            if fragment:
                pieces.append(repair_fragment("".join(fragment)))
                fragment = []
            pieces.append(char)
        else:
            fragment.append(char)
    if fragment:
        pieces.append(repair_fragment("".join(fragment)))
    return "".join(pieces)


def _is_date_question(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    return bool(re.search(r"\b(?:que dia [eé] hoje|qual [eé] a data de hoje|data de hoje|dia de hoje|que data [eé] hoje)\b", normalized))


def _project_inventory_reply(project_files: list[str], inspected: list[dict]) -> str:
    if not project_files:
        return "A pasta do workspace está acessível, mas não encontrei arquivos nela."
    top_dirs = sorted({path.split("/", 1)[0] for path in project_files if "/" in path})
    root_files = [path for path in project_files if "/" not in path]
    extensions: dict[str, int] = {}
    for path in project_files:
        suffix = Path(path).suffix.lower() or "(sem extensão)"
        extensions[suffix] = extensions.get(suffix, 0) + 1
    common = sorted(extensions.items(), key=lambda item: (-item[1], item[0]))[:8]
    lines = [f"Consegui acessar o workspace. Encontrei {len(project_files)} arquivos na listagem do projeto."]
    if top_dirs:
        lines.append("Pastas principais: " + ", ".join(top_dirs[:20]) + ".")
    if root_files:
        lines.append("Arquivos na raiz: " + ", ".join(root_files[:20]) + ".")
    if common:
        lines.append("Tipos de arquivo mais comuns: " + ", ".join(f"{ext}: {count}" for ext, count in common) + ".")
    readme = next((item for item in inspected if item.get("path", "").lower() == "readme.md" and isinstance(item.get("content"), str)), None)
    if readme:
        excerpt = " ".join(readme["content"].split())[:900]
        if excerpt:
            lines.append("Resumo do README: " + excerpt)
    lines.append("Esse inventário foi obtido diretamente da pasta aberta no Studio.")
    return "\n".join(lines)


def _write(message: dict) -> None:
    encoded = json.dumps(message, ensure_ascii=True, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
        bounded = dict(message)
        payload = bounded.get("payload")
        if isinstance(payload, dict):
            payload = dict(payload)
            if "data" in payload:
                payload["data"] = {"truncated": True}
            for key in ("message", "summary", "result_summary"):
                if isinstance(payload.get(key), str):
                    payload[key] = payload[key][:4000]
            bounded["payload"] = payload
        if isinstance(bounded.get("message"), str):
            bounded["message"] = bounded["message"][:4000]
        encoded = json.dumps(bounded, ensure_ascii=True, separators=(",", ":"), default=str)
    # Heartbeats are emitted from a daemon thread while the main thread may be
    # blocked in an LLM request. Serialize writes so JSONL frames never interleave.
    with _WRITE_LOCK:
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()


def _read_message() -> dict:
    line = sys.stdin.readline(MAX_SEMANTIC_MESSAGE_BYTES + 1)
    if not line:
        raise EOFError("Studio disconnected while waiting for a command or approval.")
    encoded_size = len(line.encode("utf-8"))
    if encoded_size > MAX_SEMANTIC_MESSAGE_BYTES:
        while line and not line.endswith("\n"):
            line = sys.stdin.readline(MAX_SEMANTIC_MESSAGE_BYTES + 1)
        raise StudioProtocolError("Command exceeds the bounded 6 MiB protocol limit.")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        if encoded_size > MAX_MESSAGE_BYTES:
            raise StudioProtocolError("Command exceeds the 64 KiB protocol limit.") from exc
        raise StudioProtocolError("Command must be a valid JSON object.") from exc
    if not isinstance(message, dict):
        raise StudioProtocolError("Command must be a JSON object.")
    limit = MAX_SEMANTIC_MESSAGE_BYTES if message.get("type") in {"editor.semantic", "editor.rename.preview", "editor.code_actions"} else MAX_MESSAGE_BYTES
    if encoded_size > limit:
        raise StudioProtocolError("Command exceeds the 64 KiB protocol limit.")
    return message



def _approval(
    task_identifier: str,
    execution_identifier: str,
    plan: ExecutionPlan,
    *,
    kind: str,
) -> bool:
    approval_identifier = str(uuid.uuid4())
    risk = "high" if plan.security_level == "high_risk" else (
        "medium" if plan.security_level == "sensitive" else "low"
    )
    _write({
        "type": "approval.requested",
        "schema_version": 1,
        "event_id": f"{task_identifier}:{execution_identifier}:approval:{uuid.uuid4()}",
        "execution_id": execution_identifier,
        "task_id": task_identifier,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "approval_id": approval_identifier,
            "kind": kind,
            "summary": plan.format_for_review(),
            "risk": risk,
        },
    })

    while True:
        try:
            message = _read_message()
        except StudioProtocolError as exc:
            _write({"type": "command.rejected", "schema_version": 1, "reason": str(exc)})
            continue
        if message.get("schema_version") != 1 or message.get("type") != "approval.respond":
            _write({
                "type": "command.rejected",
                "schema_version": 1,
                "command_id": message.get("command_id"),
                "reason": "Only the matching approval response is accepted while approval is pending.",
            })
            continue
        command_identifier = message.get("command_id")
        if not isinstance(command_identifier, str) or not command_identifier.strip():
            _write({"type": "command.rejected", "schema_version": 1, "reason": "command_id is required."})
            continue
        payload = message.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if payload.get("approval_id") != approval_identifier:
            _write({
                "type": "command.rejected",
                "schema_version": 1,
                "command_id": command_identifier,
                "reason": "Approval identifier does not match the pending request.",
            })
            continue
        decision = payload.get("decision")
        if decision not in {"approve", "reject"}:
            _write({
                "type": "command.rejected",
                "schema_version": 1,
                "command_id": command_identifier,
                "reason": "Decision must be approve or reject.",
            })
            continue
        _write({
            "type": "command.accepted",
            "schema_version": 1,
            "command_id": command_identifier,
            "accepted": True,
        })
        return decision == "approve"


def run_stdio(project_root: Path, agent: AgentLoop) -> int:
    """Serve a bounded JSONL command/event stream for the desktop Studio."""
    root = project_root.resolve()
    seen_command_ids: dict[str, None] = {}
    _write({
        "type": "runtime.ready",
        "schema_version": 1,
        "project_root": str(root),
        "limits": {
            "max_concurrent_tasks": 1,
            "max_subtasks": 6,
            "max_concurrent_subtasks": 1,
            "max_llm_iterations": agent.settings.max_iterations,
            "max_command_output_chars": agent.settings.max_command_output_chars,
            "max_project_snapshot_files": 500,
            "max_llm_timeout_seconds": agent.settings.llm_timeout_seconds,
            "max_command_timeout_seconds": agent.settings.command_timeout_seconds,
            "max_instruction_chars": MAX_INSTRUCTION_LENGTH,
            "max_event_bytes": MAX_EVENT_BYTES,
            "execution_mode": "sequential",
        },
    })

    heartbeat_stop = threading.Event()

    def emit_heartbeats() -> None:
        while not heartbeat_stop.wait(RUNTIME_HEARTBEAT_INTERVAL_SECONDS):
            try:
                _write({
                    "type": "runtime.heartbeat",
                    "schema_version": 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except (BrokenPipeError, OSError, ValueError):
                heartbeat_stop.set()
                return

    heartbeat_thread = threading.Thread(
        target=emit_heartbeats,
        name="kardecagent-studio-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()

    # Restore a bounded view of persisted work before accepting new commands.
    # Restored executions are historical UI records; they are never resumed
    # automatically and therefore cannot unexpectedly modify project files.
    try:
        persisted_tasks = TaskStore(root).list_recent(limit=20)
    except Exception as exc:
        persisted_tasks = []
        _write({"type": "runtime.warning", "schema_version": 1,
                "message": f"Could not restore task history: {exc}"})

    for persisted_id, persisted_state, _plan, _board in reversed(persisted_tasks):
        last_sequence = persisted_state.events[-1].sequence if persisted_state.events else 0
        restored_execution_id = f"restored:{persisted_id}:{last_sequence}"
        _write({
            "type": "task.created",
            "schema_version": 1,
            "event_id": f"{restored_execution_id}:created",
            "execution_id": restored_execution_id,
            "task_id": persisted_id,
            "timestamp": persisted_state.events[0].timestamp if persisted_state.events else datetime.now(timezone.utc).isoformat(),
            "payload": {"title": persisted_state.task, "status": "restored", "restored": True},
        })
        for event in persisted_state.events[-100:]:
            _write({
                "type": "core.event",
                "schema_version": 1,
                "event_id": f"{restored_execution_id}:{event.sequence}",
                "execution_id": restored_execution_id,
                "task_id": persisted_id,
                "timestamp": event.timestamp,
                "payload": {
                    "event_type": event.event_type,
                    "message": event.message,
                    "data": event.data,
                    "sequence": event.sequence,
                    "iteration": event.iteration,
                    "restored": True,
                },
            })

        if persisted_state.status.value == "completed":
            _write({
                "type": "task.completed",
                "schema_version": 1,
                "event_id": f"{restored_execution_id}:terminal",
                "execution_id": restored_execution_id,
                "task_id": persisted_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"result_summary": "Tarefa concluída e restaurada do histórico.", "restored": True},
            })
        else:
            interrupted = persisted_state.status.value in {"running", "verifying", "resuming", "recovering"}
            _write({
                "type": "task.failed",
                "schema_version": 1,
                "event_id": f"{restored_execution_id}:terminal",
                "execution_id": restored_execution_id,
                "task_id": persisted_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "message": "Esta é uma tarefa anterior que foi pausada quando o KardecAgent foi encerrado. Ela permanece disponível para recuperação; esta conversa não foi interrompida." if interrupted else f"Tarefa encerrada com estado {persisted_state.status.value}.",
                    "recoverable": persisted_state.status.value in {"running", "verifying", "resuming", "recovering", "failed", "max_iterations"},
                    "restored": True,
                },
            })

    while True:
        try:
            message = _read_message()
        except EOFError:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)
            return 0
        except StudioProtocolError as exc:
            _write({"type": "runtime.error", "schema_version": 1, "message": str(exc)})
            continue

        if message.get("schema_version") == 1 and message.get("type") == "chat.message":
            command_identifier = message.get("command_id")
            payload = message.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            text = payload.get("message")
            if not isinstance(command_identifier, str) or not command_identifier.strip():
                _write({"type": "command.rejected", "schema_version": 1, "reason": "command_id is required."})
                continue
            # Conversational messages are handled by this already workspace-bound
            # runtime process. The client path is not an authority for chat and may
            # differ in representation (case, separators, symlinks) on Windows.
            # Keep workspace validation on task/resume commands that can access files.
            if not isinstance(text, str) or not text.strip() or len(text) > 8000:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "message must contain 1-8000 characters."})
                continue
            if command_identifier in seen_command_ids:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "command_id has already been processed."})
                continue
            seen_command_ids[command_identifier] = None
            if len(seen_command_ids) > MAX_SEEN_COMMAND_IDS:
                del seen_command_ids[next(iter(seen_command_ids))]
            history = payload.get("history", [])
            if not isinstance(history, list):
                history = []
            safe_history = [{"role": item["role"], "content": item["content"][:4000]} for item in history[-16:] if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)]
            raw_editor = payload.get("active_editor")
            active_editor = None
            if isinstance(raw_editor, dict):
                editor_path = raw_editor.get("path")
                editor_content = raw_editor.get("content", "")
                if (
                    isinstance(editor_path, str) and editor_path.strip()
                    and not Path(editor_path).is_absolute()
                    and ".." not in Path(editor_path).parts
                    and isinstance(editor_content, str)
                ):
                    active_editor = {
                        "path": editor_path.replace(chr(92), "/")[:500],
                        "content": editor_content[:16000],
                        "dirty": bool(raw_editor.get("dirty", False)),
                    }
            _write({"type": "command.accepted", "schema_version": 1, "command_id": command_identifier, "accepted": True})
            try:
                # Conversational answers are workspace-aware too: inspect the actual
                # project tree and explicitly requested files before asking the model.
                from .tools.filesystem import ProjectFilesystem
                from .project.context import project_snapshot
                import re
                fs = ProjectFilesystem(root)
                project_files = project_snapshot(root, limit=250)
                if _is_date_question(text):
                    answer = "Hoje é " + _local_date_context()["date_pt_br"] + "."
                    _write({"type": "assistant.response", "schema_version": 1, "command_id": command_identifier, "payload": {"message": answer}})
                    continue
                requested_paths = re.findall(r"(?<![\w])(?:[\w.-]+[\\/])*[\w.-]+\.[A-Za-z0-9_-]{1,10}", text.strip())
                inspect_paths = list(dict.fromkeys(requested_paths))
                analysis_request = re.search(
                    r"\b(an[aá]lise|analisar|vis[aã]o geral|estrutura do projeto|como o projeto está organizado|o que tem (?:nesse|neste|no) projeto|oque tem (?:nesse|neste|no) projeto|me fale sobre (?:o )?projeto|resuma (?:o )?projeto)\b",
                    text,
                    re.IGNORECASE,
                )
                if analysis_request:
                    # A tree alone is insufficient to explain an application.
                    # Read metadata and likely entry points as evidence.
                    candidates = (
                        "README.md", "pyproject.toml", "package.json", "Cargo.toml",
                        "requirements.txt", "main.py", "app.py", "manage.py",
                        "src/main.py", "src/app.py", "src/__main__.py",
                        "src/kardecagent/__init__.py", "frontend/src/App.tsx",
                        "frontend/package.json", "index.js", "src/index.ts",
                    )
                    for candidate in candidates:
                        if candidate in project_files and candidate not in inspect_paths:
                            inspect_paths.append(candidate)
                inspected = []
                for relative in inspect_paths[:8]:
                    try:
                        content = fs.read_file(relative)
                        inspected.append({"path": relative, "content": content[:12000], "truncated": len(content) > 12000})
                    except (OSError, ValueError):
                        inspected.append({"path": relative, "error": "Arquivo não encontrado ou não pode ser lido dentro do workspace."})
                clock_context = _local_date_context()
                workspace_context = {
                    "current_local_datetime": clock_context,
                    "workspace_root": str(root),
                    "project_files": project_files,
                    "inspected_files": inspected,
                    "active_editor": active_editor,
                    "instruction": "Estes dados foram lidos diretamente do workspace ativo. Use-os como evidência; nunca diga que não tem acesso aos arquivos. Se um arquivo não estiver listado ou não puder ser lido, informe isso com precisão.",
                }
                if analysis_request:
                    answer = _project_inventory_reply(project_files, inspected)
                    _write({"type": "assistant.response", "schema_version": 1, "command_id": command_identifier, "payload": {"message": answer}})
                    continue

                access_only = (
                    re.search(r"\b(?:consegue|pode|tem como)\s+(?:acessar|ver|ler|abrir)\b", text, re.IGNORECASE)
                    and not re.search(r"\b(?:o que|explique|resuma|analise|mostre|conteúdo|conteudo)\b", text, re.IGNORECASE)
                )
                if access_only and inspected:
                    target = inspected[0]
                    if "error" in target:
                        answer = f"Não consegui ler o arquivo {target['path']} dentro do workspace ativo."
                    else:
                        answer = f"Sim. Consegui acessar {target['path']} no workspace ativo e ler seu conteúdo."
                    _write({"type": "assistant.response", "schema_version": 1, "command_id": command_identifier, "payload": {"message": answer}})
                    continue

                response = agent.llm.chat([
                    {"role": "system", "content": "Você é o KardecAgent, assistente técnico da IDE e está conectado ao workspace local. Você recebe abaixo uma listagem real de arquivos e, quando solicitado, conteúdo real dos arquivos. Use esse contexto para responder perguntas sobre o projeto. Se pedirem para ver um arquivo específico, baseie a resposta em inspected_files. Se perguntarem sobre o arquivo aberto no editor, use active_editor, que pode conter alterações ainda não salvas e tem precedência sobre a cópia em disco. Se pedirem análise do projeto, analise a árvore e os arquivos de configuração/README fornecidos; não invente conteúdo não inspecionado. Para perguntas sobre a data atual, use obrigatoriamente current_local_datetime.date_pt_br presente no contexto; nunca use datas de treinamento ou invente uma data. Responda em português quando o usuário usar português. Seja objetivo: no máximo 8 itens em listas, sem etapas repetidas. Esta é uma conversa, não execute alterações nem comandos e não invente ações. Para modificar arquivos, o usuário deve pedir a tarefa de implementação."},
                    {"role": "system", "content": "CONTEXTO REAL DO WORKSPACE (JSON): " + json.dumps(workspace_context, ensure_ascii=False)[:50000]},
                    *safe_history,
                    {"role": "user", "content": text.strip()},
                ], temperature=0.3)
                answer = _repair_mojibake(response.content.strip())
                if not answer:
                    raise RuntimeError("O modelo retornou uma resposta vazia.")
                _write({"type": "assistant.response", "schema_version": 1, "command_id": command_identifier, "payload": {"message": answer[:12000]}})
            except Exception as exc:
                reason = "timeout" if "timeout" in type(exc).__name__.lower() else "model_error"
                _write({"type": "chat.failed", "schema_version": 1, "command_id": command_identifier, "payload": {"reason": reason, "message": f"Falha na conversa ({reason}): {exc}", "retryable": reason == "timeout"}})
            continue
        if message.get("schema_version") == 1 and message.get("type") in {"editor.rename.preview", "editor.rename.apply", "editor.rename.cancel"}:
            command_identifier = message.get("command_id")
            payload = message.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            command_type = message.get("type")
            response_type = {
                "editor.rename.preview": "editor.rename.preview.response",
                "editor.rename.apply": "editor.rename.apply.response",
                "editor.rename.cancel": "editor.rename.cancel.response",
            }[command_type]
            if not isinstance(command_identifier, str) or not command_identifier.strip():
                _write({"type": response_type, "schema_version": 1, "payload": {"available": False, "message": "command_id is required."}})
                continue
            if command_identifier in seen_command_ids:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "command_id has already been processed."})
                continue
            seen_command_ids[command_identifier] = None
            if len(seen_command_ids) > MAX_SEEN_COMMAND_IDS:
                del seen_command_ids[next(iter(seen_command_ids))]
            try:
                if command_type == "editor.rename.preview":
                    requested_root = payload.get("project_root")
                    try:
                        workspace_matches = isinstance(requested_root, str) and Path(requested_root).resolve() == root
                    except (OSError, RuntimeError):
                        workspace_matches = False
                    if not workspace_matches:
                        raise ValueError("project_root does not match the active runtime workspace.")
                    result = rename_previews.preview(
                        str(root), payload.get("file_path"), payload.get("source"),
                        payload.get("line"), payload.get("column"), payload.get("new_name"),
                    )
                elif command_type == "editor.rename.apply":
                    result = rename_previews.apply(str(root), payload.get("preview_id", ""))
                else:
                    result = rename_previews.cancel(payload.get("preview_id", ""))
                _write({
                    "type": response_type,
                    "schema_version": 1,
                    "command_id": command_identifier,
                    "payload": {"available": True, **result},
                })
            except Exception as exc:
                _write({
                    "type": response_type,
                    "schema_version": 1,
                    "command_id": command_identifier,
                    "payload": {"available": False, "message": str(exc)[:1000]},
                })
            continue
        if message.get("schema_version") == 1 and message.get("type") == "editor.code_actions":
            command_identifier = message.get("command_id")
            payload = message.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            if not isinstance(command_identifier, str) or not command_identifier.strip():
                _write({"type": "editor.code_actions.response", "schema_version": 1, "payload": {"available": False, "actions": [], "message": "command_id is required."}})
                continue
            if command_identifier in seen_command_ids:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "command_id has already been processed."})
                continue
            seen_command_ids[command_identifier] = None
            if len(seen_command_ids) > MAX_SEEN_COMMAND_IDS:
                del seen_command_ids[next(iter(seen_command_ids))]
            try:
                requested_root = payload.get("project_root")
                try:
                    workspace_matches = isinstance(requested_root, str) and Path(requested_root).resolve() == root
                except (OSError, RuntimeError):
                    workspace_matches = False
                if not workspace_matches:
                    raise ValueError("project_root does not match the active runtime workspace.")
                result = python_code_actions(
                    payload.get("source"), payload.get("file_path"), str(root),
                    payload.get("line"), payload.get("column"),
                )
                _write({
                    "type": "editor.code_actions.response",
                    "schema_version": 1,
                    "command_id": command_identifier,
                    "payload": result,
                })
            except Exception as exc:
                _write({
                    "type": "editor.code_actions.response",
                    "schema_version": 1,
                    "command_id": command_identifier,
                    "payload": {"available": False, "actions": [], "message": str(exc)[:500]},
                })
            continue
        if message.get("schema_version") == 1 and message.get("type") == "editor.semantic":
            command_identifier = message.get("command_id")
            payload = message.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            if not isinstance(command_identifier, str) or not command_identifier.strip():
                _write({"type": "command.rejected", "schema_version": 1, "reason": "command_id is required."})
                continue
            if command_identifier in seen_command_ids:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "command_id has already been processed."})
                continue
            seen_command_ids[command_identifier] = None
            if len(seen_command_ids) > MAX_SEEN_COMMAND_IDS:
                del seen_command_ids[next(iter(seen_command_ids))]
            try:
                requested_root = payload.get("project_root")
                requested_file = payload.get("file_path")
                source = payload.get("source")
                action = payload.get("action")
                line = payload.get("line")
                column = payload.get("column")
                try:
                    workspace_matches = isinstance(requested_root, str) and Path(requested_root).resolve() == root
                except (OSError, RuntimeError):
                    workspace_matches = False
                if not workspace_matches:
                    raise ValueError("project_root does not match the active runtime workspace.")
                if not isinstance(requested_file, str) or not requested_file.strip():
                    raise ValueError("file_path is required.")
                if not isinstance(source, str):
                    raise ValueError("source must be text.")
                if len(source.encode("utf-8")) > 1_000_000:
                    raise ValueError("Source exceeds the 1 MB semantic-analysis limit.")
                if isinstance(line, bool) or not isinstance(line, int):
                    raise ValueError("line must be an integer.")
                if isinstance(column, bool) or not isinstance(column, int):
                    raise ValueError("column must be an integer.")
                result = python_semantic_request(
                    action, source, requested_file, str(root), line, column
                )
                _write({
                    "type": "editor.semantic.response",
                    "schema_version": 1,
                    "command_id": command_identifier,
                    "payload": result,
                })
            except Exception as exc:
                _write({
                    "type": "editor.semantic.response",
                    "schema_version": 1,
                    "command_id": command_identifier,
                    "payload": {
                        "available": False,
                        "action": action if isinstance(action, str) else "",
                        "items": [],
                        "message": str(exc)[:500],
                    },
                })
            continue
        if message.get("schema_version") == 1 and message.get("type") == "task.delete":
            command_identifier = message.get("command_id")
            payload = message.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            requested_root = payload.get("project_root")
            requested_task_id = payload.get("task_id")
            if not isinstance(command_identifier, str) or not command_identifier.strip():
                _write({"type": "command.rejected", "schema_version": 1, "reason": "command_id is required."})
                continue
            if command_identifier in seen_command_ids:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "command_id has already been processed."})
                continue
            try:
                workspace_matches = isinstance(requested_root, str) and Path(requested_root).resolve() == root
            except (OSError, RuntimeError):
                workspace_matches = False
            if not workspace_matches:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "project_root does not match the active runtime workspace."})
                continue
            if not isinstance(requested_task_id, str) or not requested_task_id.strip():
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "task_id is required."})
                continue
            seen_command_ids[command_identifier] = None
            if len(seen_command_ids) > MAX_SEEN_COMMAND_IDS:
                del seen_command_ids[next(iter(seen_command_ids))]
            try:
                deleted = TaskStore(root).delete_by_id(requested_task_id)
                if not deleted:
                    raise PersistenceError("Persisted task was not found in the workspace history.")
                _write({"type": "command.accepted", "schema_version": 1, "command_id": command_identifier, "accepted": True})
                _write({
                    "type": "task.deleted",
                    "schema_version": 1,
                    "event_id": f"{requested_task_id}:deleted:{command_identifier}",
                    "command_id": command_identifier,
                    "task_id": requested_task_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as exc:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": str(exc)[:500]})
            continue
        if message.get("schema_version") == 1 and message.get("type") == "task.resume":
            command_identifier = message.get("command_id")
            payload = message.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            requested_root = payload.get("project_root")
            requested_task_id = payload.get("task_id")
            if not isinstance(command_identifier, str) or not command_identifier.strip():
                _write({"type": "command.rejected", "schema_version": 1, "reason": "command_id is required."})
                continue
            if command_identifier in seen_command_ids:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "command_id has already been processed."})
                continue
            try:
                workspace_matches = isinstance(requested_root, str) and Path(requested_root).resolve() == root
            except (OSError, RuntimeError):
                workspace_matches = False
            if not workspace_matches:
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "project_root does not match the active runtime workspace."})
                continue
            if not isinstance(requested_task_id, str) or not requested_task_id.strip():
                _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "task_id is required."})
                continue
            seen_command_ids[command_identifier] = None
            if len(seen_command_ids) > MAX_SEEN_COMMAND_IDS:
                del seen_command_ids[next(iter(seen_command_ids))]

            execution_id = None
            command_accepted = False
            identifier = requested_task_id
            try:
                persisted = next(
                    (item for item in TaskStore(root).list_recent(limit=100) if item[0] == requested_task_id),
                    None,
                )
                if persisted is None:
                    raise PersistenceError("Persisted task was not found in the recent resumable history.")
                _, persisted_state, _, _ = persisted
                if persisted_state.status.value == "completed":
                    raise PersistenceError("Completed tasks cannot be resumed.")
                execution_id = str(uuid.uuid4())
                _write({
                    "type": "command.accepted",
                    "schema_version": 1,
                    "command_id": command_identifier,
                    "accepted": True,
                    "task_id": identifier,
                })
                command_accepted = True
                _write({
                    "type": "task.created",
                    "schema_version": 1,
                    "event_id": f"{identifier}:{execution_id}:resumed",
                    "execution_id": execution_id,
                    "task_id": identifier,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {"title": persisted_state.task, "status": "running", "resumed": True},
                })
                for previous_event in persisted_state.events[-100:]:
                    _write({
                        "type": "core.event",
                        "schema_version": 1,
                        "event_id": f"{identifier}:{execution_id}:history:{previous_event.sequence}",
                        "execution_id": execution_id,
                        "task_id": identifier,
                        "timestamp": previous_event.timestamp,
                        "payload": {
                            "event_type": previous_event.event_type,
                            "message": previous_event.message,
                            "data": previous_event.data,
                            "sequence": previous_event.sequence,
                            "iteration": previous_event.iteration,
                            "restored": True,
                        },
                    })

                def forward_resumed_event(event: AgentEvent) -> None:
                    _write({
                        "type": "core.event",
                        "schema_version": 1,
                        "event_id": f"{identifier}:{execution_id}:{event.sequence}",
                        "execution_id": execution_id,
                        "task_id": identifier,
                        "timestamp": event.timestamp,
                        "payload": {
                            "event_type": event.event_type,
                            "message": event.message,
                            "data": event.data,
                            "sequence": event.sequence,
                            "iteration": event.iteration,
                            "resumed": True,
                        },
                    })

                state = agent.resume(root, persisted_state.task, event_callback=forward_resumed_event,
                                         model_switch_approval_callback=lambda request: _approval(
                                             identifier, execution_id, request, kind="model_switch"
                                         ))
                summary = state.events[-1].message if state.events else f"Task ended with status {state.status.value}."
                completed = state.status.value == "completed"
                _write({
                    "type": "task.completed" if completed else "task.failed",
                    "schema_version": 1,
                    "event_id": f"{identifier}:{execution_id}:terminal",
                    "execution_id": execution_id,
                    "task_id": identifier,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "result_summary": summary,
                        "message": summary,
                        "recoverable": state.status.value in {"failed", "max_iterations", "recovering", "running", "verifying", "resuming"},
                        "resumed": True,
                    },
                })
            except (BrokenPipeError, EOFError):
                return 0
            except Exception as exc:
                if not command_accepted:
                    _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": str(exc)})
                if execution_id is not None:
                    _write({
                        "type": "task.failed",
                        "schema_version": 1,
                        "event_id": f"{identifier}:{execution_id}:resume-error",
                        "execution_id": execution_id,
                        "task_id": identifier,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "payload": {"message": str(exc), "recoverable": True, "resumed": True},
                    })
                _write({"type": "runtime.error", "schema_version": 1, "task_id": requested_task_id, "message": str(exc)})
            continue

        if message.get("schema_version") != 1 or message.get("type") != "task.submit":
            _write({
                "type": "command.rejected",
                "schema_version": 1,
                "command_id": message.get("command_id"),
                "reason": "Unsupported command. Expected task.submit.",
            })
            continue

        command_identifier = message.get("command_id")
        payload = message.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        instruction = payload.get("instruction")
        requested_root = payload.get("project_root")
        if not isinstance(command_identifier, str) or not command_identifier.strip():
            _write({"type": "command.rejected", "schema_version": 1, "reason": "command_id is required."})
            continue
        try:
            workspace_matches = isinstance(requested_root, str) and Path(requested_root).resolve() == root
        except (OSError, RuntimeError):
            workspace_matches = False
        if not workspace_matches:
            _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "project_root does not match the active runtime workspace."})
            continue
        if not isinstance(instruction, str) or not instruction.strip():
            _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "instruction is required."})
            continue
        instruction = instruction.strip()
        raw_history = payload.get("history", [])
        if not isinstance(raw_history, list):
            raw_history = []
        conversation_history = [
            {"role": item["role"], "content": item["content"][:4000]}
            for item in raw_history[-16:]
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and isinstance(item.get("content"), str)
        ]
        raw_editor = payload.get("active_editor")
        active_editor = None
        if isinstance(raw_editor, dict):
            editor_path = raw_editor.get("path")
            editor_content = raw_editor.get("content", "")
            if (isinstance(editor_path, str) and editor_path.strip()
                    and not Path(editor_path).is_absolute()
                    and ".." not in Path(editor_path).parts
                    and isinstance(editor_content, str)):
                active_editor = {
                    "path": editor_path.replace("\\", "/")[:500],
                    "content": editor_content[:16000],
                    "dirty": bool(raw_editor.get("dirty", False)),
                }
        if len(instruction) > MAX_INSTRUCTION_LENGTH:
            _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "instruction exceeds 8000 characters."})
            continue
        if command_identifier in seen_command_ids:
            _write({"type": "command.rejected", "schema_version": 1, "command_id": command_identifier, "reason": "command_id has already been processed."})
            continue
        seen_command_ids[command_identifier] = None
        if len(seen_command_ids) > MAX_SEEN_COMMAND_IDS:
            del seen_command_ids[next(iter(seen_command_ids))]

        identifier = task_id(instruction, root)
        execution_id = str(uuid.uuid4())
        _write({
            "type": "command.accepted",
            "schema_version": 1,
            "command_id": command_identifier,
            "accepted": True,
            "task_id": identifier,
        })
        _write({
            "type": "task.created",
            "schema_version": 1,
            "event_id": f"{identifier}:{execution_id}:created",
            "execution_id": execution_id,
            "task_id": identifier,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {"title": instruction, "status": "running"},
        })

        def forward_event(event: AgentEvent) -> None:
            _write({
                "type": "core.event",
                "schema_version": 1,
                "event_id": f"{identifier}:{execution_id}:{event.sequence}",
                "execution_id": execution_id,
                "task_id": identifier,
                "timestamp": event.timestamp,
                "payload": {
                    "event_type": event.event_type,
                    "message": event.message,
                    "data": event.data,
                    "sequence": event.sequence,
                    "iteration": event.iteration,
                },
            })

        try:
            state = agent.run(
                root,
                instruction,
                event_callback=forward_event,
                conversation_history=conversation_history,
                active_editor_context=active_editor,
                approval_callback=lambda plan: _approval(identifier, execution_id, plan, kind="plan"),
                high_risk_approval_callback=lambda plan: _approval(identifier, execution_id, plan, kind="high_risk"),
                model_switch_approval_callback=lambda request: _approval(
                    identifier, execution_id, request, kind="model_switch"
                ),
            )
            result_summary = (
                state.events[-1].message if state.events
                else f"Task ended with status {state.status.value}."
            )
            _write({
                "type": "task.completed" if state.status.value == "completed" else "task.failed",
                "schema_version": 1,
                "event_id": f"{identifier}:{execution_id}:terminal",
                "execution_id": execution_id,
                "task_id": identifier,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {
                    "result_summary": result_summary,
                    "message": result_summary,
                    "recoverable": state.status.value in {"failed", "max_iterations"},
                },
            })
        except (BrokenPipeError, EOFError):
            return 0
        except Exception as exc:
            _write({
                "type": "task.failed",
                "schema_version": 1,
                "event_id": f"{identifier}:{execution_id}:terminal-error",
                "execution_id": execution_id,
                "task_id": identifier,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": {"message": str(exc), "recoverable": True},
            })
            _write({
                "type": "runtime.error",
                "schema_version": 1,
                "task_id": identifier,
                "message": str(exc),
            })
