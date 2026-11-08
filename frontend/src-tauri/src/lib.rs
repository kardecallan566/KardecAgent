use serde::Serialize;
use std::{fs, path::Path};

#[derive(Serialize)]
struct ProjectEntry {
    name: String,
    path: String,
    is_dir: bool,
}

#[tauri::command]
fn read_project_entries(project_path: String) -> Result<Vec<ProjectEntry>, String> {
    let root = Path::new(&project_path)
        .canonicalize()
        .map_err(|e| format!("Não foi possível acessar o projeto: {e}"))?;
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
fn read_project_file(file_path: String) -> Result<String, String> {
    let path = Path::new(&file_path).canonicalize()
        .map_err(|e| format!("Não foi possível abrir o arquivo: {e}"))?;
    if !path.is_file() { return Err("O caminho selecionado não é um arquivo.".into()); }
    let metadata = fs::metadata(&path).map_err(|e| e.to_string())?;
    if metadata.len() > 1_000_000 {
        return Err("Arquivo maior que 1 MB: visualização bloqueada para preservar recursos.".into());
    }
    fs::read_to_string(path).map_err(|e| format!("Arquivo não é texto UTF-8 ou falha de leitura: {e}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![read_project_entries, read_project_file])
        .run(tauri::generate_context!())
        .expect("failed to run KardecAgent Studio");
}
