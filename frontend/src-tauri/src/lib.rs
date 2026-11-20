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
fn resolve_python_command() -> Result<(std::ffi::OsString, Vec<String>), String> {
    let explicit = std::env::var_os("KARDECAGENT_PYTHON");
    let candidates: Vec<(std::ffi::OsString, Vec<String>)> = if let Some(path) = explicit {
        vec![(path, Vec::new())]
    } else if cfg!(windows) {
        vec![("python".into(), Vec::new()), ("py".into(), vec!["-3".into()]), ("python3".into(), Vec::new())]
    } else {
        vec![("python3".into(), Vec::new()), ("python".into(), Vec::new())]
    };
    let mut diagnostics = Vec::new();
    for (program, prefix) in candidates {
        match Command::new(&program).args(&prefix).args(["-c", "import kardecagent, kardecagent.cli"]).output() {
            Ok(output) if output.status.success() => return Ok((program, prefix)),
            Ok(output) => {
                let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
                let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
                let detail = if stderr.is_empty() { stdout } else { stderr };
                diagnostics.push(format!("{}: {}", program.to_string_lossy(), if detail.is_empty() { "Python não encontrou o pacote kardecagent.".to_string() } else { detail }));
            }
            Err(error) => diagnostics.push(format!("{}: {}", program.to_string_lossy(), error)),
        }
    }
    Err(format!("Python com KardecAgent não encontrado. Instale com python -m pip install git+https://github.com/kardecallan566/KardecAgent.git ou defina KARDECAGENT_PYTHON com o caminho completo do python.exe. Diagnóstico: {}", diagnostics.join(" | ")))
}

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
    entries.truncate(1000);
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
fn write_project_file(project_root: String, file_path: String, content: String) -> Result<(), String> {
    if content.len() > 1_000_000 {
        return Err("Arquivo maior que 1 MB: salvamento bloqueado para preservar recursos.".into());
    }
    let path = resolve_workspace_path(&project_root, &file_path)
        .map_err(|e| format!("Não foi possível salvar o arquivo: {e}"))?;
    if !path.is_file() {
        return Err("O caminho selecionado não é um arquivo existente.".into());
    }
    fs::write(path, content.as_bytes()).map_err(|e| format!("Falha ao gravar arquivo: {e}"))
}


fn resolve_new_workspace_path(project_root: &str, requested_path: &str) -> Result<std::path::PathBuf, String> {
    let root = Path::new(project_root).canonicalize()
        .map_err(|e| format!("Não foi possível acessar a raiz do projeto: {e}"))?;
    let relative = Path::new(requested_path);
    if relative.as_os_str().is_empty() || relative.is_absolute() || relative.components().any(|c| !matches!(c, std::path::Component::Normal(_))) {
        return Err("Informe um caminho relativo válido dentro do projeto.".into());
    }
    let candidate = root.join(relative);
    let parent = candidate.parent().ok_or("Caminho de destino inválido.")?;
    let canonical_parent = parent.canonicalize()
        .map_err(|e| format!("A pasta de destino precisa existir: {e}"))?;
    if !canonical_parent.starts_with(&root) {
        return Err("A operação está fora da pasta do projeto.".into());
    }
    if candidate.exists() || candidate.is_symlink() {
        return Err("Já existe um arquivo ou pasta nesse caminho.".into());
    }
    Ok(candidate)
}

#[tauri::command]
fn create_project_file(project_root: String, relative_path: String, content: String) -> Result<(), String> {
    if content.len() > 1_000_000 { return Err("Arquivo maior que 1 MB.".into()); }
    let path = resolve_new_workspace_path(&project_root, &relative_path)?;
    fs::write(path, content.as_bytes()).map_err(|e| format!("Falha ao criar arquivo: {e}"))
}

#[tauri::command]
fn create_project_directory(project_root: String, relative_path: String) -> Result<(), String> {
    let path = resolve_new_workspace_path(&project_root, &relative_path)?;
    fs::create_dir(path).map_err(|e| format!("Falha ao criar pasta: {e}"))
}

#[derive(Serialize)]
struct ProjectRunResult {
    file: String,
    language: String,
    exit_code: i32,
    stdout: String,
    stderr: String,
    timed_out: bool,
}

#[tauri::command]
fn run_project_file(project_root: String, file_path: String, approved: bool) -> Result<ProjectRunResult, String> {
    if !approved { return Err("Execução não autorizada pelo usuário.".into()); }
    let path = resolve_workspace_path(&project_root, &file_path)?;
    if !path.is_file() { return Err("Selecione um arquivo válido para executar.".into()); }
    let root = Path::new(&project_root).canonicalize().map_err(|e| e.to_string())?;
    let extension = path.extension().and_then(|v| v.to_str()).unwrap_or("").to_ascii_lowercase();
    let language = match extension.as_str() {
        "py" => "Python", "js" | "mjs" | "cjs" => "JavaScript", "java" => "Java",
        "go" => "Go", "rs" => "Rust", "php" => "PHP", "rb" => "Ruby",
        "c" => "C", "cpp" | "cc" | "cxx" => "C++",
        _ => return Err("Linguagem não habilitada. Suportados: Python, JavaScript, Java, Go, Rust, PHP, Ruby, C e C++.".into()),
    };
    let script = r#"
import json, os, subprocess, sys, tempfile
root = os.environ["KARDECAGENT_RUN_ROOT"]
source = os.environ["KARDECAGENT_RUN_FILE"]
ext = os.path.splitext(source)[1].lower()
commands = {
 ".py": [sys.executable, source], ".js": ["node", source], ".mjs": ["node", source], ".cjs": ["node", source],
 ".java": ["java", source], ".go": ["go", "run", source], ".php": ["php", source], ".rb": ["ruby", source],
}
try:
    if ext in {".rs", ".c", ".cpp", ".cc", ".cxx"}:
        with tempfile.TemporaryDirectory(prefix="kardecagent-build-") as temp:
            if ext == ".rs":
                binary = os.path.join(temp, "program.exe" if os.name == "nt" else "program")
                compile_argv = ["rustc", source, "-o", binary]
            elif ext == ".c":
                binary = os.path.join(temp, "program.exe" if os.name == "nt" else "program")
                compile_argv = ["gcc", source, "-o", binary]
            else:
                binary = os.path.join(temp, "program.exe" if os.name == "nt" else "program")
                compile_argv = ["g++", source, "-o", binary]
            build = subprocess.run(compile_argv, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, shell=False)
            if build.returncode:
                print(json.dumps({"exit_code":build.returncode,"stdout":build.stdout[-12000:],"stderr":build.stderr[-12000:],"timed_out":False}))
                sys.exit(0)
            argv = [binary]
            result = subprocess.run(argv, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, shell=False)
    else:
        argv = commands[ext]
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, shell=False)
    print(json.dumps({"exit_code":result.returncode,"stdout":result.stdout[-12000:],"stderr":result.stderr[-12000:],"timed_out":False}))
except subprocess.TimeoutExpired as exc:
    print(json.dumps({"exit_code":-1,"stdout":str(exc.stdout or "")[-12000:],"stderr":str(exc.stderr or "")[-12000:],"timed_out":True}))
except FileNotFoundError as exc:
    print(json.dumps({"error":"Runtime ou compilador não encontrado: " + str(exc)}))
    sys.exit(2)
except Exception as exc:
    print(json.dumps({"error":str(exc)}))
    sys.exit(2)
"#;
    let (python, prefix) = resolve_python_command()?;
    let output = Command::new(python).args(&prefix).args(["-c", script])
        .current_dir(&root).env("KARDECAGENT_RUN_ROOT", &root).env("KARDECAGENT_RUN_FILE", &path)
        .output().map_err(|e| format!("Não foi possível iniciar execução: {e}"))?;
    let parsed: Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| format!("Falha ao interpretar saída do executor: {}", String::from_utf8_lossy(&output.stderr)))?;
    if !output.status.success() {
        return Err(parsed.get("error").and_then(Value::as_str).unwrap_or("Falha ao executar arquivo.").to_string());
    }
    Ok(ProjectRunResult {
        file: file_path, language: language.into(),
        exit_code: parsed.get("exit_code").and_then(Value::as_i64).unwrap_or(-1) as i32,
        stdout: parsed.get("stdout").and_then(Value::as_str).unwrap_or("").to_string(),
        stderr: parsed.get("stderr").and_then(Value::as_str).unwrap_or("").to_string(),
        timed_out: parsed.get("timed_out").and_then(Value::as_bool).unwrap_or(false),
    })
}

#[tauri::command]
fn start_runtime(
    app: tauri::AppHandle,
    state: State<'_, RuntimeState>,
    project_path: String,
    ollama_base_url: String,
    llm_model: String,
    llm_timeout_seconds: f64,
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

    let base = ollama_base_url.trim().trim_end_matches('/');
    if !(base.starts_with("http://") || base.starts_with("https://")) || base.len() > 500 { return Err("Endereço Ollama inválido.".into()); }
    if llm_model.trim().is_empty() || llm_model.len() > 200 { return Err("Modelo Ollama inválido.".into()); }
    if !llm_timeout_seconds.is_finite() || !(10.0..=900.0).contains(&llm_timeout_seconds) { return Err("Timeout Ollama deve estar entre 10 e 900 segundos.".into()); }
    let (python, python_prefix) = resolve_python_command()?;
    let mut command = Command::new(python);
    command
        .args(&python_prefix)
        .args(["-m", "kardecagent.cli", "studio", "--project"])
        .arg(&root)
        .current_dir(&root)
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1")
        .env("KARDECAGENT_OLLAMA_BASE_URL", base)
        .env("KARDECAGENT_LLM_MODEL", llm_model.trim())
        .env("KARDECAGENT_LLM_TIMEOUT", llm_timeout_seconds.to_string())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().map_err(|e| format!("Não foi possível iniciar o core Python: {e}"))?;

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


#[derive(Serialize)]
struct OllamaTestResult {
    version: String,
    models: Vec<String>,
    model: String,
    response: String,
}

#[tauri::command]
fn test_ollama_connection(
    ollama_base_url: String,
    llm_model: String,
    llm_timeout_seconds: f64,
) -> Result<OllamaTestResult, String> {
    let base = ollama_base_url.trim().trim_end_matches('/');
    if !(base.starts_with("http://") || base.starts_with("https://")) || base.len() > 500 {
        return Err("Informe um endereço HTTP(S) válido para o Ollama.".into());
    }
    if llm_model.trim().is_empty() || llm_model.len() > 200 {
        return Err("Informe o nome de um modelo Ollama válido.".into());
    }
    if !llm_timeout_seconds.is_finite() || !(10.0..=900.0).contains(&llm_timeout_seconds) {
        return Err("O timeout deve estar entre 10 e 900 segundos.".into());
    }
    let (python, prefix) = resolve_python_command()?;
    let script = r#"
import json, os, sys
import httpx
base = os.environ["KARDECAGENT_OLLAMA_BASE_URL"].rstrip("/")
model = os.environ["KARDECAGENT_LLM_MODEL"]
timeout = float(os.environ["KARDECAGENT_LLM_TIMEOUT"])
try:
    version_response = httpx.get(base + "/api/version", timeout=timeout)
    version_response.raise_for_status()
    version = version_response.json().get("version", "unknown")
    tags_response = httpx.get(base + "/api/tags", timeout=timeout)
    tags_response.raise_for_status()
    models = tags_response.json().get("models", [])
    names = [item.get("name", "") for item in models if isinstance(item, dict)]
    if model not in names and model + ":latest" not in names:
        raise RuntimeError("Modelo não instalado: " + model + ". Modelos disponíveis: " + (", ".join(names) or "(nenhum)"))
    response = httpx.post(base + "/api/chat", json={"model": model, "messages": [{"role":"system","content":"Reply with exactly: KARDECAGENT_OK"},{"role":"user","content":"Health check."}],"stream":False,"options":{"temperature":0.0,"num_predict":32}}, timeout=timeout)
    response.raise_for_status()
    answer = (response.json().get("message") or {}).get("content", "")
    if not isinstance(answer, str) or "KARDECAGENT_OK" not in answer:
        raise RuntimeError("O modelo respondeu, mas não passou no teste de inferência.")
    print(json.dumps({"ok":True,"version":version,"models":names,"model":model,"response":"Teste de inferência concluído: KARDECAGENT_OK"}))
except Exception as exc:
    print(json.dumps({"ok":False,"error":str(exc)}))
    sys.exit(2)
"#;
    let output = Command::new(python)
        .args(&prefix)
        .args(["-c", script])
        .env("KARDECAGENT_OLLAMA_BASE_URL", base)
        .env("KARDECAGENT_LLM_MODEL", llm_model.trim())
        .env("KARDECAGENT_LLM_TIMEOUT", llm_timeout_seconds.to_string())
        .output()
        .map_err(|error| format!("Falha ao executar o teste do Ollama: {error}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let result: Value = serde_json::from_str(stdout.trim())
        .map_err(|_| format!("Resposta inválida do teste Ollama: {}", String::from_utf8_lossy(&output.stderr)))?;
    if !output.status.success() {
        return Err(result.get("error").and_then(Value::as_str).unwrap_or("Falha no teste do Ollama.").to_string());
    }
    let models = result.get("models").and_then(Value::as_array).ok_or("Lista de modelos inválida.")?
        .iter().filter_map(Value::as_str).map(str::to_string).collect();
    Ok(OllamaTestResult {
        version: result.get("version").and_then(Value::as_str).unwrap_or("unknown").to_string(),
        models,
        model: result.get("model").and_then(Value::as_str).unwrap_or("").to_string(),
        response: result.get("response").and_then(Value::as_str).unwrap_or("OK").to_string(),
    })
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
            write_project_file,
            create_project_file,
            create_project_directory,
            run_project_file,
            start_runtime,
            test_ollama_connection,
            send_runtime_command,
            stop_runtime
        ])
        .run(tauri::generate_context!())
        .expect("failed to run KardecAgent Studio");
}
