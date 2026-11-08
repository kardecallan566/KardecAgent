use serde::Serialize;
use serde_json::Value;
use std::{
    fs,
    io::{BufRead, BufReader, ErrorKind, Write},
    path::Path,
    process::{Child, ChildStdin, Command, Stdio},
    sync::Mutex,
    thread,
};
use tauri::{Emitter, State};

#[derive(Serialize)]
struct ProjectEntry {
    name: String,
    path: String,
    is_dir: bool,
}

const MAX_RUNTIME_LINE_BYTES: usize = 256 * 1024 + 1;
const MAX_RUNTIME_LOG_BYTES: usize = 8 * 1024;

fn read_bounded_line<R: BufRead>(reader: &mut R, limit: usize) -> std::io::Result<Option<Vec<u8>>> {
    let mut line = Vec::with_capacity(4096);
    let mut overflow = false;
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            if line.is_empty() && !overflow {
                return Ok(None);
            }
            break;
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let consumed = newline.map_or(available.len(), |index| index + 1);
        if !overflow {
            let remaining = limit.saturating_sub(line.len());
            if consumed > remaining {
                line.extend_from_slice(&available[..remaining]);
                overflow = true;
            } else {
                line.extend_from_slice(&available[..consumed]);
            }
        }
        reader.consume(consumed);
        if newline.is_some() {
            break;
        }
    }
    if overflow {
        return Err(std::io::Error::new(ErrorKind::InvalidData, "runtime line exceeded its byte limit"));
    }
    if line.last() == Some(&b'\n') {
        line.pop();
    }
    if line.last() == Some(&b'\r') {
        line.pop();
    }
    Ok(Some(line))
}

struct RuntimeProcess {
    child: Child,
    stdin: ChildStdin,
}

impl Drop for RuntimeProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Default)]
struct RuntimeState(Mutex<Option<RuntimeProcess>>);

fn resolve_workspace_path(project_root: &str, requested_path: &str) -> Result<std::path::PathBuf, String> {
    let root = Path::new(project_root)
        .canonicalize()
        .map_err(|e| format!("Não foi possível acessar a raiz do projeto: {e}"))?;
    let path = Path::new(requested_path)
        .canonicalize()
        .map_err(|e| format!("Não foi possível acessar o caminho: {e}"))?;
    if !path.starts_with(&root) {
        return Err("A operação está fora da pasta do projeto.".into());
    }
    Ok(path)
}

#[tauri::command]
fn read_project_entries(project_root: String, project_path: String) -> Result<Vec<ProjectEntry>, String> {
    let root = resolve_workspace_path(&project_root, &project_path)?;
    if !root.is_dir() {
        return Err("O caminho selecionado não é uma pasta.".into());
    }

    let mut entries = fs::read_dir(&root)
        .map_err(|e| format!("Falha ao listar projeto: {e}"))?
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            let metadata = entry.metadata().ok()?;
            Some(ProjectEntry {
                name,
                path: path.to_string_lossy().into_owned(),
                is_dir: metadata.is_dir(),
            })
        })
        .collect::<Vec<_>>();

    entries.sort_by(|a, b| {
        b.is_dir.cmp(&a.is_dir).then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });
    entries.truncate(200);
    Ok(entries)
}

#[tauri::command]
fn read_project_file(project_root: String, file_path: String) -> Result<String, String> {
    let path = resolve_workspace_path(&project_root, &file_path)
        .map_err(|e| format!("Não foi possível abrir o arquivo: {e}"))?;
    if !path.is_file() { return Err("O caminho selecionado não é um arquivo.".into()); }
    let metadata = fs::metadata(&path).map_err(|e| e.to_string())?;
    if metadata.len() > 1_000_000 {
        return Err("Arquivo maior que 1 MB: visualização bloqueada para preservar recursos.".into());
    }
    fs::read_to_string(path).map_err(|e| format!("Arquivo não é texto UTF-8 ou falha de leitura: {e}"))
}

#[tauri::command]
fn start_runtime(
    app: tauri::AppHandle,
    state: State<'_, RuntimeState>,
    project_path: String,
) -> Result<(), String> {
    let root = Path::new(&project_path)
        .canonicalize()
        .map_err(|e| format!("Projeto inválido: {e}"))?;
    if !root.is_dir() {
        return Err("O caminho do projeto não é uma pasta.".into());
    }

    let mut runtime = state.0.lock().map_err(|_| "Runtime lock poisoned".to_string())?;
    if runtime.is_some() {
        return Err("O runtime já está iniciado.".into());
    }

    let python = std::env::var_os("KARDECAGENT_PYTHON")
        .unwrap_or_else(|| if cfg!(windows) { "python".into() } else { "python3".into() });
    let mut child = Command::new(python)
        .args(["-m", "kardecagent.cli", "studio", "--project"])
        .arg(&root)
        .current_dir(&root)
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Não foi possível iniciar o core Python. Configure KARDECAGENT_PYTHON se necessário: {e}"))?;

    let stdin = child.stdin.take().ok_or("Falha ao abrir stdin do runtime.")?;
    let stdout = child.stdout.take().ok_or("Falha ao abrir stdout do runtime.")?;
    let stderr = child.stderr.take().ok_or("Falha ao abrir stderr do runtime.")?;

    let stdout_app = app.clone();
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            match read_bounded_line(&mut reader, MAX_RUNTIME_LINE_BYTES) {
                Ok(Some(bytes)) => {
                    let line = String::from_utf8_lossy(&bytes);
                    if let Ok(message) = serde_json::from_str::<Value>(&line) {
                        let _ = stdout_app.emit("runtime-message", message);
                    } else {
                        let _ = stdout_app.emit("runtime-protocol-error", line.to_string());
                    }
                }
                Ok(None) => break,
                Err(error) => {
                    let _ = stdout_app.emit("runtime-protocol-error", error.to_string());
                    if error.kind() != ErrorKind::InvalidData {
                        break;
                    }
                }
            }
        }
        let _ = stdout_app.emit("runtime-stream-closed", ());
    });

    let stderr_app = app.clone();
    thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        loop {
            match read_bounded_line(&mut reader, MAX_RUNTIME_LOG_BYTES) {
                Ok(Some(bytes)) => {
                    let line = String::from_utf8_lossy(&bytes).into_owned();
                    let _ = stderr_app.emit("runtime-stderr", line);
                }
                Ok(None) => break,
                Err(error) => {
                    let _ = stderr_app.emit("runtime-stderr", error.to_string());
                    if error.kind() != ErrorKind::InvalidData {
                        break;
                    }
                }
            }
        }
    });

    *runtime = Some(RuntimeProcess { child, stdin });
    Ok(())
}

#[tauri::command]
fn send_runtime_command(state: State<'_, RuntimeState>, message: Value) -> Result<(), String> {
    let encoded = serde_json::to_string(&message)
        .map_err(|e| format!("Comando inválido: {e}"))?;
    if encoded.len() > 64 * 1024 {
        return Err("Comando excede o limite de 64 KiB.".into());
    }
    let mut runtime = state.0.lock().map_err(|_| "Runtime lock poisoned".to_string())?;
    let process = runtime.as_mut().ok_or("Runtime não iniciado.")?;
    process.stdin.write_all(encoded.as_bytes()).map_err(|e| format!("Falha ao enviar comando ao runtime: {e}"))?;
    process.stdin.write_all(b"\n").map_err(|e| format!("Falha ao finalizar comando: {e}"))?;
    process.stdin.flush().map_err(|e| format!("Falha ao descarregar comando: {e}"))
}

#[tauri::command]
fn stop_runtime(state: State<'_, RuntimeState>) -> Result<(), String> {
    let mut runtime = state.0.lock().map_err(|_| "Runtime lock poisoned".to_string())?;
    runtime.take();
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(RuntimeState::default())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            read_project_entries,
            read_project_file,
            start_runtime,
            send_runtime_command,
            stop_runtime
        ])
        .run(tauri::generate_context!())
        .expect("failed to run KardecAgent Studio");
}
