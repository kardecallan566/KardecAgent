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
use tauri::{Emitter, Manager, State};

#[derive(Serialize)]
struct ProjectEntry {
    name: String,
    path: String,
    is_dir: bool,
}


#[cfg(windows)]
fn configure_hidden_process(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x08000000);
}
#[cfg(not(windows))]
fn configure_hidden_process(_command: &mut Command) {}

const MAX_RUNTIME_LINE_BYTES: usize = 256 * 1024 + 1;
const MAX_RUNTIME_LOG_BYTES: usize = 8 * 1024;
fn resolve_python_command(python_override: Option<&str>) -> Result<(std::ffi::OsString, Vec<String>), String> {
    let explicit = python_override
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(std::ffi::OsString::from)
        .or_else(|| std::env::var_os("KARDECAGENT_PYTHON").filter(|value| !value.is_empty()));
    let candidates: Vec<(std::ffi::OsString, Vec<String>)> = if let Some(path) = explicit {
        vec![(path, Vec::new())]
    } else if cfg!(windows) {
        vec![("python".into(), Vec::new()), ("py".into(), vec!["-3".into()]), ("python3".into(), Vec::new())]
    } else {
        vec![("python3".into(), Vec::new()), ("python".into(), Vec::new())]
    };
    let mut diagnostics = Vec::new();
    for (program, prefix) in candidates {
        let mut probe = Command::new(&program);
        probe.args(&prefix).args(["-c", "import kardecagent, kardecagent.cli"]);
        configure_hidden_process(&mut probe);
        match probe.output() {
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
    stdin: Option<ChildStdin>,
    python_executable: String,
    launch_mode: String,
    project_root: String,
    exited: bool,
}

impl Drop for RuntimeProcess {
    fn drop(&mut self) {
        if !self.exited {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

#[derive(Default)]
struct RuntimeState {
    process: Mutex<Option<RuntimeProcess>>,
    last_exit_code: Mutex<Option<i32>>,
}

#[derive(Serialize)]
struct RuntimeDiagnostics {
    running: bool,
    pid: Option<u32>,
    python_executable: Option<String>,
    launch_mode: Option<String>,
    project_root: Option<String>,
    last_exit_code: Option<i32>,
}

#[tauri::command]
fn runtime_diagnostics(state: State<'_, RuntimeState>) -> Result<RuntimeDiagnostics, String> {
    let mut process_slot = state.process.lock().map_err(|_| "Runtime lock poisoned".to_string())?;
    let mut last_exit = state.last_exit_code.lock().map_err(|_| "Runtime diagnostics lock poisoned".to_string())?;
    if let Some(process) = process_slot.as_mut() {
        if let Some(status) = process.child.try_wait().map_err(|e| format!("Falha ao verificar o processo Python: {e}"))? {
            let code = status.code().unwrap_or(-1);
            process.exited = true;
            *last_exit = Some(code);
            process_slot.take();
        } else {
            return Ok(RuntimeDiagnostics {
                running: true,
                pid: Some(process.child.id()),
                python_executable: Some(process.python_executable.clone()),
                launch_mode: Some(process.launch_mode.clone()),
                project_root: Some(process.project_root.clone()),
                last_exit_code: *last_exit,
            });
        }
    }
    Ok(RuntimeDiagnostics { running: false, pid: None, python_executable: None, launch_mode: None, project_root: None, last_exit_code: *last_exit })
}

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
            if name == ".kardecagent" {
                return None;
            }
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

#[derive(Serialize)]
struct ProjectSearchResult {
    path: String,
    name: String,
    line: usize,
    preview: String,
}

#[tauri::command]
fn search_project_files(project_root: String, query: String) -> Result<Vec<ProjectSearchResult>, String> {
    let query = query.trim();
    if query.is_empty() {
        return Ok(Vec::new());
    }
    if query.chars().count() > 200 {
        return Err("A busca aceita no máximo 200 caracteres.".into());
    }
    let root = Path::new(&project_root)
        .canonicalize()
        .map_err(|e| format!("Não foi possível acessar a raiz do projeto: {e}"))?;
    if !root.is_dir() {
        return Err("A raiz do workspace não é uma pasta.".into());
    }

    const MAX_FILES: usize = 2000;
    const MAX_RESULTS: usize = 100;
    const MAX_FILE_BYTES: u64 = 256 * 1024;
    const MAX_TOTAL_BYTES: u64 = 64 * 1024 * 1024;
    const IGNORED: &[&str] = &[
        ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
        "dist", "build", ".next", ".expo", "__pycache__",
        ".pytest_cache", ".mypy_cache", ".kardecagent", "target",
    ];
    let needle = query.to_lowercase();
    let mut pending = vec![root.clone()];
    let mut results = Vec::new();
    let mut visited_files = 0usize;
    let mut scanned_bytes = 0u64;

    while let Some(directory) = pending.pop() {
        let entries = match fs::read_dir(&directory) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let mut entries = entries.filter_map(Result::ok).collect::<Vec<_>>();
        entries.sort_by_key(|entry| entry.file_name().to_string_lossy().to_lowercase());

        for entry in entries.into_iter().rev() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            let file_type = match entry.file_type() {
                Ok(value) => value,
                Err(_) => continue,
            };
            // Never follow symlinks: they can escape the workspace root.
            if file_type.is_symlink() {
                continue;
            }
            if file_type.is_dir() {
                if !IGNORED.iter().any(|ignored| name.eq_ignore_ascii_case(ignored)) {
                    pending.push(path);
                }
                continue;
            }
            if !file_type.is_file() || visited_files >= MAX_FILES || scanned_bytes >= MAX_TOTAL_BYTES {
                continue;
            }
            visited_files += 1;
            let relative = match path.strip_prefix(&root) {
                Ok(value) => value.to_string_lossy().replace('\\', "/"),
                Err(_) => continue,
            };

            if relative.to_lowercase().contains(&needle) {
                results.push(ProjectSearchResult {
                    path: path.to_string_lossy().into_owned(),
                    name: relative.clone(),
                    line: 0,
                    preview: "Correspondência no nome do arquivo".into(),
                });
                if results.len() >= MAX_RESULTS { return Ok(results); }
            }

            let metadata = match fs::metadata(&path) {
                Ok(value) if value.len() <= MAX_FILE_BYTES => value,
                _ => continue,
            };
            let bytes = match fs::read(&path) {
                Ok(value) if value.len() as u64 <= metadata.len() => value,
                _ => continue,
            };
            scanned_bytes = scanned_bytes.saturating_add(bytes.len() as u64);
            if scanned_bytes > MAX_TOTAL_BYTES { break; }
            if bytes.contains(&0) { continue; }
            let content = match String::from_utf8(bytes) {
                Ok(value) => value,
                Err(_) => continue,
            };
            for (index, line) in content.lines().enumerate() {
                if line.to_lowercase().contains(&needle) {
                    let compact = line.trim();
                    let preview = if compact.chars().count() > 240 {
                        format!("{}…", compact.chars().take(240).collect::<String>())
                    } else {
                        compact.to_string()
                    };
                    results.push(ProjectSearchResult {
                        path: path.to_string_lossy().into_owned(),
                        name: relative.clone(),
                        line: index + 1,
                        preview,
                    });
                    if results.len() >= MAX_RESULTS { return Ok(results); }
                }
            }
        }
        if visited_files >= MAX_FILES || scanned_bytes >= MAX_TOTAL_BYTES { break; }
    }
    Ok(results)
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

#[derive(Serialize)]
struct QualityResult {
    file: String,
    language: String,
    tool: String,
    available: bool,
    exit_code: i32,
    output: String,
    fixed: bool,
}

#[tauri::command]
fn quality_project_file(
    project_root: String,
    file_path: String,
    fix: bool,
    approved: bool,
) -> Result<QualityResult, String> {
    if fix && !approved {
        return Err("Correção automática não autorizada pelo usuário.".into());
    }
    let path = resolve_workspace_path(&project_root, &file_path)?;
    if !path.is_file() {
        return Err("Selecione um arquivo válido para analisar.".into());
    }
    let root = Path::new(&project_root).canonicalize().map_err(|e| e.to_string())?;
    let extension = path.extension().and_then(|v| v.to_str()).unwrap_or("").to_ascii_lowercase();
    let relative = path.strip_prefix(&root).map_err(|_| "Arquivo fora do projeto.")?;
    let relative = relative.to_string_lossy().into_owned();
    let (language, tool, args): (&str, &str, Vec<String>) = match extension.as_str() {
        "py" | "pyi" => ("Python", "ruff", if fix { vec!["check".into(), "--fix".into(), relative.clone()] } else { vec!["check".into(), relative.clone()] }),
        "js" | "jsx" | "mjs" | "cjs" | "ts" | "tsx" | "mts" | "cts" => ("JavaScript/TypeScript", "eslint", if fix { vec!["--fix".into(), relative.clone()] } else { vec![relative.clone()] }),
        "go" => ("Go", "gofmt", if fix { vec!["-w".into(), relative.clone()] } else { vec!["-d".into(), relative.clone()] }),
        "rs" => ("Rust", "rustfmt", if fix { vec![relative.clone()] } else { vec!["--check".into(), relative.clone()] }),
        "c" | "h" | "cc" | "cpp" | "cxx" | "hpp" | "hh" | "hxx" => ("C/C++", "clang-format", if fix { vec!["-i".into(), relative.clone()] } else { vec!["--dry-run".into(), "--Werror".into(), relative.clone()] }),
        "java" => ("Java", "google-java-format", if fix { vec!["-i".into(), relative.clone()] } else { vec!["--dry-run".into(), "--set-exit-if-changed".into(), relative.clone()] }),
        "php" => ("PHP", if fix { "php-cs-fixer" } else { "php" }, if fix { vec!["fix".into(), relative.clone()] } else { vec!["-l".into(), relative.clone()] }),
        "rb" => ("Ruby", "rubocop", if fix { vec!["--autocorrect".into(), relative.clone()] } else { vec![relative.clone()] }),
        "sh" | "bash" => ("Shell", if fix { "shfmt" } else { "shellcheck" }, if fix { vec!["-w".into(), relative.clone()] } else { vec![relative.clone()] }),
        "html" | "htm" | "css" | "scss" | "json" | "md" | "mdx" | "yaml" | "yml" => ("Web/Config", "prettier", if fix { vec!["--write".into(), relative.clone()] } else { vec!["--check".into(), relative.clone()] }),
        _ => return Ok(QualityResult {
            file: file_path, language: "Não identificado".into(), tool: "—".into(),
            available: false, exit_code: -1,
            output: "A IDE ainda não possui uma regra de lint/correção configurada para esta extensão.".into(),
            fixed: false,
        }),
    };

    let mut program = tool.to_string();
    let mut command_args = args.clone();
    let local_script = if tool == "eslint" {
        let candidate = root.join("node_modules").join("eslint").join("bin").join("eslint.js");
        candidate.is_file().then_some(candidate)
    } else if tool == "prettier" {
        let candidates = [
            root.join("node_modules").join("prettier").join("bin").join("prettier.cjs"),
            root.join("node_modules").join("prettier").join("bin-prettier.js"),
        ];
        candidates.into_iter().find(|candidate| candidate.is_file())
    } else { None };
    if let Some(script_path) = local_script {
        program = "node".into();
        command_args.insert(0, script_path.to_string_lossy().into_owned());
    } else if tool == "ruff" {
        let candidates = [root.join(".venv").join("Scripts").join("ruff.exe"), root.join(".venv").join("bin").join("ruff")];
        if let Some(local_ruff) = candidates.iter().find(|candidate| candidate.is_file()) {
            program = local_ruff.to_string_lossy().into_owned();
        }
    }
    let mut command = Command::new(&program);
    configure_hidden_process(&mut command);
    let output = match command.args(&command_args).current_dir(&root).output() {
        Ok(output) => output,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Ok(QualityResult {
                file: file_path, language: language.into(), tool: tool.into(),
                available: false, exit_code: -1,
                output: format!("A ferramenta '{}' não está instalada ou não está no PATH. Instale-a no ambiente do projeto e tente novamente.", tool),
                fixed: false,
            });
        }
        Err(error) => return Err(format!("Não foi possível iniciar {}: {}", tool, error)),
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = [stdout.trim(), stderr.trim()].into_iter().filter(|part| !part.is_empty()).collect::<Vec<_>>().join("\n");
    let success = output.status.success();
    let message = if combined.is_empty() {
        if success { if fix { "Correções aplicadas; nenhuma saída adicional." } else { "Nenhum problema encontrado." } }
        else { "A ferramenta encontrou problemas, mas não retornou detalhes." }
    } else { "" };
    Ok(QualityResult {
        file: file_path, language: language.into(), tool: tool.into(), available: true,
        exit_code: output.status.code().unwrap_or(-1),
        output: if combined.is_empty() { message.into() } else { combined },
        fixed: fix && success,
    })
}

#[tauri::command]
fn format_project_file(
    project_root: String,
    file_path: String,
    approved: bool,
) -> Result<QualityResult, String> {
    if !approved {
        return Err("Formatação automática não autorizada pelo usuário.".into());
    }
    let path = resolve_workspace_path(&project_root, &file_path)?;
    if !path.is_file() {
        return Err("Selecione um arquivo válido para formatar.".into());
    }
    let root = Path::new(&project_root).canonicalize().map_err(|e| e.to_string())?;
    let relative = path.strip_prefix(&root).map_err(|_| "Arquivo fora do projeto.")?;
    let relative = relative.to_string_lossy().into_owned();
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("").to_ascii_lowercase();
    let (language, tool, args): (&str, &str, Vec<String>) = match extension.as_str() {
        "py" | "pyi" => ("Python", "ruff", vec!["format".into(), relative.clone()]),
        "js" | "jsx" | "mjs" | "cjs" | "ts" | "tsx" | "mts" | "cts" | "html" | "htm" | "css" | "scss" | "json" | "md" | "mdx" | "yaml" | "yml" => ("Web/Config", "prettier", vec!["--write".into(), relative.clone()]),
        "go" => ("Go", "gofmt", vec!["-w".into(), relative.clone()]),
        "rs" => ("Rust", "rustfmt", vec![relative.clone()]),
        "c" | "h" | "cc" | "cpp" | "cxx" | "hpp" | "hh" | "hxx" => ("C/C++", "clang-format", vec!["-i".into(), relative.clone()]),
        "java" => ("Java", "google-java-format", vec!["-i".into(), relative.clone()]),
        "sh" | "bash" => ("Shell", "shfmt", vec!["-w".into(), relative.clone()]),
        _ => return Ok(QualityResult {
            file: file_path, language: "Não identificado".into(), tool: "—".into(),
            available: false, exit_code: -1,
            output: "Não há formatador configurado para esta extensão. Nenhuma alteração foi feita.".into(),
            fixed: false,
        }),
    };
    let mut program = tool.to_string();
    let mut command_args = args;
    if tool == "prettier" {
        let candidates = [
            root.join("node_modules").join("prettier").join("bin").join("prettier.cjs"),
            root.join("node_modules").join("prettier").join("bin-prettier.js"),
        ];
        if let Some(script) = candidates.into_iter().find(|candidate| candidate.is_file()) {
            program = "node".into();
            command_args.insert(0, script.to_string_lossy().into_owned());
        }
    } else if tool == "ruff" {
        let candidates = [root.join(".venv").join("Scripts").join("ruff.exe"), root.join(".venv").join("bin").join("ruff")];
        if let Some(local) = candidates.iter().find(|candidate| candidate.is_file()) {
            program = local.to_string_lossy().into_owned();
        }
    }
    let mut command = Command::new(&program);
    configure_hidden_process(&mut command);
    let output = match command.args(&command_args).current_dir(&root).output() {
        Ok(output) => output,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(QualityResult {
            file: file_path, language: language.into(), tool: tool.into(), available: false,
            exit_code: -1, output: format!("O formatador '{}' não está instalado ou não está no PATH. Nenhuma alteração foi feita.", tool),
            fixed: false,
        }),
        Err(error) => return Err(format!("Não foi possível iniciar {}: {}", tool, error)),
    };
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let combined = [stdout.trim(), stderr.trim()].into_iter().filter(|part| !part.is_empty()).collect::<Vec<_>>().join("\n");
    let success = output.status.success();
    Ok(QualityResult {
        file: file_path, language: language.into(), tool: tool.into(), available: true,
        exit_code: output.status.code().unwrap_or(-1),
        output: if combined.is_empty() {
            if success { "Formatação concluída.".into() } else { "O formatador encontrou um erro.".into() }
        } else { combined },
        fixed: success,
    })
}

#[tauri::command]
fn run_project_file(project_root: String, file_path: String, approved: bool) -> Result<ProjectRunResult, String> {
    if !approved { return Err("Execução não autorizada pelo usuário.".into()); }
    let path = resolve_workspace_path(&project_root, &file_path)?;
    if !path.is_file() { return Err("Selecione um arquivo válido para executar.".into()); }
    let root = Path::new(&project_root).canonicalize().map_err(|e| e.to_string())?;
    let extension = path.extension().and_then(|v| v.to_str()).unwrap_or("").to_ascii_lowercase();
    let language = match extension.as_str() {
        "py" => "Python", "js" | "mjs" | "cjs" | "jsx" => "JavaScript",
        "ts" | "mts" | "cts" | "tsx" => "TypeScript", "java" => "Java",
        "go" => "Go", "rs" => "Rust", "php" => "PHP", "rb" => "Ruby",
        "c" => "C", "cpp" | "cc" | "cxx" => "C++",
        "kt" | "kts" => "Kotlin", "swift" => "Swift",
        "dart" => "Dart", "lua" => "Lua", "sh" | "bash" => "Shell",
        "pl" => "Perl", "r" => "R", "scala" => "Scala", "ex" | "exs" => "Elixir",
        "ps1" => "PowerShell",
        _ => return Err("Linguagem sem executor integrado. A IDE ainda permite editar e aplicar lint às linguagens identificadas.".into()),
    };
    let script = r#"
import json, os, subprocess, sys, tempfile
root = os.environ["KARDECAGENT_RUN_ROOT"]
source = os.environ["KARDECAGENT_RUN_FILE"]
ext = os.path.splitext(source)[1].lower()
commands = {
 ".py": [sys.executable, source],
 ".js": ["node", source], ".mjs": ["node", source], ".cjs": ["node", source],
 ".ts": ["node", "--experimental-strip-types", source], ".mts": ["node", "--experimental-strip-types", source],
 ".cts": ["node", "--experimental-strip-types", source],
 ".java": ["java", source], ".go": ["go", "run", source], ".php": ["php", source], ".rb": ["ruby", source],
 ".kt": ["kotlin", source], ".kts": ["kotlinc", "-script", source], ".swift": ["swift", source],
 ".dart": ["dart", "run", source], ".lua": ["lua", source], ".sh": ["bash", source], ".bash": ["bash", source],
 ".pl": ["perl", source], ".r": ["Rscript", source], ".scala": ["scala", source],
 ".ex": ["elixir", source], ".exs": ["elixir", source], ".ps1": ["powershell", "-NoProfile", "-File", source],
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
        if ext in {".jsx", ".tsx"}:
            tsx_cli = os.path.join(root, "node_modules", "tsx", "dist", "cli.mjs")
            if not os.path.isfile(tsx_cli):
                raise FileNotFoundError("Para executar JSX/TSX, instale a dependência tsx no projeto (npm install -D tsx).")
            argv = ["node", tsx_cli, source]
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
    let (python, prefix) = resolve_python_command(None)?;
    let mut command = Command::new(&python);
    configure_hidden_process(&mut command);
    let output = command.args(&prefix).args(["-c", script])
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
    python_executable: Option<String>,
) -> Result<(), String> {
    let root = Path::new(&project_path)
        .canonicalize()
        .map_err(|e| format!("Projeto inválido: {e}"))?;
    if !root.is_dir() {
        return Err("O caminho do projeto não é uma pasta.".into());
    }

    let mut runtime = state.process.lock().map_err(|_| "Runtime lock poisoned".to_string())?;
    if let Some(existing) = runtime.as_mut() {
        if let Some(status) = existing.child.try_wait().map_err(|e| format!("Falha ao verificar o Core: {e}"))? {
            existing.exited = true;
            *state.last_exit_code.lock().map_err(|_| "Runtime diagnostics lock poisoned".to_string())? = Some(status.code().unwrap_or(-1));
            runtime.take();
        } else {
            return Err(format!("O Core já está em execução (PID {}).", existing.child.id()));
        }
    }

    let base = ollama_base_url.trim().trim_end_matches('/');
    if !(base.starts_with("http://") || base.starts_with("https://")) || base.len() > 500 { return Err("Endereço Ollama inválido.".into()); }
    if llm_model.trim().is_empty() || llm_model.len() > 200 { return Err("Modelo Ollama inválido.".into()); }
    if !llm_timeout_seconds.is_finite() || !(10.0..=900.0).contains(&llm_timeout_seconds) { return Err("Timeout Ollama deve estar entre 10 e 900 segundos.".into()); }
    let has_python_override = python_executable.as_deref().map(str::trim).filter(|value| !value.is_empty()).is_some()
        || std::env::var_os("KARDECAGENT_PYTHON").is_some();
    let preferred_python = if has_python_override {
        Some(resolve_python_command(python_executable.as_deref())?)
    } else {
        None
    };
    let bundled_core = app.path().resolve("core-dist/kardecagent-core.exe", tauri::path::BaseDirectory::Resource)
        .ok().filter(|path| path.is_file());
    let (executable, launch_prefix, launch_mode) = if let Some((python, prefix)) = preferred_python {
        (python, prefix, "python".to_string())
    } else if let Some(core_path) = bundled_core {
        (core_path.into_os_string(), Vec::new(), "bundled".to_string())
    } else {
        let (python, prefix) = resolve_python_command(None)?;
        (python, prefix, "python".to_string())
    };
    let mut launch_args = launch_prefix;
    if launch_mode == "bundled" {
        launch_args.extend(["studio".into(), "--project".into()]);
    } else {
        launch_args.extend(["-m".into(), "kardecagent.cli".into(), "studio".into(), "--project".into()]);
    }
    let mut command = Command::new(&executable);
    configure_hidden_process(&mut command);
    command
        .args(&launch_args)
        .arg(&root)
        .current_dir(&root)
        .env("PYTHONIOENCODING", "utf-8:strict")
        .env("PYTHONUTF8", "1")
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
                    // A child process or native dependency may write arbitrary bytes to stdout.
                    // Preserve valid JSON frames by replacing malformed byte sequences instead
                    // of treating one bad byte as a fatal protocol-stream failure.
                    let line = String::from_utf8_lossy(&bytes).into_owned();
                    if let Ok(message) = serde_json::from_str::<Value>(&line) {
                        let _ = stdout_app.emit("runtime-message", message);
                    } else {
                        let _ = stdout_app.emit("runtime-protocol-error", line);
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

    let executable_label = executable.to_string_lossy().into_owned();
    *runtime = Some(RuntimeProcess {
        child,
        stdin: Some(stdin),
        python_executable: executable_label,
        launch_mode,
        project_root: root.to_string_lossy().into_owned(),
        exited: false,
    });
    *state.last_exit_code.lock().map_err(|_| "Runtime diagnostics lock poisoned".to_string())? = None;
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
    app: tauri::AppHandle,
    ollama_base_url: String,
    llm_model: String,
    llm_timeout_seconds: f64,
    python_executable: Option<String>,
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

    let has_python_override = python_executable.as_deref().map(str::trim).filter(|value| !value.is_empty()).is_some()
        || std::env::var_os("KARDECAGENT_PYTHON").is_some();
    let preferred_python = if has_python_override {
        Some(resolve_python_command(python_executable.as_deref())?)
    } else {
        None
    };
    let bundled_core = app.path().resolve("core-dist/kardecagent-core.exe", tauri::path::BaseDirectory::Resource)
        .ok().filter(|path| path.is_file());
    let (executable, args) = if let Some((python, prefix)) = preferred_python {
        let mut args = prefix;
        args.extend(["-m".into(), "kardecagent.cli".into(), "ollama-health".into()]);
        (python, args)
    } else if let Some(core_path) = bundled_core {
        (core_path.into_os_string(), vec!["ollama-health".to_string()])
    } else {
        let (python, prefix) = resolve_python_command(None)?;
        let mut args = prefix;
        args.extend(["-m".into(), "kardecagent.cli".into(), "ollama-health".into()]);
        (python, args)
    };

    let mut command = Command::new(&executable);
    configure_hidden_process(&mut command);
    let output = command
        .args(&args)
        .env("KARDECAGENT_OLLAMA_BASE_URL", base)
        .env("KARDECAGENT_LLM_MODEL", llm_model.trim())
        .env("KARDECAGENT_LLM_TIMEOUT", llm_timeout_seconds.to_string())
        .output()
        .map_err(|error| format!("Falha ao executar o diagnóstico do Ollama: {error}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let result: Value = serde_json::from_str(stdout.trim()).map_err(|_| {
        let stderr = String::from_utf8_lossy(&output.stderr);
        if !output.status.success() && !stderr.trim().is_empty() {
            format!("Diagnóstico do Ollama falhou: {}", stderr.trim())
        } else {
            format!("Resposta inválida do diagnóstico do Ollama (código {}): {}",
                output.status.code().unwrap_or(-1), stderr.trim())
        }
    })?;
    if !output.status.success() {
        return Err(result.get("error").and_then(Value::as_str)
            .unwrap_or("Falha no teste do Ollama.").to_string());
    }
    let models = result.get("models").and_then(Value::as_array).ok_or("Lista de modelos inválida.")?
        .iter().filter_map(Value::as_str).map(str::to_string).collect();
    let version = result.get("version").and_then(Value::as_str)
        .filter(|value| !value.is_empty()).ok_or("Versão do Ollama ausente.")?;
    let model = result.get("model").and_then(Value::as_str)
        .filter(|value| !value.is_empty()).ok_or("Modelo configurado ausente.")?;
    let response = result.get("response").and_then(Value::as_str)
        .filter(|value| *value == "KARDECAGENT_OK").ok_or("Resposta de saúde do Ollama inválida.")?;
    Ok(OllamaTestResult {
        version: version.to_string(),
        models,
        model: model.to_string(),
        response: response.to_string(),
    })
}

#[tauri::command]
fn switch_runtime_workspace(state: State<'_, RuntimeState>, project_path: String) -> Result<(), String> {
    let root = Path::new(&project_path)
        .canonicalize()
        .map_err(|e| format!("Projeto inválido: {e}"))?;
    if !root.is_dir() {
        return Err("O caminho do projeto não é uma pasta.".into());
    }
    let mut runtime = state.process.lock().map_err(|_| "Runtime lock poisoned".to_string())?;
    let process = runtime.as_mut().ok_or("Core não iniciado.")?;
    if let Some(status) = process.child.try_wait().map_err(|e| format!("Falha ao verificar o Core: {e}"))? {
        let code = status.code().unwrap_or(-1);
        process.exited = true;
        *state.last_exit_code.lock().map_err(|_| "Runtime diagnostics lock poisoned".to_string())? = Some(code);
        runtime.take();
        return Err(format!("O Core encerrou antes da troca de projeto (código {code})."));
    }
    let command_id = format!(
        "workspace-switch-{}-{}",
        process.child.id(),
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis()
    );
    let message = serde_json::json!({
        "type": "workspace.change",
        "schema_version": 1,
        "command_id": command_id,
        "payload": { "project_root": root.to_string_lossy() }
    });
    let encoded = serde_json::to_vec(&message).map_err(|e| format!("Falha ao preparar troca de projeto: {e}"))?;
    let stdin = process.stdin.as_mut().ok_or("Entrada do Core indisponível.")?;
    stdin.write_all(&encoded).map_err(|e| format!("Falha ao enviar novo workspace ao Core: {e}"))?;
    stdin.write_all(b"\n").map_err(|e| format!("Falha ao finalizar troca de workspace: {e}"))?;
    stdin.flush().map_err(|e| format!("Falha ao confirmar troca de workspace: {e}"))?;
    process.project_root = root.to_string_lossy().into_owned();
    Ok(())
}

#[tauri::command]
fn send_runtime_command(state: State<'_, RuntimeState>, mut message: Value) -> Result<(), String> {
    let mut runtime = state.process.lock().map_err(|_| "Runtime lock poisoned".to_string())?;
    let process = runtime.as_mut().ok_or("Runtime não iniciado.")?;
    if let Some(status) = process.child.try_wait().map_err(|e| format!("Falha ao verificar o Core: {e}"))? {
        let code = status.code().unwrap_or(-1);
        process.exited = true;
        *state.last_exit_code.lock().map_err(|_| "Runtime diagnostics lock poisoned".to_string())? = Some(code);
        runtime.take();
        return Err(format!("O processo Python encerrou antes de receber o comando (código de saída: {code})."));
    }
    // The runtime owns one canonical workspace. Do not trust a raw frontend path here:
    // Windows may represent the same directory differently (case, junctions, long paths).
    // Inject the exact workspace associated with this live process for task commands.
    if matches!(message.get("type").and_then(Value::as_str), Some("task.submit") | Some("task.resume")) {
        let payload = message.get_mut("payload")
            .and_then(Value::as_object_mut)
            .ok_or("Payload do comando inválido.")?;
        payload.insert("project_root".to_string(), Value::String(process.project_root.clone()));
    }
    let encoded = serde_json::to_string(&message)
        .map_err(|e| format!("Comando inválido: {e}"))?;
    if encoded.len() > 64 * 1024 {
        return Err("Comando excede o limite de 64 KiB.".into());
    }
    let stdin = process.stdin.as_mut().ok_or("Entrada do Core indisponível.")?;
    stdin.write_all(encoded.as_bytes()).map_err(|e| format!("Falha ao enviar comando ao runtime: {e}"))?;
    stdin.write_all(b"\n").map_err(|e| format!("Falha ao finalizar comando: {e}"))?;
    stdin.flush().map_err(|e| format!("Falha ao descarregar comando: {e}"))
}

#[tauri::command]
fn stop_runtime(state: State<'_, RuntimeState>) -> Result<(), String> {
    let process = state.process.lock().map_err(|_| "Runtime lock poisoned".to_string())?.take();
    let Some(mut process) = process else { return Ok(()); };

    // EOF is the protocol's graceful shutdown signal. Do not kill Python while it can exit normally.
    process.stdin.take();
    let mut exit_code = None;
    for _ in 0..30 {
        if let Some(status) = process.child.try_wait().map_err(|e| format!("Falha ao aguardar o Core: {e}"))? {
            exit_code = Some(status.code().unwrap_or(-1));
            break;
        }
        thread::sleep(std::time::Duration::from_millis(100));
    }
    if exit_code.is_none() {
        let _ = process.child.kill();
        let status = process.child.wait().map_err(|e| format!("Falha ao finalizar o Core: {e}"))?;
        exit_code = Some(status.code().unwrap_or(-1));
    }
    process.exited = true;
    *state.last_exit_code.lock().map_err(|_| "Runtime diagnostics lock poisoned".to_string())? = exit_code;
    Ok(())
}


#[derive(Serialize)]
struct GitFileChange {
    path: String,
    status: String,
    staged: bool,
    unstaged: bool,
    untracked: bool,
}

#[derive(Serialize)]
struct GitStatusResult {
    is_repo: bool,
    branch: String,
    ahead: usize,
    behind: usize,
    files: Vec<GitFileChange>,
}

#[derive(Serialize)]
struct GitDiffResult {
    staged: String,
    working: String,
}

fn canonical_git_root(project_root: &str) -> Result<std::path::PathBuf, String> {
    let root = Path::new(project_root).canonicalize()
        .map_err(|error| format!("Projeto Git inválido: {error}"))?;
    if !root.is_dir() {
        return Err("O workspace Git precisa ser uma pasta.".into());
    }
    let output = Command::new("git")
        .args(["rev-parse", "--show-toplevel"])
        .current_dir(&root)
        .output()
        .map_err(|error| format!("Git não está disponível: {error}"))?;
    if !output.status.success() {
        return Err("A pasta aberta não pertence a um repositório Git.".into());
    }
    let repository = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let repository = Path::new(&repository).canonicalize()
        .map_err(|error| format!("Não foi possível resolver a raiz Git: {error}"))?;
    if repository != root {
        return Err("Abra a raiz do repositório Git para usar o painel de alterações.".into());
    }
    Ok(root)
}

fn parse_git_status_text(output: &str) -> GitStatusResult {
    let mut branch = "HEAD".to_string();
    let mut ahead = 0usize;
    let mut behind = 0usize;
    let mut files = Vec::new();
    for line in output.lines() {
        if let Some(header) = line.strip_prefix("## ") {
            branch = header.split("...").next().unwrap_or(header).to_string();
            if let Some((_, tracking)) = header.split_once('[') {
                for part in tracking.trim_end_matches(']').split(", ") {
                    if let Some(value) = part.strip_prefix("ahead ") {
                        ahead = value.parse().unwrap_or(0);
                    } else if let Some(value) = part.strip_prefix("behind ") {
                        behind = value.parse().unwrap_or(0);
                    }
                }
            }
            continue;
        }
        if line.len() < 4 { continue; }
        let index = line.chars().next().unwrap_or(' ');
        let worktree = line.chars().nth(1).unwrap_or(' ');
        let raw_path = line.get(3..).unwrap_or("").trim();
        let path = raw_path.rsplit_once(" -> ").map(|(_, new)| new).unwrap_or(raw_path);
        if path.is_empty() { continue; }
        let status = format!("{index}{worktree}");
        files.push(GitFileChange {
            path: path.to_string(),
            status: status.clone(),
            staged: index != ' ' && index != '?',
            unstaged: worktree != ' ' && worktree != '?',
            untracked: status == "??",
        });
    }
    GitStatusResult { is_repo: true, branch, ahead, behind, files }
}

fn git_command(root: &Path, args: &[&str]) -> Result<std::process::Output, String> {
    Command::new("git")
        .args(args)
        .current_dir(root)
        .output()
        .map_err(|error| format!("Não foi possível iniciar Git: {error}"))
}

fn ensure_git_success(output: std::process::Output) -> Result<String, String> {
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if output.status.success() {
        return Ok(stdout);
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    Err(if stderr.is_empty() { format!("Git encerrou com código {}.", output.status.code().unwrap_or(-1)) } else { stderr })
}

fn git_relative_path(root: &Path, requested: &str) -> Result<String, String> {
    let relative = Path::new(requested);
    if requested.trim().is_empty()
        || relative.is_absolute()
        || relative.components().any(|component| !matches!(component, std::path::Component::Normal(_)))
    {
        return Err("Caminho Git relativo inválido.".into());
    }
    let candidate = root.join(relative);
    let parent = candidate.parent().ok_or("Caminho Git sem pasta pai.")?;
    let canonical_parent = parent.canonicalize()
        .map_err(|error| format!("Pasta do arquivo não está disponível: {error}"))?;
    if !canonical_parent.starts_with(root) {
        return Err("O caminho Git está fora do workspace.".into());
    }
    if candidate.exists() {
        let canonical_file = candidate.canonicalize()
            .map_err(|error| format!("Não foi possível validar o arquivo: {error}"))?;
        if !canonical_file.starts_with(root) {
            return Err("O arquivo Git aponta para fora do workspace.".into());
        }
    }
    Ok(relative.to_string_lossy().into_owned())
}

fn bounded_git_text(bytes: &[u8]) -> String {
    const MAX_GIT_OUTPUT_CHARS: usize = 250_000;
    let text = String::from_utf8_lossy(bytes);
    if text.chars().count() <= MAX_GIT_OUTPUT_CHARS {
        text.into_owned()
    } else {
        let mut bounded = text.chars().take(MAX_GIT_OUTPUT_CHARS).collect::<String>();
        bounded.push_str("\n\n[Diff truncado após 250.000 caracteres.]");
        bounded
    }
}

#[tauri::command]
fn git_status(project_root: String) -> Result<GitStatusResult, String> {
    let root = canonical_git_root(&project_root)?;
    let output = git_command(&root, &["-c", "core.quotepath=false", "status", "--short", "--branch", "--untracked-files=all"])?;
    let text = ensure_git_success(output)?;
    Ok(parse_git_status_text(&text))
}

#[tauri::command]
fn git_diff(project_root: String, file_path: String) -> Result<GitDiffResult, String> {
    let root = canonical_git_root(&project_root)?;
    let relative = git_relative_path(&root, &file_path)?;
    let staged = git_command(&root, &["diff", "--cached", "--no-ext-diff", "--no-color", "--unified=3", "--", &relative])?;
    let working = git_command(&root, &["diff", "--no-ext-diff", "--no-color", "--unified=3", "--", &relative])?;
    Ok(GitDiffResult {
        staged: bounded_git_text(&staged.stdout),
        working: bounded_git_text(&working.stdout),
    })
}

#[tauri::command]
fn git_stage(project_root: String, file_path: String) -> Result<GitStatusResult, String> {
    let root = canonical_git_root(&project_root)?;
    let relative = git_relative_path(&root, &file_path)?;
    let output = Command::new("git").args(["add", "--"]).arg(&relative)
        .current_dir(&root).output()
        .map_err(|error| format!("Não foi possível iniciar git add: {error}"))?;
    ensure_git_success(output)?;
    git_status(project_root)
}

#[tauri::command]
fn git_unstage(project_root: String, file_path: String) -> Result<GitStatusResult, String> {
    let root = canonical_git_root(&project_root)?;
    let relative = git_relative_path(&root, &file_path)?;
    let output = Command::new("git").args(["restore", "--staged", "--"]).arg(&relative)
        .current_dir(&root).output()
        .map_err(|error| format!("Não foi possível iniciar git restore: {error}"))?;
    ensure_git_success(output)?;
    git_status(project_root)
}

#[tauri::command]
fn git_commit(project_root: String, message: String) -> Result<GitStatusResult, String> {
    let root = canonical_git_root(&project_root)?;
    let message = message.trim();
    if message.is_empty() || message.chars().count() > 2000 {
        return Err("A mensagem do commit deve conter entre 1 e 2.000 caracteres.".into());
    }
    let output = Command::new("git").arg("commit").arg("-m").arg(message)
        .current_dir(&root).output()
        .map_err(|error| format!("Não foi possível iniciar git commit: {error}"))?;
    ensure_git_success(output)?;
    git_status(project_root)
}

struct TerminalProcess {
    child: Child,
    stdin: Option<ChildStdin>,
    exited: bool,
}
impl Drop for TerminalProcess {
    fn drop(&mut self) {
        if !self.exited {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}
#[derive(Default)]
struct TerminalInner {
    process: Option<TerminalProcess>,
    last_exit_code: Option<i32>,
}
#[derive(Default)]
struct TerminalState(Mutex<TerminalInner>);

#[derive(Serialize)]
struct TerminalDiagnostics {
    running: bool,
    pid: Option<u32>,
    last_exit_code: Option<i32>,
}

#[tauri::command]
fn terminal_diagnostics(state: State<'_, TerminalState>) -> Result<TerminalDiagnostics, String> {
    let mut inner = state.0.lock().map_err(|_| "Terminal lock poisoned".to_string())?;
    let status = match inner.process.as_mut() {
        Some(process) => process.child.try_wait()
            .map_err(|error| format!("Falha ao verificar terminal: {error}"))?,
        None => None,
    };
    if let Some(status) = status {
        let code = status.code().unwrap_or(-1);
        if let Some(process) = inner.process.as_mut() {
            process.exited = true;
        }
        inner.process.take();
        inner.last_exit_code = Some(code);
    }
    if let Some(process) = inner.process.as_ref() {
        return Ok(TerminalDiagnostics {
            running: true,
            pid: Some(process.child.id()),
            last_exit_code: inner.last_exit_code,
        });
    }
    Ok(TerminalDiagnostics {
        running: false,
        pid: None,
        last_exit_code: inner.last_exit_code,
    })
}

const MAX_TERMINAL_LINE_BYTES: usize = 16 * 1024;

#[tauri::command]
fn start_integrated_terminal(
    app: tauri::AppHandle,
    state: State<'_, TerminalState>,
    project_path: String,
    shell_name: String,
) -> Result<(), String> {
    let root = Path::new(&project_path)
        .canonicalize()
        .map_err(|error| format!("Pasta do terminal inválida: {error}"))?;
    if !root.is_dir() {
        return Err("A pasta selecionada não é um diretório.".into());
    }
    let mut inner = state.0.lock().map_err(|_| "Terminal lock poisoned".to_string())?;
    let status = match inner.process.as_mut() {
        Some(process) => process.child.try_wait()
            .map_err(|error| format!("Falha ao verificar terminal: {error}"))?,
        None => None,
    };
    if let Some(status) = status {
        let code = status.code().unwrap_or(-1);
        if let Some(process) = inner.process.as_mut() {
            process.exited = true;
        }
        inner.process.take();
        inner.last_exit_code = Some(code);
    } else if inner.process.is_some() {
        return Err("O terminal já está em execução.".into());
    }

    #[cfg(windows)]
    let mut command = match shell_name.as_str() {
        "cmd" => { let mut command = Command::new("cmd.exe"); command.args(["/Q"]); command },
        "powershell" => { let mut command = Command::new("powershell.exe"); command.args(["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"]); command },
        _ => return Err("Shell não permitido. Escolha PowerShell ou CMD.".into()),
    };
    #[cfg(not(windows))]
    let mut command = {
        let _ = shell_name;
        let mut command = Command::new("bash");
        command.args(["--noprofile", "--norc"]);
        command
    };
    configure_hidden_process(&mut command);
    let mut child = command
        .current_dir(&root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("Não foi possível iniciar o terminal integrado: {error}"))?;
    let stdin = child.stdin.take().ok_or("Falha ao abrir entrada do terminal.")?;
    let stdout = child.stdout.take().ok_or("Falha ao abrir saída do terminal.")?;
    let stderr = child.stderr.take().ok_or("Falha ao abrir erros do terminal.")?;

    let out_app = app.clone();
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            match read_bounded_line(&mut reader, MAX_TERMINAL_LINE_BYTES) {
                Ok(Some(bytes)) => {
                    let line = String::from_utf8_lossy(&bytes).into_owned();
                    let _ = out_app.emit("terminal-output", line);
                }
                Ok(None) => break,
                Err(error) => {
                    let message = if error.kind() == ErrorKind::InvalidData {
                        "Linha do terminal excedeu 16 KiB e foi descartada.".to_string()
                    } else {
                        format!("Erro de leitura do terminal: {error}")
                    };
                    let _ = out_app.emit("terminal-output", message);
                    if error.kind() != ErrorKind::InvalidData { break; }
                }
            }
        }
        let _ = out_app.emit("terminal-exited", ());
    });
    let err_app = app.clone();
    thread::spawn(move || {
        let mut reader = BufReader::new(stderr);
        loop {
            match read_bounded_line(&mut reader, MAX_TERMINAL_LINE_BYTES) {
                Ok(Some(bytes)) => {
                    let line = String::from_utf8_lossy(&bytes).into_owned();
                    let _ = err_app.emit("terminal-output", line);
                }
                Ok(None) => break,
                Err(error) => {
                    let message = if error.kind() == ErrorKind::InvalidData {
                        "Linha de erro do terminal excedeu 16 KiB e foi descartada.".to_string()
                    } else {
                        format!("Erro de leitura stderr do terminal: {error}")
                    };
                    let _ = err_app.emit("terminal-output", message);
                    if error.kind() != ErrorKind::InvalidData { break; }
                }
            }
        }
    });
    inner.last_exit_code = None;
    inner.process = Some(TerminalProcess { child, stdin: Some(stdin), exited: false });
    Ok(())
}

#[tauri::command]
fn write_integrated_terminal(state: State<'_, TerminalState>, input: String) -> Result<(), String> {
    if input.len() > 16 * 1024 {
        return Err("Comando excede 16 KiB.".into());
    }
    let mut inner = state.0.lock().map_err(|_| "Terminal lock poisoned".to_string())?;
    let status = inner.process.as_mut()
        .ok_or("Terminal não iniciado.")?
        .child.try_wait()
        .map_err(|error| format!("Falha ao verificar terminal: {error}"))?;
    if let Some(status) = status {
        let code = status.code().unwrap_or(-1);
        if let Some(process) = inner.process.as_mut() {
            process.exited = true;
        }
        inner.process.take();
        inner.last_exit_code = Some(code);
        return Err(format!("O terminal encerrou com código {code}."));
    }
    let process = inner.process.as_mut().ok_or("Terminal não iniciado.")?;
    let stdin = process.stdin.as_mut().ok_or("Entrada do terminal indisponível.")?;
    stdin.write_all(input.as_bytes()).map_err(|error| format!("Falha ao enviar comando: {error}"))?;
    stdin.write_all(b"\n").map_err(|error| format!("Falha ao finalizar comando: {error}"))?;
    stdin.flush().map_err(|error| format!("Falha ao descarregar comando: {error}"))
}

#[tauri::command]
fn stop_integrated_terminal(state: State<'_, TerminalState>) -> Result<(), String> {
    let mut inner = state.0.lock().map_err(|_| "Terminal lock poisoned".to_string())?;
    let Some(mut process) = inner.process.take() else { return Ok(()); };

    // EOF lets interactive shells exit normally before the forced-stop fallback.
    process.stdin.take();
    let mut status = None;
    for _ in 0..10 {
        if let Some(result) = process.child.try_wait()
            .map_err(|error| format!("Falha ao aguardar terminal: {error}"))? {
            status = Some(result);
            break;
        }
        thread::sleep(std::time::Duration::from_millis(100));
    }
    let result = if let Some(status) = status {
        status
    } else {
        let _ = process.child.kill();
        process.child.wait().map_err(|error| format!("Falha ao finalizar terminal: {error}"))?
    };
    process.exited = true;
    inner.last_exit_code = Some(result.code().unwrap_or(-1));
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(RuntimeState::default())
        .manage(TerminalState::default())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            read_project_entries,
            search_project_files,
            read_project_file,
            write_project_file,
            create_project_file,
            create_project_directory,
            run_project_file,
            quality_project_file,
            format_project_file,
            start_runtime,
            runtime_diagnostics,
            test_ollama_connection,
            switch_runtime_workspace,
            send_runtime_command,
            stop_runtime,
            git_status,
            git_diff,
            git_stage,
            git_unstage,
            git_commit,
            start_integrated_terminal,
            terminal_diagnostics,
            write_integrated_terminal,
            stop_integrated_terminal
        ])
        .run(tauri::generate_context!())
        .expect("failed to run KardecAgent Studio");
}


#[cfg(test)]
mod terminal_io_tests {
    use super::read_bounded_line;
    use std::io::{BufReader, Cursor, ErrorKind};

    #[test]
    fn bounded_terminal_line_discards_overflow_and_recovers_at_next_line() {
        let input = b"123456789\\nok\\n";
        let mut reader = BufReader::new(Cursor::new(input));
        let error = read_bounded_line(&mut reader, 4).expect_err("oversized line must be rejected");
        assert_eq!(error.kind(), ErrorKind::InvalidData);
        assert_eq!(read_bounded_line(&mut reader, 4).unwrap(), Some(b"ok".to_vec()));
        assert_eq!(read_bounded_line(&mut reader, 4).unwrap(), None);
    }

    #[test]
    fn bounded_terminal_line_preserves_final_line_without_newline() {
        let mut reader = BufReader::new(Cursor::new(b"last line"));
        assert_eq!(read_bounded_line(&mut reader, 32).unwrap(), Some(b"last line".to_vec()));
        assert_eq!(read_bounded_line(&mut reader, 32).unwrap(), None);
    }
}
