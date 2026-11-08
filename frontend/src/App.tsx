import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import {
  Activity, Bot, CheckCircle2, ChevronDown, ChevronRight, CircleDot,
  Clock3, FileCode2, Files, Folder, GitBranch, LayoutDashboard,
  ListTodo, Plus, Search, ShieldCheck, TerminalSquare, Workflow,
  Zap, Play, Pause, GitFork, Check, Circle, AlertCircle
} from "lucide-react";

type NavItem = "explorer" | "agent" | "tasks" | "approvals";
type ProjectEntry = { name: string; path: string; is_dir: boolean };
type AgentRun = { id: string; name: string; role: string; task: string; status: "running" | "done" | "queued"; progress: number; output: string };
type ProcessEvent = { id: string; time: string; kind: "info" | "success" | "warning"; agent: string; message: string };

const agentRuns: AgentRun[] = [
  { id: "agent-orchestrator", name: "Orquestrador", role: "Coordenação", task: "Distribuir etapas e dependências", status: "done", progress: 100, output: "Plano de execução definido e dividido em três frentes." },
  { id: "agent-frontend", name: "Frontend", role: "Interface", task: "Construir componentes do Studio", status: "running", progress: 68, output: "Estrutura visual e painel de processos em implementação." },
  { id: "agent-runtime", name: "Runtime", role: "Integração Python", task: "Preparar eventos do runtime", status: "running", progress: 42, output: "Contrato de eventos em preparação; execução real ainda desconectada." },
  { id: "agent-review", name: "Revisor", role: "Qualidade", task: "Revisar alterações", status: "queued", progress: 0, output: "Aguardando a conclusão das frentes de implementação." }
];

const processEvents: ProcessEvent[] = [
  { id: "evt-05", time: "12:43:49", kind: "info", agent: "Frontend", message: "Atualizando o inspetor de processos." },
  { id: "evt-04", time: "12:43:22", kind: "success", agent: "Orquestrador", message: "Dependências das tarefas foram definidas." },
  { id: "evt-03", time: "12:42:58", kind: "info", agent: "Runtime", message: "Contrato de eventos preparado para integração." }
];

const tasks = [
  { id: "T-024", title: "Mapear arquitetura do projeto", status: "Concluído", tone: "done" },
  { id: "T-025", title: "Criar shell desktop", status: "Em execução", tone: "active" },
  { id: "T-026", title: "Conectar runtime Python", status: "Na fila", tone: "queued" }
];

function App() {
  const [activeNav, setActiveNav] = useState<NavItem>("explorer");
  const [selectedFile, setSelectedFile] = useState("App.tsx");
  const [bottomPanel, setBottomPanel] = useState("Atividades");
  const [projectRoot, setProjectRoot] = useState<string | null>(null);
  const [projectEntries, setProjectEntries] = useState<ProjectEntry[]>([]);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [openedPath, setOpenedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [selectedProcess, setSelectedProcess] = useState("implement");
  const [selectedAgent, setSelectedAgent] = useState("agent-frontend");
  const [expandedProcesses, setExpandedProcesses] = useState<string[]>(["implement"]);
  const [instructionDraft, setInstructionDraft] = useState("");
  const [runtimeStatus, setRuntimeStatus] = useState<"offline" | "connecting" | "online">("offline");
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [runtimeEvents, setRuntimeEvents] = useState<Array<{ id: string; type: string; message: string }>>([]);
  const [pendingApproval, setPendingApproval] = useState<{ approvalId: string; kind: "plan" | "high_risk"; summary: string; risk: string } | null>(null);

  useEffect(() => {
    let active = true;
    const unlisten = listen<Record<string, any>>("runtime-message", event => {
      if (!active) return;
      const message = event.payload;
      if (message.type === "runtime.ready") {
        setRuntimeStatus("online");
        setRuntimeError(null);
      } else if (message.type === "approval.requested") {
        setPendingApproval({ approvalId: message.payload.approval_id, kind: message.payload.kind, summary: message.payload.summary, risk: message.payload.risk });
        setRuntimeEvents(items => [...items, { id: message.event_id, type: message.type, message: "Plano aguardando sua aprovação." }].slice(-100));
      } else if (message.type === "core.event") {
        setRuntimeEvents(items => [...items, { id: message.event_id, type: message.payload.event_type, message: message.payload.message }].slice(-100));
      } else if (message.type === "task.created") {
        setRuntimeEvents(items => [...items, { id: message.event_id, type: message.type, message: message.payload.title }].slice(-100));
      } else if (message.type === "task.completed" || message.type === "task.failed") {
        setRuntimeEvents(items => [...items, { id: message.event_id, type: message.type, message: message.payload.result_summary || message.payload.message }].slice(-100));
        setPendingApproval(null);
      } else if (message.type === "runtime.error" || message.type === "command.rejected") {
        setRuntimeError(message.message || message.reason || "Falha de comunicação com o runtime.");
      }
    });
    const streamClosed = listen("runtime-stream-closed", () => { if (active) setRuntimeStatus("offline"); });
    return () => {
      active = false;
      unlisten.then(stop => stop());
      streamClosed.then(stop => stop());
      void invoke("stop_runtime").catch(() => undefined);
    };
  }, []);

  async function openEntry(entry: ProjectEntry) {
    if (entry.is_dir) {
      try { setProjectEntries(await invoke<ProjectEntry[]>("read_project_entries", { projectPath: entry.path })); setProjectError(null); }
      catch (error) { setProjectError(String(error)); }
      return;
    }
    try { setFileContent(await invoke<string>("read_project_file", { filePath: entry.path })); setOpenedPath(entry.path); setSelectedFile(entry.name); setProjectError(null); }
    catch (error) { setProjectError(String(error)); }
  }

  async function openProject() {
    const selected = await open({ directory: true, multiple: false, title: "Abrir projeto" });
    if (typeof selected !== "string") return;
    try {
      const entries = await invoke<ProjectEntry[]>("read_project_entries", { projectPath: selected });
      setProjectRoot(selected);
      setProjectEntries(entries);
      setProjectError(null);
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
      setInstructionDraft("");
      setRuntimeError(null);
    } catch (error) { setRuntimeError(String(error)); }
  }

  async function respondToApproval(decision: "approve" | "reject") {
    if (!pendingApproval) return;
    try {
      await invoke("send_runtime_command", { message: {
        type: "approval.respond", schema_version: 1, command_id: crypto.randomUUID(),
        payload: { approval_id: pendingApproval.approvalId, decision }
      }});
      setPendingApproval(null);
    } catch (error) { setRuntimeError(String(error)); }
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
                <div className="tree-row"><ChevronDown/><Folder/><span>frontend</span></div>
                <div className="tree-row indent"><ChevronDown/><Folder/><span>src</span></div>
                <button className={selectedFile === "App.tsx" ? "tree-row file selected-file indent2" : "tree-row file indent2"} onClick={() => setSelectedFile("App.tsx")}><FileCode2/><span>App.tsx</span></button>
                <button className={selectedFile === "main.tsx" ? "tree-row file selected-file indent2" : "tree-row file indent2"} onClick={() => setSelectedFile("main.tsx")}><FileCode2/><span>main.tsx</span></button>
                <button className={selectedFile === "styles.css" ? "tree-row file selected-file indent2" : "tree-row file indent2"} onClick={() => setSelectedFile("styles.css")}><FileCode2/><span>styles.css</span></button>
                {projectEntries.map(entry => <button className="tree-row file" key={entry.path} title={entry.path} onClick={() => openEntry(entry)}>{entry.is_dir ? <Folder/> : <FileCode2/>}<span>{entry.name}</span></button>)}
                {projectError && <div className="tree-row" role="alert">{projectError}</div>}
              </div>
              <div className="sidebar-bottom"><Search size={14}/> Buscar nos arquivos <span className="shortcut">⌘P</span></div>
            </>
          ) : activeNav === "tasks" ? (
            <div className="task-sidebar">{tasks.map(task => <div className="task-mini" key={task.id}><CircleDot className={task.tone}/><div><strong>{task.title}</strong><small>{task.id} · {task.status}</small></div></div>)}</div>
          ) : activeNav === "approvals" ? <div className="empty-state"><ShieldCheck/><strong>Nenhuma aprovação pendente</strong><small>Solicitações aparecerão aqui.</small></div> :
          <div className="empty-state"><Bot/><strong>Agent Workspace</strong><small>Conecte o core para iniciar tarefas.</small></div>}
        </aside>

        <section className="center-pane">
          {activeNav === "agent" ? (
            <div className="manager-inbox">
              <div className="process-toolbar"><div><span className="eyebrow">CENTRAL DE COMANDO</span><h2>Gestor de tarefas</h2><p>Envie uma instrução em linguagem natural. O gestor será responsável por planejar, decompor e distribuir o trabalho aos agentes.</p></div><span className="runtime-offline">CORE OFFLINE</span></div>
              <section className="manager-inbox-card">
                <div className="manager-inbox-icon"><Bot size={22}/></div>
                <div><strong>Aguardando a primeira instrução</strong><p>As solicitações enviadas aparecerão aqui com seu estado: recebida, em planejamento, aguardando aprovação, em execução ou concluída.</p></div>
              </section>
              <div className="manager-flow">
                <div><span>01</span><strong>Receber</strong><small>O gestor recebe seu objetivo e o contexto do projeto.</small></div>
                <div><span>02</span><strong>Planejar</strong><small>O core Python cria um plano e solicita sua aprovação.</small></div>
                <div><span>03</span><strong>Distribuir</strong><small>As tarefas são atribuídas aos agentes conforme dependências e recursos.</small></div>
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
              <div className="process-toolbar"><div><span className="eyebrow">ORQUESTRAÇÃO</span><h2>Fluxo de execução</h2><p>Visão de processos e dependências da tarefa selecionada</p></div><button className="process-filter"><Circle size={9}/> Todas as etapas <ChevronDown size={13}/></button></div>
              <div className="process-summary"><div><span>PROCESSOS</span><strong>05</strong><small>1 ativo · 2 concluídos · 2 aguardando</small></div><div><span>PROGRESSO</span><strong>68%</strong><div className="progress-track"><i style={{width:"68%"}}/></div></div><div><span>TEMPO DECORRIDO</span><strong>02m 41s</strong><small>Última atualização há 3s</small></div></div>
              <div className="flow-canvas">
                <div className="flow-grid"/>
                <div className="flow-label">T-025 · Criar shell desktop <span className="pill-running">EM EXECUÇÃO</span></div>
                <div className="flow-line line-a"/><div className="flow-line line-b"/><div className="flow-line line-c"/><div className="flow-line line-d"/>
                <button className={`flow-node node-root ${selectedProcess === "plan" ? "node-selected" : ""}`} onClick={() => setSelectedProcess("plan")}><div className="node-icon"><Workflow size={16}/></div><div><strong>Planejamento</strong><small>Plano definido</small></div><Check size={14} className="node-check"/></button>
                <button className={`flow-node node-active ${selectedProcess === "implement" ? "node-selected" : ""}`} onClick={() => setSelectedProcess("implement")}><div className="node-icon"><Bot size={16}/></div><div><strong>Implementar UI</strong><small>Agente principal · 68%</small></div><span className="node-spinner"/></button>
                <button className={`flow-node node-done ${selectedProcess === "structure" ? "node-selected" : ""}`} onClick={() => setSelectedProcess("structure")}><div className="node-icon"><FileCode2 size={16}/></div><div><strong>Estrutura base</strong><small>Concluído · 34s</small></div><Check size={14} className="node-check"/></button>
                <button className={`flow-node node-wait ${selectedProcess === "review" ? "node-selected" : ""}`} onClick={() => setSelectedProcess("review")}><div className="node-icon"><ShieldCheck size={16}/></div><div><strong>Revisão</strong><small>Aguardando etapa anterior</small></div><Clock3 size={14} className="node-clock"/></button>
                <button className={`flow-node node-final ${selectedProcess === "finish" ? "node-selected" : ""}`} onClick={() => setSelectedProcess("finish")}><div className="node-icon"><CheckCircle2 size={16}/></div><div><strong>Finalização</strong><small>Na fila</small></div></button>
                <div className="canvas-legend"><span><i className="legend-dot done"/> Concluído</span><span><i className="legend-dot active"/> Em execução</span><span><i className="legend-dot wait"/> Aguardando</span></div>
              </div>
              <section className="agent-team">
                <div className="process-section-title"><strong>Equipe de agentes</strong><span>Execução paralela · 4 agentes</span></div>
                <div className="agent-lanes">
                  {agentRuns.map(agent => <button key={agent.id} className={"agent-lane " + (selectedAgent === agent.id ? "agent-lane-selected" : "")} onClick={() => setSelectedAgent(agent.id)}>
                    <div className="agent-lane-top"><span className={"agent-avatar-small " + agent.status}><Bot size={14}/></span><span className="agent-lane-identity"><strong>{agent.name}</strong><small>{agent.role}</small></span><span className={"agent-state state-" + agent.status}>{agent.status === "running" ? "ATIVO" : agent.status === "done" ? "CONCLUÍDO" : "NA FILA"}</span></div>
                    <div className="agent-lane-task">{agent.task}</div>
                    <div className="agent-progress"><i style={{width: agent.progress + "%"}}/></div>
                    <div className="agent-lane-foot"><span>{agent.status === "running" ? "Trabalhando" : agent.status === "done" ? "Finalizado" : "Aguardando"}</span><strong>{agent.progress}%</strong></div>
                  </button>)}
                </div>
              </section>
              <div className="process-bottom"><div className="process-section-title"><strong>Etapas da execução</strong><span>5 etapas · selecione para inspecionar</span></div>
                <button className={`process-step ${selectedProcess === "plan" ? "selected" : ""}`} onClick={() => setSelectedProcess("plan")}><span className="step-state done"><Check size={13}/></span><div><strong>Planejamento e decomposição</strong><small>Agente coordenador · 12:41:08</small></div><span className="step-duration">18s</span></button>
                <button className={`process-step ${selectedProcess === "implement" ? "selected" : ""}`} onClick={() => setSelectedProcess("implement")}><span className="step-state active"><Play size={11}/></span><div><strong>Implementar UI</strong><small>Agente principal · trabalhando em App.tsx</small></div><span className="step-duration">68%</span></button>
                <button className={`process-step ${selectedProcess === "structure" ? "selected" : ""}`} onClick={() => setSelectedProcess("structure")}><span className="step-state done"><Check size={13}/></span><div><strong>Estrutura base</strong><small>Agente frontend · concluído</small></div><span className="step-duration">34s</span></button>
                <button className={`process-step ${selectedProcess === "review" ? "selected" : ""}`} onClick={() => setSelectedProcess("review")}><span className="step-state wait"><Clock3 size={12}/></span><div><strong>Revisão de alterações</strong><small>Dependência: Implementar UI</small></div><span className="step-duration">—</span></button>
              </div>
              <section className="process-events">
                <div className="process-section-title"><strong>Eventos recentes</strong><span>Feed local demonstrativo · sem polling</span></div>
                {processEvents.map(event => <div className="process-event" key={event.id}><span className={"event-marker " + event.kind}/><time>{event.time}</time><strong>{event.agent}</strong><span>{event.message}</span></div>)}
              </section>
            </div>
          ) : (
            <>
              <div className="tabs"><div className="tab active"><FileCode2 size={14}/>{selectedFile}<span className="tab-close">×</span></div><div className="tab-placeholder">KardecAgent Studio</div></div>
              <div className="editor">
                {openedPath ? <div className="file-view"><div className="file-path">{openedPath}</div><pre>{fileContent}</pre></div> : <div className="welcome"><div className="welcome-icon"><Zap size={24}/></div><h1>KardecAgent <span>Studio</span></h1><p>Seu ambiente de desenvolvimento orientado por agentes.</p><div className="welcome-actions"><button onClick={openProject}><Folder size={15}/> Abrir projeto</button><button className="secondary" onClick={() => setActiveNav("tasks")}><Plus size={15}/> Ver processos</button></div><div className="quick-grid"><div><span className="quick-label">WORKSPACE</span><strong>{projectRoot ? projectRoot.replaceAll("\\\\", "/").split("/").filter(Boolean).pop() : "Projeto local"}</strong><small>{projectRoot || "Selecione uma pasta para começar"}</small></div><div><span className="quick-label">RUNTIME</span><strong className="status-muted">Desconectado</strong><small>Core Python não iniciado</small></div></div></div>}
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
                <span className="eyebrow">T-025 / ETAPA</span>
                <h3>{({plan:"Planejamento",implement:"Implementar UI",structure:"Estrutura base",review:"Revisão de alterações",finish:"Finalização"} as Record<string,string>)[selectedProcess]}</h3>
                <span className={`inspector-status status-${selectedProcess === "implement" ? "active" : selectedProcess === "plan" || selectedProcess === "structure" ? "done" : "wait"}`}>{selectedProcess === "implement" ? "EM EXECUÇÃO" : selectedProcess === "plan" || selectedProcess === "structure" ? "CONCLUÍDO" : "AGUARDANDO"}</span>
                <div className="inspector-metrics"><div><small>AGENTE</small><strong>{selectedProcess === "plan" ? "Coordenador" : selectedProcess === "structure" ? "Frontend" : "Agente principal"}</strong></div><div><small>DURAÇÃO</small><strong>{selectedProcess === "plan" ? "18s" : selectedProcess === "structure" ? "34s" : selectedProcess === "implement" ? "02m 41s" : "—"}</strong></div></div>
                <div className="inspector-divider"/>
                <div className="inspector-section-head"><strong>Subprocessos</strong><button onClick={() => setExpandedProcesses(current => current.includes(selectedProcess) ? current.filter(id => id !== selectedProcess) : [...current, selectedProcess])}>{expandedProcesses.includes(selectedProcess) ? "Recolher" : "Expandir"} <ChevronDown size={12}/></button></div>
                {expandedProcesses.includes(selectedProcess) ? <div className="subprocess-list"><div><span className="subprocess-dot complete"/><section><strong>{selectedProcess === "plan" ? "Analisar contexto" : "Preparar contexto"}</strong><small>Concluído · etapa interna</small></section><Check size={13}/></div><div><span className={`subprocess-dot ${selectedProcess === "implement" ? "running" : "queued"}`}/><section><strong>{selectedProcess === "implement" ? "Construir componentes" : "Validar dependências"}</strong><small>{selectedProcess === "implement" ? "Executando · agente frontend" : "Na fila"}</small></section>{selectedProcess === "implement" ? <span className="node-spinner"/> : <Clock3 size={13}/>}</div><div><span className="subprocess-dot queued"/><section><strong>Registrar resultados</strong><small>Dependente da etapa anterior</small></section><Clock3 size={13}/></div></div> : <p className="inspector-hint">Subprocessos recolhidos. Expanda para ver as atividades internas.</p>}
                <div className="inspector-divider"/>
                <div className="inspector-section-head"><strong>Agente selecionado</strong></div>
                {agentRuns.filter(agent => agent.id === selectedAgent).map(agent => <div className="selected-agent-card" key={agent.id}><div className={"agent-avatar-small " + agent.status}><Bot size={15}/></div><div><strong>{agent.name}</strong><small>{agent.role} · {agent.status === "running" ? "Ativo" : agent.status === "done" ? "Concluído" : "Na fila"}</small></div></div>)}
                <div className="inspector-output">{agentRuns.find(agent => agent.id === selectedAgent)?.output}</div>
                <div className="inspector-divider"/>
                <div className="inspector-section-head"><strong>Contexto e recursos</strong></div>
                <div className="resource-row"><FileCode2 size={14}/><span>frontend/src/App.tsx</span></div><div className="resource-row"><Files size={14}/><span>Arquivos de interface</span></div>
                <div className="inspector-note"><AlertCircle size={14}/><span>Dados demonstrativos. A conexão com eventos reais será adicionada na integração do runtime.</span></div>
              </div>
            </div>
          ) : <>
            <div className="agent-header"><div><Bot size={17}/><strong>Agent</strong></div><span className="live-dot">● Offline</span></div>
            {pendingApproval ? <div className="approval-prompt">
              <div className="approval-prompt-head"><ShieldCheck size={18}/><strong>{pendingApproval.kind === "high_risk" ? "Aprovação adicional de alto risco" : "Aprovar plano de execução"}</strong></div>
              <span className="approval-risk">RISCO: {pendingApproval.risk.toUpperCase()}</span>
              <pre>{pendingApproval.summary}</pre>
              <div className="approval-actions"><button onClick={() => respondToApproval("reject")} className="approval-reject">Rejeitar</button><button onClick={() => respondToApproval("approve")} className="approval-accept">Aprovar execução</button></div>
            </div> : <div className="agent-empty"><div className="agent-orb"><Bot size={24}/></div><strong>Pronto para trabalhar</strong><p>Abra um projeto e descreva o que deseja construir. O KardecAgent planeja, executa e valida com você no controle.</p></div>}
            <div className="agent-composer">
              <label htmlFor="manager-instruction">Instrução para o gestor</label>
              <textarea id="manager-instruction" value={instructionDraft} onChange={event => setInstructionDraft(event.target.value.slice(0, 8000))} placeholder="Ex.: Analise o projeto, proponha um plano e implemente a autenticação..." rows={4} maxLength={8000}/>
              <div className="composer-footer"><span>{instructionDraft.length}/8000 · {runtimeStatus === "online" ? "Pronto para enviar" : "Aguardando conexão"}</span><button onClick={submitInstruction} disabled={!projectRoot || !instructionDraft.trim() || runtimeStatus !== "online"} title={runtimeStatus === "online" ? "Enviar instrução ao gestor" : "Runtime Python ainda não conectado"}><Plus size={15}/></button></div>
              <small className="composer-hint">A instrução será enviada ao gestor central, não diretamente a um agente individual.</small>
            </div>
            <div className="agent-foot"><ShieldCheck size={13}/> Aprovações e ações sensíveis sob seu controle</div>
          </>}
        </aside>
      </div>

      <footer className="statusbar"><div><GitBranch size={12}/> feat/kardecagent-studio <span>·</span><CheckCircle2 size={12}/> {projectRoot ? "Projeto aberto" : "Workspace local"}</div><div><span className="status-indicator"/> UI Preview <span>·</span> {runtimeStatus === "online" ? "Runtime conectado" : runtimeStatus === "connecting" ? "Conectando runtime" : "Runtime offline"}</div></footer>
    </main>
  );
}

export default App;
