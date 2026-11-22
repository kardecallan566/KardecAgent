import { type ReactNode, useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import type { RuntimeLimits, RuntimePlanStep, RuntimeSubtask } from "./types/runtime";
import {
  Activity, Bot, CheckCircle2, ChevronDown, ChevronRight, CircleDot,
  Clock3, FileCode2, Files, Folder, FolderOpen, GitBranch,
  ListTodo, Plus, Search, ShieldCheck, Workflow, Save,
  Zap, GitFork, Circle, AlertCircle, Settings, Wifi, RefreshCw, X, CheckCircle, Play, FilePlus2, FolderPlus, Terminal, Send, Square
} from "lucide-react";

type NavItem = "explorer" | "agent" | "tasks" | "approvals";
type StudioSettings = {
  ollamaUrl: string; model: string; timeout: number; pythonPath: string; autoStartCore: boolean;
  editorFontSize: number; editorFontFamily: string; lineHeight: number; tabSize: number; wordWrap: boolean; showLineNumbers: boolean;
  shell: "powershell" | "cmd"; terminalFontSize: number;
  uiTheme: "dark" | "midnight" | "light"; uiFontFamily: string; uiFontSize: number; accentColor: "violet" | "cyan" | "blue" | "green";
};
type SettingsSection = "core" | "editor" | "appearance" | "terminal" | "memory" | "diagnostics";
type CoreDiagnostics = { running: boolean; pid: number | null; python_executable: string | null; launch_mode: string | null; project_root: string | null; last_exit_code: number | null };
const DEFAULT_SETTINGS: StudioSettings = {
  ollamaUrl: "http://127.0.0.1:11434", model: "qwen2.5-coder:3b", timeout: 180, pythonPath: "", autoStartCore: true,
  editorFontSize: 13, editorFontFamily: "Shantell Sans", lineHeight: 1.6, tabSize: 2, wordWrap: false, showLineNumbers: true,
  shell: "powershell", terminalFontSize: 12,
  uiTheme: "dark", uiFontFamily: "Shantell Sans", uiFontSize: 13, accentColor: "violet"
};
type ProjectEntry = { name: string; path: string; is_dir: boolean };

function ExplorerEntry({ entry, projectRoot, onOpenFile }: { entry: ProjectEntry; projectRoot: string; onOpenFile: (entry: ProjectEntry) => void }) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<ProjectEntry[]>([]);
  const [loading, setLoading] = useState(false);
  async function toggleFolder() {
    if (!entry.is_dir) { onOpenFile(entry); return; }
    if (expanded) { setExpanded(false); return; }
    setLoading(true);
    try {
      const result = await invoke<ProjectEntry[]>("read_project_entries", { projectRoot, projectPath: entry.path });
      setChildren(result); setExpanded(true);
    } catch (error) { console.error("Falha ao expandir pasta", error); }
    finally { setLoading(false); }
  }
  return <><button className={entry.is_dir ? "tree-row folder-entry" : "tree-row file"} onClick={toggleFolder} title={entry.path}>
    {entry.is_dir ? (expanded ? <ChevronDown/> : <ChevronRight/>) : <span className="tree-spacer"/>}
    {entry.is_dir ? (expanded ? <FolderOpen/> : <Folder/>) : <FileCode2/>}<span>{entry.name}</span>{loading && <small className="tree-loading">...</small>}
  </button>{expanded && <div className="tree-children">{children.map(child => <ExplorerEntry key={child.path} entry={child} projectRoot={projectRoot} onOpenFile={onOpenFile}/>)}</div>}</>;
}


function highlightSource(source: string, extension: string) {
  const language = extension.toLowerCase();
  const patterns = language === "json"
    ? /(\/\/.*$|\/\*[\s\S]*?\*\/|"(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?\b)/gm
    : /(#[^\n]*|\/\/.*$|\/\*[\s\S]*?\*\/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[^`]*`|\b(?:const|let|var|function|return|if|else|for|while|class|import|from|export|default|async|await|try|catch|throw|new|this|type|interface|extends|implements|def|pass|in|is|not|and|or|None|True|False|lambda|with|as|yield|pub|fn|mut|struct|enum|impl|use|mod|match|Some|Ok|Err)\b|\b\d+(?:\.\d+)?\b)/gm;
  const nodes: ReactNode[] = [];
  let cursor = 0, match: RegExpExecArray | null;
  while ((match = patterns.exec(source)) !== null) {
    if (match.index > cursor) nodes.push(source.slice(cursor, match.index));
    const token = match[0];
    const kind = token.startsWith("#") || token.startsWith("//") || token.startsWith("/*") ? "comment"
      : token.startsWith('"') || token.startsWith("'") || token.startsWith("`") ? (language === "json" && source.slice(match.index + token.length).trimStart().startsWith(":") ? "property" : "string")
      : /^\d/.test(token) ? "number" : "keyword";
    nodes.push(<span className={"syntax-" + kind} key={match.index}>{token}</span>);
    cursor = match.index + token.length;
  }
  if (cursor < source.length) nodes.push(source.slice(cursor));
  return nodes.length ? nodes : source;
}

function importedGoogleFontNames(): string[] {
  try {
    const fonts = JSON.parse(localStorage.getItem("kardecagent.studio.googleFonts") || "{}");
    return fonts && typeof fonts === "object" ? Object.keys(fonts) : [];
  } catch { return []; }
}

function taskStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "Pendente",
    running: "Em execução",
    verifying: "Validando",
    verified: "Verificada",
    completed: "Concluída",
    failed: "Falhou",
    max_iterations: "Limite de iterações",
    recovering: "Recuperando",
    resuming: "Retomando",
  };
  return labels[status] || status;
}

function App() {
  const [activeNav, setActiveNav] = useState<NavItem>("explorer");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("core");
  const [settings, setSettings] = useState<StudioSettings>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("kardecagent.studio.settings") || "{}");
      return {
        ...DEFAULT_SETTINGS, ...saved,
        uiFontFamily: saved.uiFontFamily === "Inter" ? "Shantell Sans" : saved.uiFontFamily || DEFAULT_SETTINGS.uiFontFamily,
        editorFontFamily: saved.editorFontFamily === "Cascadia Code" ? "Shantell Sans" : saved.editorFontFamily || DEFAULT_SETTINGS.editorFontFamily
      };
    } catch { return DEFAULT_SETTINGS; }
  });
  const [coreDiagnostics, setCoreDiagnostics] = useState<CoreDiagnostics | null>(null);
  const startupTimer = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const [googleFontUrl, setGoogleFontUrl] = useState("https://fonts.google.com/specimen/Shantell+Sans");
  const [googleFontStatus, setGoogleFontStatus] = useState<string | null>(null);
  const [googleFontBusy, setGoogleFontBusy] = useState(false);
  const [googleFontRevision, setGoogleFontRevision] = useState(0);
  const [coreDiagnosticsError, setCoreDiagnosticsError] = useState<string | null>(null);
  const [memoryItems, setMemoryItems] = useState<Array<{ id: string; scope: "user" | "project"; category: string; content: string; updated_at: string }>>([]);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryError, setMemoryError] = useState<string | null>(null);
  const [ollamaTest, setOllamaTest] = useState<{ ok: boolean; message: string; version?: string; models?: string[] } | null>(null);
  const [ollamaTesting, setOllamaTesting] = useState(false);
  const [terminalReady, setTerminalReady] = useState(false);
  const [terminalInput, setTerminalInput] = useState("");
  const [terminalOutput, setTerminalOutput] = useState<string[]>([]);
  const [selectedFile, setSelectedFile] = useState("App.tsx");
  const [bottomPanel, setBottomPanel] = useState("Atividades");
  const [projectRoot, setProjectRoot] = useState<string | null>(null);
  const [projectEntries, setProjectEntries] = useState<ProjectEntry[]>([]);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [openedPath, setOpenedPath] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [fileDirty, setFileDirty] = useState(false);
  const [fileSaving, setFileSaving] = useState(false);
  const [runningFile, setRunningFile] = useState(false);
  const [runOutput, setRunOutput] = useState<{ file: string; language: string; exit_code: number; stdout: string; stderr: string; timed_out: boolean } | null>(null);
  const [openTabs, setOpenTabs] = useState<Array<{ path: string; name: string; content: string; dirty: boolean }>>([]);
  const [instructionDraft, setInstructionDraft] = useState("");
  const [chatMessages, setChatMessages] = useState<Array<{ id: string; commandId?: string; role: "user" | "assistant"; text: string; status?: "sending" | "sent" | "rejected" }>>([]);
  const pendingInstructionCommandId = useRef<string | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<"offline" | "connecting" | "online">("offline");
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [runtimeEvents, setRuntimeEvents] = useState<Array<{ id: string; type: string; message: string }>>([]);
  const [eventSearch, setEventSearch] = useState("");
  const filteredEvents = runtimeEvents.filter(event => `${event.type} ${event.message}`.toLowerCase().includes(eventSearch.trim().toLowerCase()));
  const [runtimeTasks, setRuntimeTasks] = useState<Array<{ id: string; executionId: string; title: string; status: string; recoverable?: boolean }>>([]);
  const [runtimeSubtasks, setRuntimeSubtasks] = useState<RuntimeSubtask[]>([]);
  const [runtimePlanSteps, setRuntimePlanSteps] = useState<RuntimePlanStep[]>([]);
  const [activeRuntimeSubtask, setActiveRuntimeSubtask] = useState<string | null>(null);
  const [activeRuntimeStep, setActiveRuntimeStep] = useState<number | null>(null);
  const [runtimeLimits, setRuntimeLimits] = useState<RuntimeLimits | null>(null);
  const [pendingApproval, setPendingApproval] = useState<{ approvalId: string; kind: "plan" | "high_risk"; summary: string; risk: string } | null>(null);
  const [approvalSubmitting, setApprovalSubmitting] = useState(false);
  const seenEventIds = useRef(new Set<string>());
  const approvalCommandId = useRef<string | null>(null);
  const runtimeListenersReady = useRef<Promise<void>>(Promise.resolve());


  useEffect(() => {
    if (!importedGoogleFontNames().includes("Shantell Sans")) {
      setGoogleFontUrl("https://fonts.google.com/specimen/Shantell+Sans");
      void importGoogleFont(false);
    }
  }, []);

  useEffect(() => {
    const savedFonts = (() => { try { return JSON.parse(localStorage.getItem("kardecagent.studio.googleFonts") || "{}") as Record<string, string>; } catch { return {}; } })();
    const style = document.createElement("style");
    style.id = "kardecagent-google-fonts";
    style.textContent = Object.values(savedFonts).join("\n");
    document.head.appendChild(style);
    return () => style.remove();
  }, [googleFontRevision]);

  useEffect(() => () => { if (startupTimer.current) window.clearTimeout(startupTimer.current); }, []);

  useEffect(() => {
    let active = true;
    const output = listen<string>("terminal-output", event => {
      if (active) setTerminalOutput(items => [...items, event.payload].slice(-1000));
    });
    const exited = listen("terminal-exited", () => {
      if (active) { setTerminalReady(false); setTerminalOutput(items => [...items, "[Processo do terminal encerrado.]"]); }
    });
    return () => {
      active = false;
      output.then(stop => stop());
      exited.then(stop => stop());
      void invoke("stop_integrated_terminal").catch(() => undefined);
    };
  }, []);

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
        if (startupTimer.current) window.clearTimeout(startupTimer.current);
        startupTimer.current = null;
        setRuntimeStatus("online");
        setRuntimeLimits(message.limits || null);
        setRuntimeError(null);
      } else if (message.type === "workspace.changed") {
        if (startupTimer.current) window.clearTimeout(startupTimer.current);
        startupTimer.current = null;
        setRuntimeStatus("online");
        setRuntimeError(null);
        void refreshCoreDiagnostics();
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
        if ((coreType === "plan_step_started" || coreType === "subtask_started") && message.execution_id) {
          setRuntimeTasks(items => items.map(task => task.executionId === message.execution_id ? { ...task, status: "Em execução" } : task));
        }
        if (coreType === "plan_step_started" && Number.isInteger(eventData.step)) {
          setActiveRuntimeStep(eventData.step);
          setRuntimePlanSteps(items => items.map(item => item.step === eventData.step ? { ...item, status: "running" } : item));
        }
        if (coreType === "plan_step_completed" && Number.isInteger(eventData.step)) {
          setRuntimePlanSteps(items => items.map(item => item.step === eventData.step ? { ...item, status: "completed" } : item));
          setActiveRuntimeStep(null);
        }
        if (board && Array.isArray(board.subtasks)) {
          setRuntimeSubtasks(board.subtasks);
          const running = board.subtasks.find((item: RuntimeSubtask) => item.status === "running");
          setActiveRuntimeSubtask(running?.id || null);
          setActiveRuntimeStep(running?.plan_steps?.[0] || null);
        }
        if (coreType === "status_changed" && eventData.to_status) {
          setRuntimeTasks(items => items.map(task => (message.execution_id ? task.executionId === message.execution_id : task.id === message.task_id) ? { ...task, status: taskStatusLabel(String(eventData.to_status)) } : task));
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
        setRuntimeSubtasks([]);
        setRuntimePlanSteps([]);
        setActiveRuntimeSubtask(null);
        setActiveRuntimeStep(null);
        setRuntimeTasks(items => [...items, { id: message.task_id, executionId: message.execution_id, title: message.payload.title, status: message.payload?.resumed ? "Retomando" : message.payload?.restored ? "Restaurada" : "Recebida", recoverable: false }].slice(-50));
        setRuntimeEvents(items => [...items, { id: message.event_id, type: message.type, message: message.payload.title }].slice(-100));
      } else if (message.type === "task.completed" || message.type === "task.failed") {
        setRuntimeBusy(false);
        setRuntimeTasks(items => items.map(task => task.executionId === message.execution_id ? { ...task, status: message.type === "task.completed" ? "Concluída" : message.payload?.recoverable ? "Recuperável" : "Falhou", recoverable: message.type === "task.failed" && Boolean(message.payload?.recoverable) } : task));
        setActiveRuntimeSubtask(null);
        setActiveRuntimeStep(null);
        const terminalSummary = message.payload?.result_summary || message.payload?.message || (message.type === "task.completed" ? "Tarefa concluída." : "A tarefa falhou.");
        setChatMessages(items => [...items, { id: message.event_id || crypto.randomUUID(), role: "assistant" as const, text: terminalSummary }].slice(-100));
        setRuntimeEvents(items => [...items, { id: message.event_id, type: message.type, message: terminalSummary }].slice(-100));
        setPendingApproval(null);
        setApprovalSubmitting(false);
      } else if (message.type === "command.accepted" && message.command_id === pendingInstructionCommandId.current) {
        const acceptedId = pendingInstructionCommandId.current;
        pendingInstructionCommandId.current = null;
        setChatMessages(items => items.map(item => item.commandId === acceptedId ? { ...item, status: "sent" } : item));
      } else if (message.type === "memory.snapshot" || message.type === "memory.updated") {
        setMemoryItems(message.payload?.items || []);
        setMemoryLoading(false);
        setMemoryError(null);
      } else if (message.type === "assistant.response") {
        const responseText = message.payload?.message || "Não consegui formular uma resposta para essa mensagem.";
        const responseId = "chat-" + (message.command_id || crypto.randomUUID());
        setChatMessages(items => [...items, { id: responseId, role: "assistant" as const, text: responseText }].slice(-100));
        setRuntimeBusy(false);
        setRuntimeError(null);
        setRuntimeEvents(items => [...items, { id: responseId, type: message.intent === "clarify" ? "intent.clarification" : "chat.response", message: responseText }].slice(-100));
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
        const rejection = message.message || message.reason || "Falha de comunicação com o runtime.";
        if (message.type === "command.rejected" && message.command_id === pendingInstructionCommandId.current) {
          const rejectedId = pendingInstructionCommandId.current;
          pendingInstructionCommandId.current = null;
          setChatMessages(items => items.map(item => item.commandId === rejectedId ? { ...item, status: "rejected", text: item.text + "\n\nNão enviada pelo Core: " + rejection } : item));
          setRuntimeBusy(false);
        }
        setRuntimeError(rejection);
      }
    });
    const streamClosed = listen("runtime-stream-closed", () => {
      if (active) {
        if (startupTimer.current) window.clearTimeout(startupTimer.current);
        startupTimer.current = null;
        void invoke<CoreDiagnostics>("runtime_diagnostics").then(diagnostics => {
          setCoreDiagnostics(diagnostics);
          if (diagnostics.running) return;
          setRuntimeStatus("offline");
          setRuntimeBusy(false);
          setRuntimeSubtasks([]);
          setRuntimePlanSteps([]);
          setActiveRuntimeSubtask(null);
          setActiveRuntimeStep(null);
          setPendingApproval(null);
          setApprovalSubmitting(false);
          approvalCommandId.current = null;
          setRuntimeError(current => current || (diagnostics.last_exit_code === null
            ? "O processo Python encerrou a conexão."
            : "O processo Python encerrou com código " + diagnostics.last_exit_code + "."));
        }).catch(error => {
          setRuntimeStatus("offline");
          setRuntimeError("Não foi possível consultar o estado do Core: " + String(error));
        });
      }
    });
    const stderr = listen<string>("runtime-stderr", event => {
      if (!active || !event.payload.trim()) return;
      const line = event.payload.slice(0, 2000);
      setRuntimeEvents(items => [...items, { id: crypto.randomUUID(), type: "runtime.log", message: line }].slice(-100));
      if (/\b(ERROR|CRITICAL|Traceback|Exception)\b/.test(line)) setRuntimeError(line);
    });
    const protocolError = listen<string>("runtime-protocol-error", event => {
      if (active) setRuntimeError("Falha no protocolo do runtime: " + event.payload.slice(0, 1000));
    });
    runtimeListenersReady.current = Promise.all([unlisten, streamClosed, stderr, protocolError]).then(() => undefined);
    return () => {
      active = false;
      unlisten.then(stop => stop());
      streamClosed.then(stop => stop());
      stderr.then(stop => stop());
      protocolError.then(stop => stop());
      // Listener lifecycle is independent from the Core process lifecycle.
      // The runtime is stopped explicitly when changing workspace or exiting the app.
    };
  }, []);


  useEffect(() => {
    const lastProject = localStorage.getItem("kardecagent.studio.lastProject");
    if (!lastProject) return;
    void (async () => {
      const restored = await activateProject(lastProject);
      if (!restored) return;
      setRuntimeEvents(items => [
        ...items,
        { id: crypto.randomUUID(), type: "workspace.restored", message: "Último projeto restaurado: " + lastProject }
      ].slice(-100));
    })();
  }, []);

  async function startTerminal() {
    if (!projectRoot) { setProjectError("Abra um projeto antes de iniciar o terminal."); return; }
    try {
      await invoke("start_integrated_terminal", { projectPath: projectRoot, shellName: settings.shell });
      setTerminalReady(true);
      setTerminalOutput(items => [...items, "[Terminal iniciado em " + projectRoot + "]"]);
      setBottomPanel("Terminal");
    } catch (error) { setProjectError("Não foi possível iniciar o terminal: " + String(error)); }
  }
  async function sendTerminalCommand() {
    const command = terminalInput;
    if (!command.trim() || !terminalReady) return;
    setTerminalOutput(items => [...items, (settings.shell === "cmd" ? ">" : "PS>") + " " + command]);
    setTerminalInput("");
    try { await invoke("write_integrated_terminal", { input: command }); }
    catch (error) { setTerminalOutput(items => [...items, "Erro: " + String(error)]); }
  }
  async function stopTerminal() {
    try { await invoke("stop_integrated_terminal"); } catch { /* terminal may already have exited */ }
    setTerminalReady(false);
  }

  async function openEntry(entry: ProjectEntry) {
    if (entry.is_dir) return;
    try {
      const existing = openTabs.find(tab => tab.path === entry.path);
      if (existing) { setOpenedPath(existing.path); setSelectedFile(existing.name); setFileContent(existing.content); setFileDirty(existing.dirty); setProjectError(null); setActiveNav("explorer"); return; }
      const content = await invoke<string>("read_project_file", { projectRoot: projectRoot!, filePath: entry.path });
      setOpenTabs(tabs => [...tabs, { path: entry.path, name: entry.name, content, dirty: false }]);
      setFileContent(content); setOpenedPath(entry.path); setSelectedFile(entry.name); setFileDirty(false); setProjectError(null); setActiveNav("explorer");
    }
    catch (error) { setProjectError(String(error)); }
  }

  async function refreshCoreDiagnostics() {
    try {
      const diagnostics = await invoke<CoreDiagnostics>("runtime_diagnostics");
      setCoreDiagnostics(diagnostics);
      setCoreDiagnosticsError(null);
    } catch (error) {
      setCoreDiagnosticsError(String(error));
    }
  }

  function armCoreStartupTimeout() {
    if (startupTimer.current) window.clearTimeout(startupTimer.current);
    startupTimer.current = window.setTimeout(() => {
      void invoke<CoreDiagnostics>("runtime_diagnostics").then(diagnostics => {
        setCoreDiagnostics(diagnostics);
        if (!diagnostics.running || runtimeStatus !== "online") {
          setRuntimeStatus("offline");
          setRuntimeError("O Core não respondeu ao handshake em 45 segundos. Consulte os logs e o modo de inicialização em Configurações → Diagnóstico.");
        }
      }).catch(error => { setRuntimeStatus("offline"); setRuntimeError("Tempo limite ao iniciar o Core: " + String(error)); });
    }, 45000);
  }

  async function restartCore() {
    if (!projectRoot) { setRuntimeError("Abra um projeto antes de iniciar o Core."); return; }
    if (runtimeBusy) { setRuntimeError("Aguarde a tarefa atual terminar antes de reiniciar o Core."); return; }
    try {
      setRuntimeStatus("connecting");
      setRuntimeError(null);
      await invoke("stop_runtime");
      await runtimeListenersReady.current;
      armCoreStartupTimeout();
      await invoke("start_runtime", {
        projectPath: projectRoot, ollamaBaseUrl: settings.ollamaUrl, llmModel: settings.model,
        llmTimeoutSeconds: settings.timeout, pythonExecutable: settings.pythonPath.trim() || null
      });
      await refreshCoreDiagnostics();
    } catch (error) {
      if (startupTimer.current) window.clearTimeout(startupTimer.current);
      startupTimer.current = null;
      setRuntimeStatus("offline");
      setRuntimeError(String(error));
    }
  }

  async function saveAllFiles() {
    if (!projectRoot) return;
    const dirtyTabs = openTabs.filter(tab => tab.dirty);
    if (!dirtyTabs.length) return;
    try {
      for (const tab of dirtyTabs) await invoke("write_project_file", { projectRoot, filePath: tab.path, content: tab.path === openedPath ? fileContent : tab.content });
      setOpenTabs(tabs => tabs.map(tab => ({ ...tab, dirty: false, content: tab.path === openedPath ? fileContent : tab.content })));
      setFileDirty(false); setProjectError(null);
      setRuntimeEvents(items => [...items, { id: crypto.randomUUID(), type: "files.saved", message: dirtyTabs.length + " arquivo(s) salvo(s)." }].slice(-100));
    } catch (error) { setProjectError("Falha ao salvar os arquivos: " + String(error)); }
  }

  async function activateProject(selected: string): Promise<boolean> {
    if (projectRoot === selected) return true;
    if (runtimeBusy) { setRuntimeError("Aguarde a tarefa atual terminar antes de trocar de workspace."); return false; }
    if (projectRoot && projectRoot !== selected && openTabs.some(tab => tab.dirty)) {
      const discard = window.confirm("Existem alterações não salvas. Deseja descartar essas alterações e abrir outro projeto? (Use Salvar tudo antes, se quiser preservá-las.)");
      if (!discard) return false;
    }
    try {
      const entries = await invoke<ProjectEntry[]>("read_project_entries", { projectRoot: selected, projectPath: selected });
      setProjectRoot(selected); setProjectEntries(entries); setProjectError(null);
      try { localStorage.setItem("kardecagent.studio.lastProject", selected); } catch { /* local preference storage may be unavailable */ }
      setOpenedPath(null); setSelectedFile(""); setFileContent(""); setFileDirty(false); setOpenTabs([]); setRunOutput(null);
      setRuntimeEvents([]); setRuntimeTasks([]); setRuntimeSubtasks([]); setRuntimePlanSteps([]); setChatMessages([]); pendingInstructionCommandId.current = null;
      setMemoryItems([]); setMemoryError(null); setMemoryLoading(false);
      setActiveRuntimeSubtask(null); setActiveRuntimeStep(null); setRuntimeLimits(null);
      setRuntimeBusy(false); setPendingApproval(null); setApprovalSubmitting(false);
      seenEventIds.current.clear(); approvalCommandId.current = null; setRuntimeError(null);
      const diagnostics = await invoke<CoreDiagnostics>("runtime_diagnostics");
      setCoreDiagnostics(diagnostics);
      if (diagnostics.running) {
        setRuntimeStatus("connecting"); armCoreStartupTimeout();
        await invoke("switch_runtime_workspace", { projectPath: selected });
      } else if (settings.autoStartCore) {
        setRuntimeStatus("connecting");
        await runtimeListenersReady.current;
        armCoreStartupTimeout();
        await invoke("start_runtime", {
          projectPath: selected, ollamaBaseUrl: settings.ollamaUrl, llmModel: settings.model,
          llmTimeoutSeconds: settings.timeout, pythonExecutable: settings.pythonPath.trim() || null
        });
      } else setRuntimeStatus("offline");
      return true;
    } catch (error) {
      if (startupTimer.current) window.clearTimeout(startupTimer.current);
      startupTimer.current = null;
      setProjectError(String(error)); setRuntimeStatus("offline"); setRuntimeError(String(error));
      return false;
    }
  }

  async function openProject() {
    let selected: string | string[] | null;
    try { selected = await open({ directory: true, multiple: false, title: "Abrir projeto existente" }); }
    catch (error) { setProjectError("Não foi possível abrir o seletor de pastas: " + String(error)); return; }
    if (typeof selected === "string") await activateProject(selected);
  }

  async function createNewProject() {
    let parent: string | string[] | null;
    try { parent = await open({ directory: true, multiple: false, title: "Escolha onde criar o novo projeto" }); }
    catch (error) { setProjectError("Não foi possível escolher a pasta de destino: " + String(error)); return; }
    if (typeof parent !== "string") return;
    const name = window.prompt("Nome da nova pasta do projeto:");
    if (!name?.trim()) return;
    const folderName = name.trim();
    if (folderName === "." || folderName === ".." || /[\\\\/<>:"|?*]/.test(folderName)) {
      setProjectError("Informe um nome de pasta válido, sem barras ou caracteres reservados."); return;
    }
    try {
      await invoke("create_project_directory", { projectRoot: parent, relativePath: folderName });
      const separator = parent.endsWith("/") || parent.endsWith("\\") ? "" : "/";
      await activateProject(parent + separator + folderName);
    } catch (error) { setProjectError("Não foi possível criar o projeto: " + String(error)); }
  }

  async function importGoogleFont(applyToCurrent = true) {
    if (googleFontBusy) return;
    setGoogleFontBusy(true); setGoogleFontStatus(null);
    try {
      const url = new URL(googleFontUrl.trim());
      if (url.hostname !== "fonts.google.com") throw new Error("Use um link de fonts.google.com.");
      const match = url.pathname.match(/\/specimen\/([^/]+)/);
      const family = decodeURIComponent((match?.[1] || "").replace(/\+/g, " ")).trim();
      if (!family || family.length > 80) throw new Error("Não foi possível identificar a família da fonte no link.");
      const cssUrl = "https://fonts.googleapis.com/css2?family=" + encodeURIComponent(family).replace(/%20/g, "+") + ":wght@300..800&display=swap";
      const cssResponse = await fetch(cssUrl);
      if (!cssResponse.ok) throw new Error("Google Fonts respondeu com HTTP " + cssResponse.status + ".");
      let css = await cssResponse.text();
      const urls = [...new Set([...css.matchAll(/url\((https:\/\/[^)]+)\)/g)].map(match => match[1]))];
      if (!urls.length) throw new Error("O Google Fonts não retornou arquivos WOFF2 para esta fonte.");
      for (const fontUrl of urls) {
        const response = await fetch(fontUrl);
        if (!response.ok) throw new Error("Falha ao baixar um arquivo da fonte (HTTP " + response.status + ").");
        const bytes = new Uint8Array(await response.arrayBuffer());
        let binary = "";
        for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
        css = css.split(fontUrl).join("data:font/woff2;base64," + btoa(binary));
      }
      const saved = (() => { try { return JSON.parse(localStorage.getItem("kardecagent.studio.googleFonts") || "{}") as Record<string, string>; } catch { return {}; } })();
      saved[family] = css;
      localStorage.setItem("kardecagent.studio.googleFonts", JSON.stringify(saved));
      setGoogleFontRevision(value => value + 1);
      if (applyToCurrent) setSettings(current => ({ ...current, uiFontFamily: family, editorFontFamily: family }));
      setGoogleFontStatus(family + " foi baixada e adicionada à biblioteca local de fontes.");
    } catch (error) { setGoogleFontStatus(String(error)); }
    finally { setGoogleFontBusy(false); }
  }

  async function refreshProjectEntries() {
    if (!projectRoot) return;
    const entries = await invoke<ProjectEntry[]>("read_project_entries", { projectRoot, projectPath: projectRoot });
    setProjectEntries(entries);
  }

  async function createProjectItem(kind: "file" | "directory") {
    if (!projectRoot) { setProjectError("Abra um projeto antes de criar arquivos ou pastas."); return; }
    const relativePath = window.prompt(kind === "file" ? "Caminho do novo arquivo (relativo ao projeto):" : "Caminho da nova pasta (relativo ao projeto):");
    if (!relativePath?.trim()) return;
    try {
      if (kind === "file") {
        await invoke("create_project_file", { projectRoot, relativePath: relativePath.trim(), content: "" });
        await refreshProjectEntries();
        const name = relativePath.trim().split(/[\\\\/]/).pop() || relativePath.trim();
        await openEntry({ name, path: projectRoot + (projectRoot.endsWith("\\\\") || projectRoot.endsWith("/") ? "" : "/") + relativePath.trim(), is_dir: false });
      } else {
        await invoke("create_project_directory", { projectRoot, relativePath: relativePath.trim() });
        await refreshProjectEntries();
      }
      setProjectError(null);
    } catch (error) { setProjectError("Não foi possível criar: " + String(error)); }
  }

  async function runCurrentFile() {
    if (!projectRoot || !openedPath || runningFile) return;
    const extension = openedPath.split(".").pop()?.toLowerCase() || "";
    const supported = ["py","js","mjs","cjs","java","go","rs","php","rb","c","cpp","cc","cxx"];
    if (!supported.includes(extension)) { setProjectError("Este tipo de arquivo ainda não possui executor integrado."); return; }
    const approved = window.confirm("Autorizar a execução deste arquivo?\n\n" + openedPath + "\n\nO código poderá ler e alterar arquivos do projeto e acessar recursos disponíveis ao seu usuário. Execute apenas código confiável.");
    if (!approved) return;
    try {
      if (fileDirty) await saveCurrentFile();
      setRunningFile(true);
      setRunOutput(null);
      const result = await invoke<{ file: string; language: string; exit_code: number; stdout: string; stderr: string; timed_out: boolean }>("run_project_file", { projectRoot, filePath: openedPath, approved: true });
      setRunOutput(result);
      setBottomPanel("Output");
    } catch (error) { setProjectError("Falha ao executar arquivo: " + String(error)); }
    finally { setRunningFile(false); }
  }

  async function saveCurrentFile() {
    if (!projectRoot || !openedPath || !fileDirty || fileSaving) return;
    setFileSaving(true);
    try {
      await invoke("write_project_file", { projectRoot, filePath: openedPath, content: fileContent });
      setFileDirty(false);
      setOpenTabs(tabs => tabs.map(tab => tab.path === openedPath ? { ...tab, content: fileContent, dirty: false } : tab));
      setRuntimeEvents(items => [...items, { id: crypto.randomUUID(), type: "file.saved", message: "Arquivo salvo: " + openedPath }].slice(-100));
      setProjectError(null);
    } catch (error) { setProjectError("Falha ao salvar arquivo: " + String(error)); }
    finally { setFileSaving(false); }
  }

  async function submitInstruction() {
    const instruction = instructionDraft.trim();
    if (!projectRoot || !instruction || runtimeStatus !== "online" || runtimeBusy) return;
    const commandId = crypto.randomUUID();
    pendingInstructionCommandId.current = commandId;
    setChatMessages(items => [...items, { id: commandId, commandId, role: "user" as const, text: instruction, status: "sending" as const }].slice(-100));
    setRuntimeBusy(true);
    setRuntimeError(null);
    try {
      await invoke("send_runtime_command", { message: {
        type: "task.submit", schema_version: 1, command_id: commandId,
        payload: { instruction, project_root: projectRoot }
      }});
      setInstructionDraft("");
    } catch (error) {
      pendingInstructionCommandId.current = null;
      setRuntimeBusy(false);
      const reason = String(error);
      setChatMessages(items => items.map(item => item.commandId === commandId ? { ...item, status: "rejected", text: item.text + "\n\nFalha ao enviar: " + reason } : item));
      setRuntimeError(reason);
    }
  }

  async function loadMemory() {
    if (!projectRoot || runtimeStatus !== "online" || runtimeBusy) {
      setMemoryError("Conecte o Core e aguarde ocioso para consultar a memória.");
      return;
    }
    setMemoryLoading(true);
    setMemoryError(null);
    try {
      await invoke("send_runtime_command", { message: {
        type: "memory.list", schema_version: 1, command_id: crypto.randomUUID(),
        payload: { project_root: projectRoot }
      }});
    } catch (error) {
      setMemoryLoading(false);
      setMemoryError(String(error));
    }
  }

  async function forgetMemory(memoryId: string) {
    if (!projectRoot || runtimeStatus !== "online" || runtimeBusy) return;
    setMemoryLoading(true);
    setMemoryError(null);
    try {
      await invoke("send_runtime_command", { message: {
        type: "memory.forget", schema_version: 1, command_id: crypto.randomUUID(),
        payload: { project_root: projectRoot, memory_id: memoryId }
      }});
    } catch (error) {
      setMemoryLoading(false);
      setMemoryError(String(error));
    }
  }

  async function resumeTask(task: { id: string; title: string }) {
    if (!projectRoot || runtimeStatus !== "online" || runtimeBusy) return;
    try {
      await invoke("send_runtime_command", { message: {
        type: "task.resume", schema_version: 1, command_id: crypto.randomUUID(),
        payload: { task_id: task.id, project_root: projectRoot }
      }});
      setRuntimeBusy(true);
      setRuntimeError(null);
      setRuntimeEvents(items => [...items, { id: crypto.randomUUID(), type: "task.resume.requested", message: `Solicitada retomada: ${task.title}` }].slice(-100));
    } catch (error) {
      setRuntimeError(String(error));
    }
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
    <main className="app-shell" data-theme={settings.uiTheme} data-accent={settings.accentColor} style={{ fontFamily: settings.uiFontFamily, fontSize: settings.uiFontSize + "px" }}>
      <header className="titlebar">
        <div className="brand"><div className="brand-mark"><Zap size={15} /></div><span>KARDEC<span className="brand-light">AGENT</span></span></div>
        <span className="workspace-label">Studio <span className="muted">/</span> {projectRoot ? projectRoot.split(/[\\\\/]/).filter(Boolean).pop() : "Workspace"}</span>
        <div className="titlebar-actions"><button onClick={() => void openProject()} title="Abrir outro projeto"><FolderOpen size={14}/> Abrir</button><button onClick={() => void createNewProject()} title="Criar novo projeto"><FolderPlus size={14}/> Novo projeto</button><button onClick={() => void saveAllFiles()} disabled={!projectRoot || !openTabs.some(tab => tab.dirty)} title="Salvar todos os arquivos"><Save size={14}/> Salvar tudo</button></div><div className="titlebar-right"><span className="connection"><span /> {runtimeStatus === "online" ? "Core conectado" : runtimeStatus === "connecting" ? "Conectando ao core..." : "Core desconectado"}</span><span className="avatar">KA</span></div>
      </header>

      <div className="work-area">
        <nav className="activity-rail" aria-label="Navegação">
          <button className={activeNav === "explorer" ? "rail-button selected" : "rail-button"} onClick={() => setActiveNav("explorer")} title="Explorer"><Files /></button>
          <button className={activeNav === "agent" ? "rail-button selected" : "rail-button"} onClick={() => setActiveNav("agent")} title="Agent"><Bot /></button>
          <button className={activeNav === "tasks" ? "rail-button selected" : "rail-button"} onClick={() => setActiveNav("tasks")} title="Tarefas"><Workflow /></button>
          <button className={activeNav === "approvals" ? "rail-button selected" : "rail-button"} onClick={() => setActiveNav("approvals")} title="Aprovações"><ShieldCheck /></button>
          <div className="rail-spacer" />
          <button className="rail-button" title="Atividade"><Activity /></button>
          <button className="rail-button" title="Configurações" onClick={() => setSettingsOpen(true)}><Settings /></button>
        </nav>

        <aside className="sidebar">
          <div className="sidebar-heading"><span>{activeNav === "explorer" ? "EXPLORER" : activeNav.toUpperCase()}</span>{activeNav === "explorer" && projectRoot ? <div className="sidebar-actions"><button className="icon-button" title="Novo arquivo" onClick={() => void createProjectItem("file")}><FilePlus2 size={14}/></button><button className="icon-button" title="Nova pasta" onClick={() => void createProjectItem("directory")}><FolderPlus size={14}/></button></div> : <button className="icon-button" title="Nova ação"><Plus size={15}/></button>}</div>
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
                {projectEntries.map(entry => <ExplorerEntry key={entry.path} entry={entry} projectRoot={projectRoot!} onOpenFile={openEntry}/>) }
                {projectError && <div className="tree-row" role="alert">{projectError}</div>}
              </div>
              <div className="sidebar-bottom"><Search size={14}/> Buscar nos arquivos <span className="shortcut">⌘P</span></div>
            </>
          ) : activeNav === "tasks" ? (
            <div className="task-sidebar">{runtimeTasks.length ? runtimeTasks.slice().reverse().map(task => <div className="task-mini" key={task.executionId}><CircleDot className={task.status === "Em execução" || task.status === "Validando" || task.status === "Retomando" ? "active" : task.status === "Concluída" || task.status === "Verificada" ? "done" : task.status === "Falhou" || task.status === "Recuperável" ? "failed" : "queued"}/><div className="task-mini-content"><strong>{task.title}</strong><small>{task.id.slice(0, 8)} · {task.status}</small>{task.recoverable && <button className="task-resume-button" disabled={runtimeBusy || runtimeStatus !== "online"} onClick={() => resumeTask(task)}><GitBranch size={12}/> Retomar tarefa</button>}</div></div>) : <div className="empty-state"><ListTodo/><strong>Nenhuma tarefa recebida</strong><small>Envie uma instrução ao gestor na área Agent.</small></div>}</div>
          ) : activeNav === "approvals" ? (pendingApproval ? <div className="approval-sidebar-card"><ShieldCheck/><strong>Aprovação pendente</strong><small>{pendingApproval.kind === "high_risk" ? "Confirmação adicional de alto risco" : "Plano aguardando sua decisão"}</small><span>Risco: {pendingApproval.risk}</span></div> : <div className="empty-state"><ShieldCheck/><strong>Nenhuma aprovação pendente</strong><small>Solicitações do gestor aparecerão aqui.</small></div>) :
          <div className="empty-state"><Bot/><strong>Agent Workspace</strong><small>Conecte o core para iniciar tarefas.</small></div>}
        </aside>

        <section className="center-pane">
          {activeNav === "agent" ? (
            <div className="manager-inbox">
              <div className="process-toolbar"><div><span className="eyebrow">CENTRAL DE COMANDO</span><h2>Gestor de tarefas</h2><p>Converse, tire dúvidas sobre o projeto ou solicite mudanças. O agente identifica a intenção e só cria um plano quando você pede uma ação.</p></div><span className="runtime-offline">{runtimeStatus === "online" ? "CORE ONLINE" : runtimeStatus === "connecting" ? "CONECTANDO" : "CORE OFFLINE"}</span></div>
              <section className="manager-inbox-card">
                <div className="manager-inbox-icon"><Bot size={22}/></div>
                <div><strong>{runtimeTasks.length ? `${runtimeTasks.length} tarefa(s) recebida(s) nesta sessão` : "Aguardando a primeira instrução"}</strong><p>As solicitações aparecem com seu estado e os eventos reais enviados pelo gestor Python. O histórico desta sessão é mantido limitado em memória.</p></div>
              </section>
              <div className="manager-flow">
                <div><span>01</span><strong>Receber</strong><small>O agente interpreta se é conversa, dúvida ou pedido de ação.</small></div>
                <div><span>02</span><strong>Planejar</strong><small>Somente pedidos de ação geram um plano para sua aprovação.</small></div>
                <div><span>03</span><strong>Distribuir</strong><small>Após a aprovação, o agente executa as etapas; dúvidas seguem direto pelo chat.</small></div>
                <div><span>04</span><strong>Entregar</strong><small>Respostas aparecem no chat; tarefas mostram progresso, resultados e bloqueios.</small></div>
              </div>
              {runtimeError && <div className="manager-runtime-error" role="alert">{runtimeError}</div>}
              <section className="manager-event-list">
                <div className="process-section-title"><strong>Atividade do gestor</strong><span>{filteredEvents.length}/{runtimeEvents.length} eventos</span></div><input className="event-search" aria-label="Filtrar eventos" placeholder="Filtrar por tipo ou mensagem..." value={eventSearch} onChange={event => setEventSearch(event.target.value)} />
                {runtimeEvents.length === 0 ? <p className="manager-no-events">As instruções recebidas e as atualizações do core aparecerão aqui.</p> : filteredEvents.slice().reverse().map(item => <div className="manager-event" key={item.id}><span>{item.type}</span><p>{item.message}</p></div>)}
              </section>
              <div className="manager-inbox-note"><ShieldCheck size={15}/> Conversas são somente leitura; alterações no projeto exigem aprovação do plano.</div>
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
                <div className="process-section-title"><strong>Orçamento de execução do core</strong><span>Limites lógicos recebidos no runtime.ready · não são quotas do sistema operacional</span></div>
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
                <div className="process-section-title"><strong>Eventos recentes</strong><span>{filteredEvents.length} correspondentes</span></div><input className="event-search" aria-label="Filtrar eventos" placeholder="Filtrar por tipo ou mensagem..." value={eventSearch} onChange={event => setEventSearch(event.target.value)} />
                {runtimeEvents.length ? filteredEvents.slice().reverse().slice(0, 12).map(event => <div className="process-event" key={event.id}><span className="event-marker info"/><time>Studio</time><strong>{event.type}</strong><span>{event.message}</span></div>) : <p className="manager-no-events">Nenhum evento recebido. Inicie uma tarefa pela área Agent.</p>}
              </section>
            </div>
          ) : (
            <>
              <div className="tabs">{openTabs.map(tab => <button key={tab.path} className={openedPath === tab.path ? "tab active" : "tab"} onClick={() => { setOpenedPath(tab.path); setSelectedFile(tab.name); setFileContent(tab.content); setFileDirty(tab.dirty); }}><FileCode2 size={14}/>{tab.name}{tab.dirty && <span className="dirty-dot"/>}<span className="tab-close" onClick={event => { event.stopPropagation(); const remaining = openTabs.filter(item => item.path !== tab.path); setOpenTabs(remaining); if (openedPath === tab.path) { const next = remaining[remaining.length - 1]; setOpenedPath(next?.path || null); setSelectedFile(next?.name || "App.tsx"); setFileContent(next?.content || ""); setFileDirty(next?.dirty || false); } }}>×</span></button>)}<div className="tab-placeholder">{openedPath || "KardecAgent Studio"}</div>{openedPath && <><button className="save-file-button" onClick={runCurrentFile} disabled={runningFile}><Play size={13}/>{runningFile ? "Executando..." : "Executar"}</button><button className="save-file-button" onClick={saveCurrentFile} disabled={!fileDirty || fileSaving}><Save size={13}/>{fileSaving ? "Salvando..." : "Salvar"}</button></>}</div>
              <div className="editor">
                {openedPath ? <div className="file-view"><div className="file-path"><span>{openedPath}</span><span>{openedPath.split(".").pop()?.toUpperCase()} · UTF-8</span></div><div className="code-editor-wrap">{settings.showLineNumbers && <div className="line-numbers">{fileContent.split("\n").map((_, index) => <span key={index}>{index + 1}</span>)}</div>}<div className="code-surface"><pre className="syntax-highlight" aria-hidden="true" style={{ fontFamily: settings.editorFontFamily, fontSize: `${settings.editorFontSize}px`, lineHeight: settings.lineHeight, tabSize: settings.tabSize, whiteSpace: settings.wordWrap ? "pre-wrap" : "pre" }}>{highlightSource(fileContent, openedPath.split(".").pop() || "")}{"\n"}</pre><textarea className="code-editor" spellCheck={false} value={fileContent} style={{ fontFamily: settings.editorFontFamily, fontSize: `${settings.editorFontSize}px`, lineHeight: settings.lineHeight, tabSize: settings.tabSize, whiteSpace: settings.wordWrap ? "pre-wrap" : "pre" }} onChange={event => { setFileContent(event.target.value); setFileDirty(true); setOpenTabs(tabs => tabs.map(tab => tab.path === openedPath ? { ...tab, content: event.target.value, dirty: true } : tab)); }} onScroll={event => { const overlay = event.currentTarget.previousElementSibling as HTMLElement | null; if (overlay) { overlay.scrollTop = event.currentTarget.scrollTop; overlay.scrollLeft = event.currentTarget.scrollLeft; } }} onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); void saveCurrentFile(); } if (event.key === "Tab") { event.preventDefault(); const target = event.currentTarget; const start = target.selectionStart; const end = target.selectionEnd; const value = target.value.slice(0, start) + "  " + target.value.slice(end); setFileContent(value); setFileDirty(true); setOpenTabs(tabs => tabs.map(tab => tab.path === openedPath ? { ...tab, content: value, dirty: true } : tab)); requestAnimationFrame(() => { target.selectionStart = target.selectionEnd = start + 2; }); } }} aria-label="Editor de código" /></div></div></div> : <div className="welcome"><div className="welcome-icon"><Zap size={24}/></div><h1>KardecAgent <span>Studio</span></h1><p>Seu ambiente de desenvolvimento orientado por agentes.</p><div className="welcome-actions"><button onClick={openProject}><Folder size={15}/> Abrir projeto</button><button className="secondary" onClick={() => setActiveNav("tasks")}><Plus size={15}/> Ver processos</button></div><div className="quick-grid"><div><span className="quick-label">WORKSPACE</span><strong>{projectRoot ? projectRoot.replaceAll("\\\\", "/").split("/").filter(Boolean).pop() : "Projeto local"}</strong><small>{projectRoot || "Selecione uma pasta para começar"}</small></div><div><span className="quick-label">RUNTIME</span><strong className="status-muted">{runtimeStatus === "online" ? "Conectado" : runtimeStatus === "connecting" ? "Conectando" : "Desconectado"}</strong><small>{runtimeError || (runtimeStatus === "online" ? "Gestor pronto para receber instruções" : "Abra um projeto para iniciar o core Python")}</small></div></div></div>}
              </div>
              <div className="bottom-panel"><div className="panel-tabs">{["Atividades","Output","Terminal","Alterações"].map(tab => <button key={tab} className={bottomPanel === tab ? "panel-tab active" : "panel-tab"} onClick={() => setBottomPanel(tab)}>{tab}</button>)}<button className="panel-collapse"><ChevronDown size={14}/></button></div><div className="panel-content">{bottomPanel === "Terminal" ? <div className="integrated-terminal" style={{ fontSize: settings.terminalFontSize + "px" }}><div className="terminal-toolbar"><span><Terminal size={13}/> {terminalReady ? "Terminal ativo" : "Terminal parado"}</span><div><button onClick={() => void startTerminal()} disabled={terminalReady || !projectRoot}>Novo terminal</button><button onClick={() => void stopTerminal()} disabled={!terminalReady}><Square size={11}/> Encerrar</button></div></div><div className="terminal-screen">{terminalOutput.map((line,index) => <div key={index}>{line}</div>)}</div><form className="terminal-command" onSubmit={event => { event.preventDefault(); void sendTerminalCommand(); }}><span>{settings.shell === "cmd" ? ">" : "PS>"}</span><input value={terminalInput} onChange={event => setTerminalInput(event.target.value)} placeholder={terminalReady ? "Digite um comando e pressione Enter..." : "Inicie um terminal para executar comandos"} disabled={!terminalReady}/><button type="submit" disabled={!terminalReady || !terminalInput.trim()}><Send size={13}/></button></form></div> : bottomPanel === "Output" && runOutput ? <div className="run-output"><div className="run-output-header"><strong>{runOutput.language} · {runOutput.file}</strong><span className={runOutput.exit_code === 0 && !runOutput.timed_out ? "run-success" : "run-failure"}>{runOutput.timed_out ? "Tempo esgotado" : "Código de saída: " + runOutput.exit_code}</span></div><pre>{[runOutput.stdout, runOutput.stderr].filter(Boolean).join("\n") || "(sem saída)"}</pre></div> : <><Clock3 size={14}/><span>{bottomPanel === "Output" ? "A saída dos programas executados aparecerá aqui." : bottomPanel === "Alterações" ? "As alterações de arquivos serão exibidas aqui." : "Eventos do runtime aparecerão aqui, sem polling contínuo."}</span></>}</div></div>
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
            {chatMessages.length > 0 && <div className="agent-chat-history" aria-live="polite">
              {chatMessages.map(message => <article className={`agent-chat-message role-${message.role} ${message.status || ""}`} key={message.id}>
                <span>{message.role === "user" ? "Você" : "KardecAgent"}{message.status === "sending" ? " · enviando" : message.status === "sent" ? " · recebida" : message.status === "rejected" ? " · não enviada" : ""}</span>
                <p>{message.text}</p>
              </article>)}
            </div>}
            {pendingApproval ? <div className="approval-prompt">
              <div className="approval-prompt-head"><ShieldCheck size={18}/><strong>{pendingApproval.kind === "high_risk" ? "Aprovação adicional de alto risco" : "Aprovar plano de execução"}</strong></div>
              <span className="approval-risk">RISCO: {pendingApproval.risk.toUpperCase()}</span>
              <pre>{pendingApproval.summary}</pre>
              <div className="approval-actions"><button onClick={() => respondToApproval("reject")} className="approval-reject" disabled={approvalSubmitting}>Rejeitar</button><button onClick={() => respondToApproval("approve")} className="approval-accept" disabled={approvalSubmitting}>{approvalSubmitting ? "Enviando..." : "Aprovar execução"}</button></div>
            </div> : chatMessages.length === 0 ? <div className="agent-empty"><div className="agent-orb"><Bot size={24}/></div><strong>Pronto para trabalhar</strong><p>Abra um projeto e descreva o que deseja construir. O KardecAgent planeja, executa e valida com você no controle.</p></div> : null}
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


      {settingsOpen && <div className="settings-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setSettingsOpen(false); }}>
        <section className="settings-dialog settings-dialog-wide" role="dialog" aria-modal="true" aria-labelledby="settings-title">
          <header className="settings-header"><div><span className="eyebrow">CENTRAL DE PREFERÊNCIAS</span><h2 id="settings-title">Configurações do KardecAgent Studio</h2></div><button className="icon-button" onClick={() => setSettingsOpen(false)} aria-label="Fechar configurações"><X size={18}/></button></header>
          <div className="settings-layout">
            <nav className="settings-nav" aria-label="Categorias de configuração">
              <button className={settingsSection === "core" ? "active" : ""} onClick={() => setSettingsSection("core")}><Bot size={15}/> Core e IA</button>
              <button className={settingsSection === "editor" ? "active" : ""} onClick={() => setSettingsSection("editor")}><FileCode2 size={15}/> Editor</button>
              <button className={settingsSection === "appearance" ? "active" : ""} onClick={() => setSettingsSection("appearance")}><Activity size={15}/> Aparência</button>
              <button className={settingsSection === "terminal" ? "active" : ""} onClick={() => setSettingsSection("terminal")}><Terminal size={15}/> Terminal</button>
              <button className={settingsSection === "memory" ? "active" : ""} onClick={() => { setSettingsSection("memory"); void loadMemory(); }}><Bot size={15}/> Memória do agente</button>
              <button className={settingsSection === "diagnostics" ? "active" : ""} onClick={() => { setSettingsSection("diagnostics"); void refreshCoreDiagnostics(); }}><Activity size={15}/> Diagnóstico</button>
            </nav>
            <div className="settings-page">
              {settingsSection === "core" && <>
                <div className="settings-page-heading"><strong>Core e inteligência artificial</strong><p>Controle a conexão local, o modelo e o ambiente Python usado pelo agente.</p></div>
                <div className="settings-section">
                  <div className="settings-section-title"><Wifi size={16}/><strong>Conexão com Ollama</strong></div>
                  <label>Endereço do servidor<input value={settings.ollamaUrl} onChange={event => setSettings(current => ({ ...current, ollamaUrl: event.target.value }))} placeholder="http://127.0.0.1:11434"/></label>
                  <label>Modelo local<input value={settings.model} onChange={event => setSettings(current => ({ ...current, model: event.target.value }))} placeholder="qwen2.5-coder:3b"/></label>
                  <p className="settings-help">Perfil econômico ativo: contexto de 4.096 tokens, geração limitada e descarregamento do modelo após dois minutos sem uso. O Core é iniciado automaticamente pelo Studio; para gerar respostas, mantenha o Ollama ativo e instale o modelo selecionado.</p>
                  <div className="settings-actions"><button className="settings-secondary" onClick={() => setSettings(current => ({ ...current, model: "qwen2.5-coder:1.5b" }))}>Selecionar modelo ultra-leve (1.5B)</button></div>
                  <label>Tempo limite de resposta (segundos)<input type="number" min={10} max={900} value={settings.timeout} onChange={event => setSettings(current => ({ ...current, timeout: Math.max(10, Math.min(900, Number(event.target.value) || 180)) }))}/></label>
                  <div className="settings-actions"><button className="settings-secondary" disabled={ollamaTesting} onClick={async () => {
                    setOllamaTesting(true); setOllamaTest(null);
                    try { const result = await invoke<{ version: string; models: string[]; model: string; response: string }>("test_ollama_connection", { ollamaBaseUrl: settings.ollamaUrl, llmModel: settings.model, llmTimeoutSeconds: settings.timeout, pythonExecutable: settings.pythonPath.trim() || null }); setOllamaTest({ ok: true, version: result.version, models: result.models, message: result.response }); }
                    catch (error) { setOllamaTest({ ok: false, message: String(error) }); }
                    finally { setOllamaTesting(false); }
                  }}><Wifi size={14}/>{ollamaTesting ? "Testando modelo..." : "Testar conexão e modelo"}</button></div>
                  {ollamaTest && <div className={ollamaTest.ok ? "ollama-test-result success" : "ollama-test-result error"}>{ollamaTest.ok ? <CheckCircle size={15}/> : <AlertCircle size={15}/>}<div><strong>{ollamaTest.ok ? "Ollama respondeu corretamente" : "Falha na conexão"}</strong><p>{ollamaTest.message}</p>{ollamaTest.version && <small>Ollama {ollamaTest.version} · Modelo {settings.model}</small>}{ollamaTest.models && <small>Modelos instalados: {ollamaTest.models.join(", ") || "nenhum"}</small>}</div></div>}
                </div>
                <div className="settings-section">
                  <div className="settings-section-title"><Bot size={16}/><strong>Ambiente do Core</strong></div>
                  <p className="settings-help">O instalador do Studio inclui o Core Python integrado. Você não precisa instalar Python nem executar comandos manualmente. O campo abaixo é apenas para desenvolvedores que desejam substituir o Core integrado.</p>
                  <label>Python manual (opcional)<input value={settings.pythonPath} onChange={event => setSettings(current => ({ ...current, pythonPath: event.target.value }))} placeholder="Automático — ou C:\Python312\python.exe"/></label>
                  <p className="settings-help">Deixe vazio para usar o Core integrado. Informe um python.exe apenas para desenvolvimento ou diagnóstico avançado.</p>
                </div>
                <div className="settings-section">
                  <div className="settings-section-title"><RefreshCw size={16}/><strong>Controle do Core</strong></div>
                  <label className="settings-checkbox"><input type="checkbox" checked={settings.autoStartCore} onChange={event => setSettings(current => ({ ...current, autoStartCore: event.target.checked }))}/> Iniciar o Core automaticamente ao abrir um projeto</label>
                  <p className="settings-help">O Core roda em um processo Python separado. Você pode consultar o processo ou reiniciá-lo sem fechar a IDE.</p>
                  <div className="settings-actions"><button className="settings-secondary" onClick={() => void refreshCoreDiagnostics()}>Verificar processo</button><button className="settings-primary" disabled={!projectRoot || runtimeBusy} onClick={() => void restartCore()}>Reiniciar Core</button></div>
                </div>
              </>}
              {settingsSection === "editor" && <>
                <div className="settings-page-heading"><strong>Editor de código</strong><p>Personalize a leitura e a edição dos arquivos.</p></div>
                <div className="settings-section">
                  <label>Família da fonte<select value={settings.editorFontFamily} onChange={event => setSettings(current => ({ ...current, editorFontFamily: event.target.value }))}><option>Shantell Sans</option><option>Cascadia Code</option><option>JetBrains Mono</option><option>Fira Code</option><option>Consolas</option><option>Courier New</option><option>ui-monospace</option>{importedGoogleFontNames().map(family => <option key={family}>{family}</option>)}</select></label>
                  <label>Tamanho da fonte: {settings.editorFontSize}px<input type="range" min={10} max={24} value={settings.editorFontSize} onChange={event => setSettings(current => ({ ...current, editorFontSize: Number(event.target.value) }))}/></label>
                  <label>Altura da linha: {settings.lineHeight.toFixed(1)}<input type="range" min={1.2} max={2.2} step={0.1} value={settings.lineHeight} onChange={event => setSettings(current => ({ ...current, lineHeight: Number(event.target.value) }))}/></label>
                  <label>Espaços por tabulação<select value={settings.tabSize} onChange={event => setSettings(current => ({ ...current, tabSize: Number(event.target.value) }))}><option value={2}>2 espaços</option><option value={4}>4 espaços</option><option value={8}>8 espaços</option></select></label>
                  <label className="settings-checkbox"><input type="checkbox" checked={settings.wordWrap} onChange={event => setSettings(current => ({ ...current, wordWrap: event.target.checked }))}/> Quebra automática de linha</label>
                  <label className="settings-checkbox"><input type="checkbox" checked={settings.showLineNumbers} onChange={event => setSettings(current => ({ ...current, showLineNumbers: event.target.checked }))}/> Exibir números das linhas</label>
                  <div className="settings-preview" style={{fontFamily:settings.editorFontFamily,fontSize:settings.editorFontSize+"px",lineHeight:settings.lineHeight}}>const agent = "KardecAgent";<br/>await agent.run("Olá, mundo");</div>
                </div>
              </>}
              {settingsSection === "appearance" && <>
                <div className="settings-page-heading"><strong>Aparência e personalização</strong><p>Defina o tema, a cor de destaque e a tipografia da interface.</p></div>
                <div className="settings-section">
                  <label>Tema da IDE<select value={settings.uiTheme} onChange={event => setSettings(current => ({ ...current, uiTheme: event.target.value as StudioSettings["uiTheme"] }))}><option value="dark">Kardec Dark</option><option value="midnight">Midnight Blue</option><option value="light">Claro</option></select></label>
                  <label>Cor de destaque<select value={settings.accentColor} onChange={event => setSettings(current => ({ ...current, accentColor: event.target.value as StudioSettings["accentColor"] }))}><option value="violet">Violeta</option><option value="cyan">Ciano</option><option value="blue">Azul</option><option value="green">Verde</option></select></label>
                  <label>Fonte da interface<select value={settings.uiFontFamily} onChange={event => setSettings(current => ({ ...current, uiFontFamily: event.target.value }))}><option>Shantell Sans</option><option>Inter</option><option>Segoe UI</option><option>Arial</option><option>Roboto</option><option>system-ui</option>{importedGoogleFontNames().map(family => <option key={family}>{family}</option>)}</select></label>
                  <label>Escala da interface: {settings.uiFontSize}px<input type="range" min={11} max={16} value={settings.uiFontSize} onChange={event => setSettings(current => ({ ...current, uiFontSize: Number(event.target.value) }))}/></label>
                  <div className="settings-preview"><strong>KardecAgent Studio</strong><p>Prévia da tipografia e do tema selecionado.</p><span className="settings-accent-preview">Destaque da interface</span></div>
                  <div className="settings-section-title"><Wifi size={16}/><strong>Biblioteca Google Fonts</strong></div>
                  <label>Link da fonte no Google Fonts<input value={googleFontUrl} onChange={event => setGoogleFontUrl(event.target.value)} placeholder="https://fonts.google.com/specimen/Shantell+Sans"/></label>
                  <p className="settings-help">A IDE baixa os arquivos WOFF2 do Google Fonts e os guarda localmente neste computador para uso sem depender de uma conexão em cada inicialização.</p>
                  <div className="settings-actions"><button className="settings-secondary" disabled={googleFontBusy} onClick={() => void importGoogleFont()}><Plus size={14}/>{googleFontBusy ? "Baixando fonte..." : "Baixar e importar fonte"}</button></div>
                  {googleFontStatus && <div className="settings-help" role="status">{googleFontStatus}</div>}
                </div>
              </>}
              {settingsSection === "terminal" && <>
                <div className="settings-page-heading"><strong>Terminal integrado</strong><p>Escolha o shell e ajuste a apresentação do terminal.</p></div>
                <div className="settings-section">
                  <label>Shell padrão<select value={settings.shell} onChange={event => setSettings(current => ({ ...current, shell: event.target.value as "powershell" | "cmd" }))}><option value="powershell">PowerShell</option><option value="cmd">Prompt de Comando (CMD)</option></select></label>
                  <label>Tamanho da fonte: {settings.terminalFontSize}px<input type="range" min={10} max={18} value={settings.terminalFontSize} onChange={event => setSettings(current => ({ ...current, terminalFontSize: Number(event.target.value) }))}/></label>
                  <div className="settings-help">O terminal inicia na pasta do projeto aberto. Os comandos são executados no shell selecionado.</div>
                </div>
              </>}
              {settingsSection === "memory" && <>
                <div className="settings-page-heading"><strong>Memória persistente do agente</strong><p>O KardecAgent guarda preferências explicitamente informadas, decisões e fatos úteis do projeto. Você pode revisar e apagar qualquer registro.</p></div>
                <div className="settings-section">
                  <div className="diagnostic-row"><span>Registros disponíveis</span><strong>{memoryItems.length}</strong></div>
                  <p className="settings-help">Memória do projeto fica em .kardecagent/memory.json. Preferências gerais ficam na pasta .kardecagent do seu perfil do Windows. O histórico recente da conversa é local ao projeto. Credenciais e segredos não devem ser memorizados.</p>
                  <div className="settings-actions"><button className="settings-secondary" disabled={memoryLoading || runtimeBusy || runtimeStatus !== "online"} onClick={() => void loadMemory()}>{memoryLoading ? "Carregando..." : "Atualizar memória"}</button></div>
                  {memoryError && <div className="ollama-test-result error"><AlertCircle size={15}/><p>{memoryError}</p></div>}
                  {memoryItems.length === 0 && !memoryLoading && <div className="settings-help">Nenhuma memória registrada ainda. Ao longo das conversas, o agente poderá guardar preferências e decisões estáveis que você declarar explicitamente.</div>}
                  {memoryItems.map(item => <article className="memory-record" key={item.id}>
                    <div className="diagnostic-row"><span>{item.scope === "project" ? "Este projeto" : "Preferência geral"} · {item.category}</span><button className="settings-secondary" disabled={memoryLoading || runtimeBusy} onClick={() => void forgetMemory(item.id)}>Esquecer</button></div>
                    <p>{item.content}</p>
                  </article>)}
                </div>
              </>}
              {settingsSection === "diagnostics" && <>
                <div className="settings-page-heading"><strong>Diagnóstico do sistema</strong><p>Informações do processo Python e da comunicação com o Core.</p></div>
                <div className="settings-section">
                  <div className="diagnostic-row"><span>Estado</span><strong>{coreDiagnostics?.running ? "Em execução" : "Desconectado"}</strong></div>
                  <div className="diagnostic-row"><span>PID</span><strong>{coreDiagnostics?.pid ?? "—"}</strong></div>
                  <div className="diagnostic-row"><span>Modo de inicialização</span><strong>{coreDiagnostics?.launch_mode === "bundled" ? "Core integrado ao Studio" : coreDiagnostics?.launch_mode === "python" ? "Python externo" : "Não iniciado"}</strong></div>
                  <div className="diagnostic-row"><span>Executável</span><strong>{coreDiagnostics?.python_executable || settings.pythonPath || "Core ainda não iniciado"}</strong></div>
                  <div className="diagnostic-row"><span>Workspace</span><strong>{coreDiagnostics?.project_root || projectRoot || "Nenhum"}</strong></div>
                  <div className="diagnostic-row"><span>Último código de saída</span><strong>{coreDiagnostics?.last_exit_code ?? "Sem encerramento registrado"}</strong></div>
                  {coreDiagnosticsError && <div className="ollama-test-result error"><AlertCircle size={15}/><p>{coreDiagnosticsError}</p></div>}
                  {runtimeError && <div className="ollama-test-result error"><AlertCircle size={15}/><p>{runtimeError}</p></div>}
                  {runtimeEvents.some(item => item.type === "runtime.log") && <div className="diagnostic-logs"><strong>Últimos registros do Core</strong>{runtimeEvents.filter(item => item.type === "runtime.log").slice(-8).map(item => <pre key={item.id}>{item.message}</pre>)}</div>}
                  <div className="settings-actions"><button className="settings-secondary" onClick={() => void refreshCoreDiagnostics()}>Atualizar diagnóstico</button><button className="settings-primary" disabled={!projectRoot || runtimeBusy} onClick={() => void restartCore()}>Reiniciar Core</button></div>
                </div>
              </>}
            </div>
          </div>
          <footer className="settings-footer"><span>As preferências são salvas localmente neste computador.</span><button className="settings-primary" onClick={() => {
            const previous = { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem("kardecagent.studio.settings") || "{}") };
            const connectionChanged = previous.ollamaUrl !== settings.ollamaUrl || previous.model !== settings.model || previous.timeout !== settings.timeout || previous.pythonPath !== settings.pythonPath;
            const startupChanged = previous.autoStartCore !== settings.autoStartCore;
            localStorage.setItem("kardecagent.studio.settings", JSON.stringify(settings));
            setOllamaTest(null);
            setSettingsOpen(false);
            if (projectRoot && !runtimeBusy && (connectionChanged || startupChanged)) {
              if (settings.autoStartCore) void restartCore();
              else void (async () => { try { await invoke("stop_runtime"); setRuntimeStatus("offline"); await refreshCoreDiagnostics(); } catch (error) { setRuntimeError(String(error)); } })();
            }
          }}><CheckCircle2 size={14}/> Salvar preferências</button></footer>
        </section>
      </div>}
      <footer className="statusbar"><div><GitBranch size={12}/> feat/kardecagent-studio <span>·</span><CheckCircle2 size={12}/> {projectRoot ? "Projeto aberto" : "Workspace local"}</div><div><span className="status-indicator"/> Studio <span>·</span> {runtimeStatus === "online" ? "Runtime conectado" : runtimeStatus === "connecting" ? "Conectando runtime" : "Runtime offline"}</div></footer>
    </main>
  );
}

export default App;
