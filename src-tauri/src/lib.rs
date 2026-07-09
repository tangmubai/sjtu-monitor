use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::HashMap,
    env,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
};
use tauri::{AppHandle, Emitter, Manager, State};

#[derive(Default)]
struct ProcessStore {
    children: Mutex<HashMap<String, Child>>,
}

#[derive(Clone, Serialize)]
struct ProcessEvent {
    label: String,
    line: String,
}

#[derive(Clone, Serialize)]
struct ProcessExit {
    label: String,
    code: Option<i32>,
}

#[derive(Deserialize)]
struct JsonPayload {
    payload: Value,
}

#[derive(Deserialize)]
struct AutoSwapPayload {
    enabled: bool,
    dry_run: bool,
}

#[derive(Deserialize)]
struct StartProcessPayload {
    label: String,
    script: String,
    args: Vec<String>,
    debug: bool,
}

fn repo_root(app: &AppHandle) -> Result<PathBuf, String> {
    let mut candidates = Vec::new();
    if let Ok(configured) = env::var("SJTU_MONITOR_ROOT") {
        if !configured.trim().is_empty() {
            candidates.push(PathBuf::from(configured));
        }
    }
    if let Ok(current) = env::current_dir() {
        candidates.extend(current.ancestors().map(Path::to_path_buf));
    }
    candidates.push(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")))
            .to_path_buf(),
    );
    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.extend(resource_dir.ancestors().map(Path::to_path_buf));
    }

    candidates
        .into_iter()
        .find(|candidate| candidate.join("gui_backend.py").is_file())
        .ok_or_else(|| {
            "cannot find gui_backend.py; set SJTU_MONITOR_ROOT to the repository root".to_string()
        })
}

fn python_command(root: &Path) -> (String, Vec<String>) {
    if let Ok(value) = env::var("SJTU_MONITOR_PYTHON") {
        if !value.trim().is_empty() {
            return (value, Vec::new());
        }
    }
    let conda_bat = PathBuf::from(r"C:\Users\TMB07\miniconda3\condabin\conda.bat");
    if conda_bat.exists() {
        return (
            conda_bat.to_string_lossy().to_string(),
            vec!["run".into(), "-n".into(), "sjtu-monitor".into(), "python".into()],
        );
    }
    let local = root.join(".venv").join("Scripts").join("python.exe");
    if local.exists() {
        return (local.to_string_lossy().to_string(), Vec::new());
    }
    ("python".into(), Vec::new())
}

fn run_backend(app: &AppHandle, command: &str, payload: Option<Value>) -> Result<Value, String> {
    let root = repo_root(app)?;
    let (program, mut args) = python_command(&root);
    args.push(root.join("gui_backend.py").to_string_lossy().to_string());
    args.push(command.to_string());
    let mut child = Command::new(program)
        .args(args)
        .current_dir(root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1")
        .stdin(if payload.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("failed to start Python backend: {err}"))?;

    if let Some(data) = payload {
        let mut stdin = child
            .stdin
            .take()
            .ok_or_else(|| "backend stdin unavailable".to_string())?;
        stdin
            .write_all(data.to_string().as_bytes())
            .map_err(|err| format!("failed to write backend payload: {err}"))?;
    }

    let output = child
        .wait_with_output()
        .map_err(|err| format!("failed to read backend output: {err}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        if !stdout.is_empty() {
            if let Ok(value) = serde_json::from_str::<Value>(&stdout) {
                return Err(value["error"].as_str().unwrap_or(&stdout).to_string());
            }
        }
        return Err(format!("backend failed: {}", stderr.trim()));
    }
    serde_json::from_str(&stdout).map_err(|err| format!("invalid backend JSON: {err}; {stdout}"))
}

#[tauri::command]
fn load_snapshot(app: AppHandle) -> Result<Value, String> {
    run_backend(&app, "snapshot", None)
}

#[tauri::command]
fn save_settings(app: AppHandle, input: JsonPayload) -> Result<Value, String> {
    run_backend(&app, "save-settings", Some(input.payload))
}

#[tauri::command]
fn save_groups(app: AppHandle, input: JsonPayload) -> Result<Value, String> {
    run_backend(&app, "save-groups", Some(input.payload))
}

#[tauri::command]
fn set_auto_swap(app: AppHandle, input: AutoSwapPayload) -> Result<Value, String> {
    run_backend(
        &app,
        "set-auto-swap",
        Some(serde_json::json!({
            "enabled": input.enabled,
            "dry_run": input.dry_run
        })),
    )
}

#[tauri::command]
fn start_process(
    app: AppHandle,
    store: State<'_, Arc<ProcessStore>>,
    input: StartProcessPayload,
) -> Result<(), String> {
    let mut children = store
        .children
        .lock()
        .map_err(|_| "process store poisoned".to_string())?;
    if children.contains_key(&input.label) {
        return Err(format!("{} is already running", input.label));
    }

    let root = repo_root(&app)?;
    let (program, mut args) = python_command(&root);
    args.push(root.join(&input.script).to_string_lossy().to_string());
    args.extend(input.args);
    if input.debug {
        args.push("--debug".to_string());
    }

    let mut child = Command::new(program)
        .args(args)
        .current_dir(root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|err| format!("failed to start {}: {err}", input.label))?;

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let label = input.label.clone();
    let app_for_stdout = app.clone();
    if let Some(stream) = stdout {
        let label_for_thread = label.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stream);
            for line in reader.lines().map_while(Result::ok) {
                let _ = app_for_stdout.emit(
                    "process-output",
                    ProcessEvent {
                        label: label_for_thread.clone(),
                        line,
                    },
                );
            }
        });
    }
    let app_for_stderr = app.clone();
    if let Some(stream) = stderr {
        let label_for_thread = label.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stream);
            for line in reader.lines().map_while(Result::ok) {
                let _ = app_for_stderr.emit(
                    "process-output",
                    ProcessEvent {
                        label: label_for_thread.clone(),
                        line,
                    },
                );
            }
        });
    }
    children.insert(label, child);
    Ok(())
}

#[tauri::command]
fn stop_process(store: State<'_, Arc<ProcessStore>>, label: String) -> Result<(), String> {
    let mut children = store
        .children
        .lock()
        .map_err(|_| "process store poisoned".to_string())?;
    if let Some(mut child) = children.remove(&label) {
        child
            .kill()
            .map_err(|err| format!("failed to stop {label}: {err}"))?;
        let _ = child.wait();
    }
    Ok(())
}

#[tauri::command]
fn poll_processes(app: AppHandle, store: State<'_, Arc<ProcessStore>>) -> Result<Vec<String>, String> {
    let mut finished = Vec::new();
    let mut children = store
        .children
        .lock()
        .map_err(|_| "process store poisoned".to_string())?;
    let labels: Vec<String> = children.keys().cloned().collect();
    for label in labels {
        let Some(child) = children.get_mut(&label) else {
            continue;
        };
        if let Some(status) = child
            .try_wait()
            .map_err(|err| format!("failed to poll {label}: {err}"))?
        {
            finished.push(label.clone());
            let _ = app.emit(
                "process-exit",
                ProcessExit {
                    label: label.clone(),
                    code: status.code(),
                },
            );
        }
    }
    for label in &finished {
        children.remove(label);
    }
    Ok(children.keys().cloned().collect())
}

pub fn run() {
    tauri::Builder::default()
        .manage(Arc::new(ProcessStore::default()))
        .invoke_handler(tauri::generate_handler![
            load_snapshot,
            save_settings,
            save_groups,
            set_auto_swap,
            start_process,
            stop_process,
            poll_processes
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
