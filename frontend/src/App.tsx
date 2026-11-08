import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import {
  Activity, Bot, CheckCircle2, ChevronDown, ChevronRight, CircleDot,
  Clock3, FileCode2, Files, Folder, GitBranch, LayoutDashboard,
  ListTodo, Plus, Search, ShieldCheck, TerminalSquare, Workflow,
  Zap
} from "lucide-react";

type NavItem = "explorer" | "agent" | "tasks" | "approvals";
type ProjectEntry = { name: string; path: string; is_dir: boolean };

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

  async function openProject() {
    const selected = await open({ directory: true, multiple: false, title: "Abrir projeto" });
    if (typeof selected !== "string") return;
    try {
      const entries = await invoke<ProjectEntry[]>("read_project_entries", { projectPath: selected });
      setProjectRoot(selected);
      setProjectEntries(entries);
      setProjectError(null);
    } catch (error) { setProjectError(String(error)); }
  }

  return (
    <main className="app-shell">
      <header className="titlebar">
        <div className="brand"><div className="brand-mark"><Zap size={15} /></div><span>KARDEC<span className="brand-light">AGENT</span></span>
        <span className="workspace-label">Studio <span className="muted">/</span> Workspace</span>
        <div className="titlebar-right"><span className="connection"><span /> {projectRoot ? "Projeto aberto · Core offline" : "Core desconectado"}</span><span className="avatar">KA</span></div>
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
                {projectEntries.map(entry => <div className="tree-row file" key={entry.path} title={entry.path}>{entry.is_dir ? <Folder/> : <FileCode2/>}<span>{entry.name}</span></div>)}
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
          <div className="tabs"><div className="tab active"><FileCode2 size={14}/>{selectedFile}<span className="tab-close">×</span></div><div className="tab-placeholder">KardecAgent Studio</div></div>
          <div className="editor">
            <div className="welcome">
              <div className="welcome-icon"><Zap size={24}/></div>
              <h1>KardecAgent <span>Studio</span></h1>
              <p>Seu ambiente de desenvolvimento orientado por agentes.</p>
              <div className="welcome-actions"><button onClick={openProject}><Folder size={15}/> Abrir projeto</button><button className="secondary"><Plus size={15}/> Nova tarefa</button></div>
              <div className="quick-grid">
                <div><span className="quick-label">WORKSPACE</span><strong>{projectRoot ? projectRoot.replaceAll("\\\\", "/").split("/").filter(Boolean).pop() : "Projeto local"}</strong><small>{projectRoot || "Selecione uma pasta para começar"}</small></div>
                <div><span className="quick-label">RUNTIME</span><strong className="status-muted">Desconectado</strong><small>Core Python não iniciado</small></div>
              </div>
            </div>
          </div>
          <div className="bottom-panel">
            <div className="panel-tabs">{["Atividades","Output","Alterações"].map(tab => <button key={tab} className={bottomPanel === tab ? "panel-tab active" : "panel-tab"} onClick={() => setBottomPanel(tab)}>{tab}</button>)}<button className="panel-collapse"><ChevronDown size={14}/></button></div>
            <div className="panel-content"><Clock3 size={14}/><span>Eventos do runtime aparecerão aqui, sem polling contínuo.</span></div>
          </div>
        </section>

        <aside className="agent-panel">
          <div className="agent-header"><div><Bot size={17}/><strong>Agent</strong></div><span className="live-dot">● Offline</span></div>
          <div className="agent-empty"><div className="agent-orb"><Bot size={24}/></div><strong>Pronto para trabalhar</strong><p>Abra um projeto e descreva o que deseja construir. O KardecAgent planeja, executa e valida com você no controle.</p></div>
          <div className="agent-composer"><textarea placeholder="Descreva uma tarefa para o agente..." rows={3}/><div className="composer-footer"><span>Enter para enviar</span><button disabled title="Core ainda não conectado"><Plus size={15}/></button></div></div>
          <div className="agent-foot"><ShieldCheck size={13}/> Aprovações e ações sensíveis sob seu controle</div>
        </aside>
      </div>

      <footer className="statusbar"><div><GitBranch size={12}/> feat/kardecagent-studio <span>·</span><CheckCircle2 size={12}/> {projectRoot ? "Projeto aberto" : "Workspace local"}</div><div><span className="status-indicator"/> UI Preview <span>·</span> Runtime não conectado</div></footer>
    </main>
  );
}

export default App;
