import { useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import type { RuntimeLimits, RuntimePlanStep, RuntimeSubtask } from "./types/runtime";
import {
  Activity, Bot, CheckCircle2, ChevronDown, CircleDot,
  Clock3, FileCode2, Files, Folder, GitBranch,
  ListTodo, Plus, Search, ShieldCheck, Workflow,
  Zap, GitFork, Circle, AlertCircle
} from "lucide-react";

type NavItem = "explorer" | "agent" | "tasks" | "approvals";
type ProjectEntry = { name: string; path: string; is_dir: boolean };
function App() {
  const [activeNav, setActiveNav] = useState<NavItem>("explorer");
  const [selectedFile, setSelectedFile] = useState("App.tsx");
  const [bottomPanel, setBottomPanel] = useState("Atividades");
  const [projectRoot, setProjectRoot] = useState<string | null>(null);
  const [projectEntries, setProjectEntries] = useState<ProjectEntry[]>([]);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [openedPath, setOpenedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [instructionDraft, setInstructionDraft] = useState("");
  const [runtimeStatus, setRuntimeStatus] = useState<"offline" | "connecting" | "online">("offline");
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [runtimeEvents, setRuntimeEvents] = useState<Array<{ id: string; type: string; message: string }>>([]);
  const [runtimeTasks, setRuntimeTasks] = useState<Array<{ id: string; executionId: string; title: string; status: string }>>([]);
  const [runtimeSubtasks, setRuntimeSubtasks] = useState<RuntimeSubtask[]>([]);
  const [runtimePlanSteps, setRuntimePlanSteps] = useState<RuntimePlanStep[]>([]);
  const [activeRuntimeSubtask, setActiveRuntimeSubtask] = useState<string | null>(null);
  const [activeRuntimeStep, setActiveRuntimeStep] = useState<number | null>(null);
  const [runtimeLimits, setRuntimeLimits] = useState<RuntimeLimits | null>(null);
  const [pendingApproval, setPendingApproval] = useState<{ approvalId: string; kind: "plan" | "high_risk"; summary: string; risk: string } | null>(null);
  const [approvalSubmitting, setApprovalSubmitting] = useState(false);
  const seenEventIds = useRef(new Set<string>());
  const approvalCommandId = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    const unlisten = listen<Record<string, any>>("runtime-message", event => {
      if (!active) return;
      const message = event.payload;
      if (typeof message.event_id === "string") {
        if (seenEventIds.current.has(message.event_id)) return;
        seenEventIds.current.add(message.event_id);
        if (seenEventIds.current.size > 500) {
          const oldest = seenEventIds.current.values().next().value;
          if (oldest) seenEventIds.current.delete(oldest);
        }
      }
      if (message.type === "runtime.ready") {
        setRuntimeStatus("online");
        setRuntimeLimits(message.limits || null);
        setRuntimeError(null);
      } else if (message.type === "approval.requested") {
        setRuntimeTasks(items => items.map(task => (message.execution_id ? task.executionId === message.execution_id : task.id === message.task_id) ? { ...task, status: "Aguardando aprovação" } : task));
        setPendingApproval({ approvalId: message.payload.approval_id, kind: message.payload.kind, summary: message.payload.summary, risk: message.payload.risk });
        setRuntimeEvents(items => [...items, { id: message.event_id, type: message.type, message: "Plano aguardando sua aprovação." }].slice(-100));
      } else if (message.type === "core.event") {
        const coreType = message.payload?.event_type || "core.event";
        const eventData = message.payload?.data || {};
        const board = eventData.board;
        if (coreType === "plan_created" && Array.isArray(eventData.plan?.steps)) {
          setRuntimePlanSteps(eventData.plan.steps.map((title: string, index: number) => ({ step: index + 1, title, status: "pending" })));
        }
        if (coreType === "plan_step_started" && Number.isInteger(eventData.step)) {
          setActiveRuntimeStep(eventData.step);
          setRuntimePlanSteps(items => items.map(item => item.step === eventData.step ? { ...item, status: "running" } : item));
        }
        if (coreType === "plan_step_completed" && Number.isInteger(eventData.step)) {
          setRuntimePlanSteps(items => items.map(item => item.step === eventData.step ? { ...item, status: "completed" } : item));
        }
        if (board && Array.isArray(board.subtasks)) {
          setRuntimeSubtasks(board.subtasks);
          const running = board.subtasks.find((item: RuntimeSubtask) => item.status === "running");
          setActiveRuntimeSubtask(running?.id || null);
          setActiveRuntimeStep(running?.plan_steps?.[0] || null);
        }
        if (coreType === "status_changed" && eventData.to_status) {
          setRuntimeTasks(items => items.map(task => (message.execution_id ? task.executionId === message.execution_id : task.id === message.task_id) ? { ...task, status: String(eventData.to_status) } : task));
        }
        if (coreType === "subtask_started") {
          setActiveRuntimeSubtask(eventData.subtask_id || null);
          setActiveRuntimeStep(eventData.plan_steps?.[0] || null);
        }
        if (coreType === "subtask_progress") {
          setActiveRuntimeSubtask(eventData.subtask_id || null);
          setActiveRuntimeStep(eventData.parent_plan_step || eventData.plan_step || null);
        }
        if (["subtask_completed", "subtask_failed", "subtask_blocked"].includes(coreType)) {
          setActiveRuntimeSubtask(null);
          setActiveRuntimeStep(null);
        }
        setRuntimeEvents(items => [...items, { id: message.event_id, type: coreType, message: message.payload.message }].slice(-100));
      } else if (message.type === "task.created") {
        setRuntimeBusy(true);
        setRuntimeTasks(items => [...items, { id: message.task_id, executionId: message.execution_id, title: message.payload.title, status: "Recebida" }].slice(-50));
        setRuntimeEvents(items => [...items, { id: message.event_id, type: message.type, message: message.payload.title }].slice(-100));
      } else if (message.type === "task.completed" || message.type === "task.failed") {
        setRuntimeBusy(false);
        setRuntimeTasks(items => items.map(task => task.executionId === message.execution_id ? { ...task, status: message.type === "task.completed" ? "Concluída" : "Falhou" } : task));
        setActiveRuntimeSubtask(null);
        setActiveRuntimeStep(null);
        setRuntimeEvents(items => [...items, { id: message.event_id, type: message.type, message: message.payload.result_summary || message.payload.message }].slice(-100));
        setPendingApproval(null);
        setApprovalSubmitting(false);
      } else if (message.type === "command.accepted" && message.command_id === approvalCommandId.current) {
        approvalCommandId.current = null;
        setApprovalSubmitting(false);
        setPendingApproval(null);
        setRuntimeTasks(items => items.map(task => task.status === "Aguardando aprovação" ? { ...task, status: "Decisão enviada" } : task));
      } else if (message.type === "runtime.error" || message.type === "command.rejected") {
        if (message.type === "command.rejected") {
          if (message.command_id === approvalCommandId.current) {
            approvalCommandId.current = null;
            setApprovalSubmitting(false);
          } else setRuntimeBusy(false);
        }
        setRuntimeError(message.message || message.reason || "Falha de comunicação com o runtime.");
      }
    });
    const streamClosed = listen("runtime-stream-closed", () => {
      if (active) {
        setRuntimeStatus("offline");
        setRuntimeBusy(false);
        setRuntimeSubtasks([]);
        setRuntimePlanSteps([]);
        setActiveRuntimeSubtask(null);
        setActiveRuntimeStep(null);
        setPendingApproval(null);
        setApprovalSubmitting(false);
        approvalCommandId.current = null;
        setRuntimeError(current => current || "O processo Python encerrou a conexão.");
      }
    });
    const stderr = listen<string>("runtime-stderr", event => {
      if (!active || !event.payload.trim()) return;
      const line = event.payload.slice(0, 2000);
      setRuntimeEvents(items => [...items, { id: crypto.randomUUID(), type: "runtime.log", message: line }].slice(-100));
      if (/\\b(ERROR|CRITICAL|Traceback|Exception)\\b/.test(line)) setRuntimeError(line);
    });
    const protocolError = listen<string>("runtime-protocol-error", event => {
      if (active) setRuntimeError("Falha no protocolo do runtime: " + event.payload.slice(0, 1000));
    });
    return () => {
      active = false;
      unlisten.then(stop => stop());
      streamClosed.then(stop => stop());
      stderr.then(stop => stop());
      protocolError.then(stop => stop());
      void invoke("stop_runtime").catch(() => undefined);
    };
  }, []);

  async function openEntry(entry: ProjectEntry) {
    if (entry.is_dir) {
      try { setProjectEntries(await invoke<ProjectEntry[]>("read_project_entries", { projectRoot: projectRoot!, projectPath: entry.path })); setProjectError(null); }
      catch (error) { setProjectError(String(error)); }
      return;
    }
    try { setFileContent(await invoke<string>("read_project_file", { projectRoot: projectRoot!, filePath: entry.path })); setOpenedPath(entry.path); setSelectedFile(entry.name); setProjectError(null); }
    catch (error) { setProjectError(String(error)); }
  }

  async function openProject() {
    if (runtimeBusy) {
      setRuntimeError("Aguarde a tarefa atual terminar antes de trocar de workspace.");
      return;
    }
    const selected = await open({ directory: true, multiple: false, title: "Abrir projeto" });
    if (typeof selected !== "string") return;
    try {
      const entries = await invoke<ProjectEntry[]>("read_project_entries", { projectRoot: selected, projectPath: selected });
      setProjectRoot(selected);
      setProjectEntries(entries);
      setProjectError(null);
      setRuntimeEvents([]);
      setRuntimeTasks([]);
      setRuntimeSubtasks([]);
      setRuntimePlanSteps([]);
      setActiveRuntimeSubtask(null);
      setActiveRuntimeStep(null);
      setRuntimeLimits(null);
      setRuntimeBusy(false);
      setPendingApproval(null);
      setApprovalSubmitting(false);
      seenEventIds.current.clear();
      approvalCommandId.current = null;
      setRuntimeStatus("connecting");
      setRuntimeError(null);
      await invoke("stop_runtime").catch(() => undefined);
      await invoke("start_runtime", { projectPath: selected });
    } catch (error) {
      setProjectError(String(error));
      setRuntimeStatus("offline");
      setRuntimeError(String(error));
    }
  }

  async function submitInstruction() {
    if (!projectRoot || !instructionDraft.trim() || runtimeStatus !== "online") return;
    try {
      await invoke("send_runtime_command", { message: {
        type: "task.submit", schema_version: 1, command_id: crypto.randomUUID(),
        payload: { instruction: instructionDraft.trim(), project_root: projectRoot }
      }});
      setRuntimeBusy(true);
      setInstructionDraft("");
      setRuntimeError(null);
    } catch (error) { setRuntimeError(String(error)); }
  }

  async function respondToApproval(decision: "approve" | "reject") {
    if (!pendingApproval) return;
    try {
      const commandId = crypto.randomUUID();
      approvalCommandId.current = commandId;
      setApprovalSubmitting(true);
      await invoke("send_runtime_command", { message: {
        type: "approval.respond", schema_version: 1, command_id: commandId,
        payload: { approval_id: pendingApproval.approvalId, decision }
      }});
    } catch (error) {
      approvalCommandId.current = null;
      setApprovalSubmitting(false);
      setRuntimeError(String(error));
    }
  }

  return (
    <main className="app-shell">
      <header className="titlebar">
        <div className="brand"><div className="brand-mark"><Zap size={15} /></div><span>KARDEC<span className="brand-light">AGENT</span></span>
        <span className="workspace-label">Studio <span className="muted">/</span> Workspace</span>
        <div className="titlebar-right"><span className="connection"><span /> {runtimeStatus === "online" ? "Core conectado" : runtimeStatus === "connecting" ? "Conectando ao core..." : "Core desconectado"}</span><span className="avatar">KA</span></div>
      </header>

      <div className="work-area">
        <nav className="activity-rail" aria-label="Navegação">
          <button className={activeNav === "explorer" ? "rail-button selected" : "rail-button"} onClick={() => setActiveNav("explorer")} title="Explorer"><Files /></button>
          <button className={activeNav === "agent" ? "rail-button selected" : "rail-button"} onClick={() => setActiveNav("agent")} title="Agent"><Bot /></button>
          <button className={activeNav === "tasks" ? "rail-button selected" : "rail-button"} onClick={() => setActiveNav("tasks")} title="Tarefas"><Workflow /></button>
          <button className={activeNav === "approvals" ? "rail-button selected" : "rail-button"} onClick={() => setActiveNav("approvals")} title="Aprovações"><ShieldCheck /></button>
          <div className="rail-spacer" />
          <button className="rail-button" title="Atividade"><Activity /></button>
        </nav>

        <aside className="sidebar">
          <div className="sidebar-heading"><span>{activeNav === "explorer" ? "EXPLORER" : activeNav.toUpperCase()}</span><button className="icon-button" title="Nova ação"><Plus size={15}/></button></div>
          {activeNav === "explorer" ? (
            <>
              <div className="project-heading"><ChevronDown size={14}/><Folder size={15} className="folder-icon"/> {projectRoot ? projectRoot.replaceAll("\\\\", "/").split("/").filter(Boolean).pop() : "KARDECAGENT"}</div>
              <div className="tree">
                {!projectRoot && <>
                  <div className="tree-row"><ChevronDown/><Folder/><span>frontend</span></div>
                  <div className="tree-row indent"><ChevronDown/><Folder/><span>src</span></div>
                  <button className={selectedFile === "App.tsx" ? "tree-row file selected-file indent2" : "tree-row file indent2"} onClick={() => setSelectedFile("App.tsx")}><FileCode2/><span>App.tsx</span></button>
                  <button className={selectedFile === "main.tsx" ? "tree-row file selected-file indent2" : "tree-row file indent2"} onClick={() => setSelectedFile("main.tsx")}><FileCode2/><span>main.tsx</span></button>
                  <button className={selectedFile === "styles.css" ? "tree-row file selected-file indent2" : "tree-row file indent2"} onClick={() => setSelectedFile("styles.css")}><FileCode2/><span>styles.css</span></button>
                </>}
                {projectEntries.map(entry => <button className="tree-row file" key={entry.path} title={entry.path} onClick={() => openEntry(entry)}>{entry.is_dir ? <Folder/> : <FileCode2/>}<span>{entry.name}</span></button>)}
                {projectError && <div className="tree-row" role="alert">{projectError}</div>}
              </div>
              <div className="sidebar-bottom"><Search size={14}/> Buscar nos arquivos <span className="shortcut">⌘P</span></div>
            </>
          ) : activeNav === "tasks" ? (
            <div className="task-sidebar">{runtimeTasks.length ? runtimeTasks.slice().reverse().map(task => <div className="task-mini" key={task.executionId}><CircleDot className={task.status === "Em execução" ? "active" : task.status === "Concluída" ? "done" : "queued"}/><div><strong>{task.title}</strong><small>{task.id.slice(0, 8)} · {task.status}</small></div></div>) : <div className="empty-state"><ListTodo/><strong>Nenhuma tarefa recebida</strong><small>Envie uma instrução ao gestor na área Agent.</small></div>}</div>
          ) : activeNav === "approvals" ? (pendingApproval ? <div className="approval-sidebar-card"><ShieldCheck/><strong>Aprovação pendente</strong><small>{pendingApproval.kind === "high_risk" ? "Confirmação adicional de alto risco" : "Plano aguardando sua decisão"}</small><span>Risco: {pendingApproval.risk}</span></div> : <div className="empty-state"><ShieldCheck/><strong>Nenhuma aprovação pendente</strong><small>Solicitações do gestor aparecerão aqui.</small></div>) :
          <div className="empty-state"><Bot/><strong>Agent Workspace</strong><small>Conecte o core para iniciar tarefas.</small></div>}
        </aside>

        <section className="center-pane">
          {activeNav === "agent" ? (
            <div className="manager-inbox">
              <div className="process-toolbar"><div><span className="eyebrow">CENTRAL DE COMANDO</span><h2>Gestor de tarefas</h2><p>Envie uma instrução em linguagem natural. O gestor será responsável por planejar, decompor e distribuir o trabalho aos agentes.</p></div><span className="runtime-offline">{runtimeStatus === "online" ? "CORE ONLINE" : runtimeStatus === "connecting" ? "CONECTANDO" : "CORE OFFLINE"}</span></div>
              <section className="manager-inbox-card">
                <div className="manager-inbox-icon"><Bot size={22}/></div>
                <div><strong>{runtimeTasks.length ? `${runtimeTasks.length} tarefa(s) recebida(s) nesta sessão` : "Aguardando a primeira instrução"}</strong><p>As solicitações aparecem com seu estado e os eventos reais enviados pelo gestor Python. O histórico desta sessão é mantido limitado em memória.</p></div>
              </section>
              <div className="manager-flow">
                <div><span>01</span><strong>Receber</strong><small>O gestor recebe seu objetivo e o contexto do projeto.</small></div>
                <div><span>02</span><strong>Planejar</strong><small>O core Python cria um plano e solicita sua aprovação antes de executar.</small></div>
                <div><span>03</span><strong>Distribuir</strong><small>O runtime atual executa as subtarefas sequencialmente; concorrência segura será adicionada depois.</small></div>
                <div><span>04</span><strong>Entregar</strong><small>Resultados, alterações e bloqueios ficam visíveis nesta área.</small></div>
              </div>
              {runtimeError && <div className="manager-runtime-error" role="alert">{runtimeError}</div>}
              <section className="manager-event-list">
                <div className="process-section-title"><strong>Atividade do gestor</strong><span>{runtimeEvents.length} eventos recentes · fluxo em tempo real</span></div>
                {runtimeEvents.length === 0 ? <p className="manager-no-events">As instruções recebidas e as atualizações do core aparecerão aqui.</p> : runtimeEvents.slice().reverse().map(item => <div className="manager-event" key={item.id}><span>{item.type}</span><p>{item.message}</p></div>)}
              </section>
              <div className="manager-inbox-note"><ShieldCheck size={15}/> Nenhuma instrução será executada sem passar pelas regras de aprovação do core.</div>
            </div>
          ) : activeNav === "tasks" ? (
            <div className="process-dashboard">
              <div className="process-toolbar"><div><span className="eyebrow">ORQUESTRAÇÃO</span><h2>Fluxo de execução</h2><p>Mapa de dependências e estados recebido por eventos do core Python</p></div><button className="process-filter"><Circle size={9}/> Todas as etapas <ChevronDown size={13}/></button></div>
              <div className="process-summary"><div><span>ESTADO DO CORE</span><strong>{runtimeStatus === "online" ? "Online" : runtimeStatus === "connecting" ? "Conectando" : "Offline"}</strong><small>{runtimeBusy ? "Uma tarefa está em execução" : "Aguardando instrução"}</small></div><div><span>TAREFAS RECEBIDAS</span><strong>{String(runtimeTasks.length).padStart(2, "0")}</strong><small>Histórico recente nesta sessão</small></div><div><span>EVENTOS</span><strong>{String(runtimeEvents.length).padStart(2, "0")}</strong><small>Buffer limitado a 100 eventos</small></div></div>
              <section className="runtime-task-graph">
                <div className="process-section-title"><strong>Mapa de execução do runtime</strong><span>{runtimeSubtasks.length ? `${runtimeSubtasks.length} subtarefas · dependências do core` : runtimePlanSteps.length ? `${runtimePlanSteps.length} etapas aprovadas · progresso do executor` : "Aguardando o plano do core"}</span></div>
                {runtimeSubtasks.length ? <div className="runtime-board-grid">
                  {runtimeSubtasks.map(subtask => <article className={`runtime-subtask-card status-${subtask.status}`} key={subtask.id}>
                    <div className="runtime-subtask-head"><span className="runtime-subtask-id">{subtask.id}</span><span className={`runtime-subtask-status status-${subtask.status}`}>{subtask.status === "running" ? "EM EXECUÇÃO" : subtask.status === "completed" ? "CONCLUÍDA" : subtask.status === "failed" ? "FALHOU" : subtask.status === "blocked" ? "BLOQUEADA" : "PENDENTE"}</span></div>
                    <strong>{subtask.title}</strong><p>{subtask.objective}</p>
                    <div className="runtime-subtask-meta"><span>Dependências: {subtask.dependencies.length ? subtask.dependencies.join(", ") : "Nenhuma"}</span><span>Etapas aprovadas: {subtask.plan_steps.join(", ") || "—"} · tentativas: {subtask.iterations}</span></div>
                    {subtask.scope.length > 0 && <div className="runtime-subtask-scope">{subtask.scope.slice(0, 3).join(" · ")}{subtask.scope.length > 3 ? ` · +${subtask.scope.length - 3}` : ""}</div>}
                    {subtask.result && <small className="runtime-subtask-result">{subtask.result}</small>}
                  </article>)}
                </div> : runtimePlanSteps.length ? <div className="runtime-plan-grid">
                  {runtimePlanSteps.map(step => <article className={`runtime-plan-step status-${step.status}`} key={step.step}>
                    <span>ETAPA {step.step}</span><strong>{step.title}</strong><small>{step.status === "running" ? "EM EXECUÇÃO" : step.status === "completed" ? "CONCLUÍDA" : "PENDENTE"}</small>
                  </article>)}
                </div> : <div className="runtime-graph-empty"><Workflow size={22}/><strong>Aguardando o plano do gestor</strong><p>Assim que o core gerar o plano aprovado, as etapas aparecerão aqui. Se houver decomposição, o mapa muda para o grafo real de subtarefas e dependências.</p></div>}
              </section>
              <section className="agent-team">
                <div className="process-section-title"><strong>Executor do runtime</strong><span>Estado real · sem agentes simulados</span></div>
                <div className="runtime-executor-card">
                  <span className={`runtime-executor-indicator ${runtimeBusy ? "busy" : "idle"}`}/><div><strong>Core Python · AgentLoop / Orchestrator</strong><small>{runtimeBusy ? activeRuntimeSubtask ? "Executando subtarefa " + activeRuntimeSubtask + (activeRuntimeStep ? " · etapa " + activeRuntimeStep : "") : activeRuntimeStep ? "Executando etapa " + activeRuntimeStep + " do plano" : "Planejando, aguardando aprovação ou executando tarefa" : "Ocioso · aguardando instrução"}</small></div>
                  <span className="runtime-executor-mode">{runtimeLimits?.execution_mode || "—"}</span>
                </div>
              </section>
              <section className="runtime-capacity">
                <div className="process-section-title"><strong>Limites operacionais anunciados pelo core</strong><span>Valores recebidos no runtime.ready</span></div>
                <div className="runtime-capacity-grid">
                  <div><span>Concorrência</span><strong>{runtimeLimits?.max_concurrent_tasks ?? "—"}</strong><small>tarefas simultâneas</small></div>
                  <div><span>Subtarefas por plano</span><strong>{runtimeLimits?.max_subtasks ?? "—"}</strong><small>limite do decompositor</small></div>
                  <div><span>Concorrência de subtarefas</span><strong>{runtimeLimits?.max_concurrent_subtasks ?? "—"}</strong><small>workers simultâneos</small></div>
                  <div><span>Iterações LLM</span><strong>{runtimeLimits?.max_llm_iterations ?? "—"}</strong><small>máximo por tarefa</small></div>
                  <div><span>Saída de comandos</span><strong>{runtimeLimits ? Math.round(runtimeLimits.max_command_output_chars / 1000) + "k" : "—"}</strong><small>caracteres retidos</small></div>
                  <div><span>Snapshot de projeto</span><strong>{runtimeLimits?.max_project_snapshot_files ?? "—"}</strong><small>arquivos por contexto</small></div>
                  <div><span>Timeout LLM</span><strong>{runtimeLimits?.max_llm_timeout_seconds ?? "—"}s</strong><small>por chamada</small></div>
                  <div><span>Timeout de comando</span><strong>{runtimeLimits?.max_command_timeout_seconds ?? "—"}s</strong><small>por execução</small></div>
                  <div><span>Instrução máxima</span><strong>{runtimeLimits ? runtimeLimits.max_instruction_chars.toLocaleString("pt-BR") : "—"}</strong><small>caracteres</small></div>
                  <div><span>Evento máximo</span><strong>{runtimeLimits ? Math.round(runtimeLimits.max_event_bytes / 1024) + " KB" : "—"}</strong><small>payload JSONL</small></div>
                </div>
              </section>
              <section className="process-events">
                <div className="process-section-title"><strong>Eventos recentes</strong><span>Eventos reais recebidos do core</span></div>
                {runtimeEvents.length ? runtimeEvents.slice().reverse().slice(0, 12).map(event => <div className="process-event" key={event.id}><span className="event-marker info"/><time>Studio</time><strong>{event.type}</strong><span>{event.message}</span></div>) : <p className="manager-no-events">Nenhum evento recebido. Inicie uma tarefa pela área Agent.</p>}
              </section>
            </div>
          ) : (
            <>
              <div className="tabs"><div className="tab active"><FileCode2 size={14}/>{selectedFile}<span className="tab-close">×</span></div><div className="tab-placeholder">KardecAgent Studio</div></div>
              <div className="editor">
                {openedPath ? <div className="file-view"><div className="file-path">{openedPath}</div><pre>{fileContent}</pre></div> : <div className="welcome"><div className="welcome-icon"><Zap size={24}/></div><h1>KardecAgent <span>Studio</span></h1><p>Seu ambiente de desenvolvimento orientado por agentes.</p><div className="welcome-actions"><button onClick={openProject}><Folder size={15}/> Abrir projeto</button><button className="secondary" onClick={() => setActiveNav("tasks")}><Plus size={15}/> Ver processos</button></div><div className="quick-grid"><div><span className="quick-label">WORKSPACE</span><strong>{projectRoot ? projectRoot.replaceAll("\\\\", "/").split("/").filter(Boolean).pop() : "Projeto local"}</strong><small>{projectRoot || "Selecione uma pasta para começar"}</small></div><div><span className="quick-label">RUNTIME</span><strong className="status-muted">{runtimeStatus === "online" ? "Conectado" : runtimeStatus === "connecting" ? "Conectando" : "Desconectado"}</strong><small>{runtimeError || (runtimeStatus === "online" ? "Gestor pronto para receber instruções" : "Abra um projeto para iniciar o core Python")}</small></div></div></div>}
              </div>
              <div className="bottom-panel"><div className="panel-tabs">{["Atividades","Output","Alterações"].map(tab => <button key={tab} className={bottomPanel === tab ? "panel-tab active" : "panel-tab"} onClick={() => setBottomPanel(tab)}>{tab}</button>)}<button className="panel-collapse"><ChevronDown size={14}/></button></div><div className="panel-content"><Clock3 size={14}/><span>Eventos do runtime aparecerão aqui, sem polling contínuo.</span></div></div>
            </>
          )}
        </section>
        <aside className="agent-panel">
          {activeNav === "tasks" ? (
            <div className="process-inspector">
              <div className="agent-header"><div><GitFork size={16}/><strong>Detalhes do processo</strong></div><span className="live-dot">INSPECTOR</span></div>
              <div className="inspector-content">
                <span className="eyebrow">RUNTIME / SUBTAREFAS</span>
                {runtimeSubtasks.length ? <>
                  <h3>{runtimeSubtasks.find(item => item.id === activeRuntimeSubtask)?.title || "Quadro de execução"}</h3>
                  <span className="inspector-status status-active">{runtimeSubtasks.find(item => item.id === activeRuntimeSubtask)?.status || "planejado"}</span>
                  <div className="inspector-divider"/>
                  <div className="inspector-section-head"><strong>Dependências e escopo</strong></div>
                  {runtimeSubtasks.map(item => <div className="runtime-inspector-item" key={item.id}>
                    <strong>{item.id} · {item.title}</strong>
                    <small>Estado: {item.status} · depende de: {item.dependencies.length ? item.dependencies.join(", ") : "nenhuma"}</small>
                    <small>Escopo: {item.scope.length ? item.scope.join(", ") : "não informado"}</small>
                  </div>)}
                </> : runtimePlanSteps.length ? <>
                  <h3>Plano aprovado</h3>
                  <div className="inspector-section-head"><strong>Etapas do plano</strong></div>
                  {runtimePlanSteps.map(step => <div className="runtime-inspector-item" key={step.step}><strong>Etapa {step.step} · {step.title}</strong><small>Estado: {step.status}</small></div>)}
                </> : <div className="runtime-graph-empty"><Workflow size={20}/><strong>Nenhum plano recebido</strong><p>O inspetor será preenchido quando o gestor publicar o plano aprovado.</p></div>}
                <div className="inspector-divider"/>
                <div className="inspector-section-head"><strong>Runtime Python</strong></div>
                <div className="selected-agent-card"><div className={"agent-avatar-small " + (runtimeBusy ? "running" : "done")}><Bot size={15}/></div><div><strong>AgentLoop / Orchestrator</strong><small>{runtimeBusy ? "Ativo" : "Ocioso"} · {runtimeLimits?.execution_mode || "modo não informado"}</small></div></div>
                <div className="inspector-note"><AlertCircle size={14}/><span>O núcleo atual utiliza um executor Python sequencial. Agentes concorrentes só serão exibidos quando existirem workers reais no runtime.</span></div>
              </div>
            </div>
          ) : <>
            <div className="agent-header"><div><Bot size={17}/><strong>Agent</strong></div><span className="live-dot">{runtimeStatus === "online" ? "● Online" : runtimeStatus === "connecting" ? "● Conectando" : "● Offline"}</span></div>
            {pendingApproval ? <div className="approval-prompt">
              <div className="approval-prompt-head"><ShieldCheck size={18}/><strong>{pendingApproval.kind === "high_risk" ? "Aprovação adicional de alto risco" : "Aprovar plano de execução"}</strong></div>
              <span className="approval-risk">RISCO: {pendingApproval.risk.toUpperCase()}</span>
              <pre>{pendingApproval.summary}</pre>
              <div className="approval-actions"><button onClick={() => respondToApproval("reject")} className="approval-reject" disabled={approvalSubmitting}>Rejeitar</button><button onClick={() => respondToApproval("approve")} className="approval-accept" disabled={approvalSubmitting}>{approvalSubmitting ? "Enviando..." : "Aprovar execução"}</button></div>
            </div> : <div className="agent-empty"><div className="agent-orb"><Bot size={24}/></div><strong>Pronto para trabalhar</strong><p>Abra um projeto e descreva o que deseja construir. O KardecAgent planeja, executa e valida com você no controle.</p></div>}
            <div className="agent-composer">
              <label htmlFor="manager-instruction">Instrução para o gestor</label>
              <textarea id="manager-instruction" value={instructionDraft} onChange={event => setInstructionDraft(event.target.value.slice(0, 8000))} placeholder="Ex.: Analise o projeto, proponha um plano e implemente a autenticação..." rows={4} maxLength={8000}/>
              <div className="composer-footer"><span>{instructionDraft.length}/8000 · {runtimeStatus !== "online" ? "Aguardando conexão" : runtimeBusy ? "Gestor ocupado" : "Pronto para enviar"}</span><button onClick={submitInstruction} disabled={!projectRoot || !instructionDraft.trim() || runtimeStatus !== "online" || runtimeBusy} title={runtimeStatus === "online" ? "Enviar instrução ao gestor" : "Runtime Python ainda não conectado"}><Plus size={15}/></button></div>
              <small className="composer-hint">A instrução será enviada ao gestor central, não diretamente a um agente individual.</small>
            </div>
            <div className="agent-foot"><ShieldCheck size={13}/> Aprovações e ações sensíveis sob seu controle</div>
          </>}
        </aside>
      </div>

      <footer className="statusbar"><div><GitBranch size={12}/> feat/kardecagent-studio <span>·</span><CheckCircle2 size={12}/> {projectRoot ? "Projeto aberto" : "Workspace local"}</div><div><span className="status-indicator"/> Studio <span>·</span> {runtimeStatus === "online" ? "Runtime conectado" : runtimeStatus === "connecting" ? "Conectando runtime" : "Runtime offline"}</div></footer>
    </main>
  );
}

export default App;
