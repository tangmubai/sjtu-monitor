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

/// Suppress the console window for spawned console helpers (Python) on Windows
/// via CREATE_NO_WINDOW. No-op on other platforms.
fn hide_console_window(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    {
        let _ = command;
    }
}

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
    let local = root.join(".venv").join("Scripts").join("python.exe");
    if local.exists() {
        return (local.to_string_lossy().to_string(), Vec::new());
    }
    ("python".into(), Vec::new())
}

/// 打包后随应用一起分发的独立 Python 后端(PyInstaller onedir),文件名 sjtu-backend[.exe]。
fn locate_sidecar(app: &AppHandle) -> Option<PathBuf> {
    let name = if cfg!(windows) {
        "sjtu-backend.exe"
    } else {
        "sjtu-backend"
    };
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        candidates.push(res.join("sjtu-backend").join(name));
        candidates.push(res.join("resources").join("sjtu-backend").join(name));
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("sjtu-backend").join(name));
            candidates.push(dir.join("resources").join("sjtu-backend").join(name));
        }
    }
    candidates.into_iter().find(|p| p.is_file())
}

/// 后端调用方式:优先用打包 sidecar(独立发行版);找不到时回退到 `python <脚本>`(源码/开发模式)。
struct Backend {
    program: String,
    prefix: Vec<String>,
    bundled: bool,
    root: PathBuf,
    data_dir: PathBuf,
}

impl Backend {
    /// 构造 `program` 之后、命令参数之前的基础参数(含脚本标识)。
    fn base_args(&self, script: &str) -> Vec<String> {
        let mut args = self.prefix.clone();
        if self.bundled {
            // sidecar 按脚本名(gui_backend.py / monitor.py / bootstrap.py)自行分发
            args.push(script.to_string());
        } else {
            args.push(self.root.join(script).to_string_lossy().to_string());
        }
        args
    }
}

fn resolve_backend(app: &AppHandle) -> Result<Backend, String> {
    // 可写数据目录:装机后不能写安装目录,统一用平台 app-data 目录。
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|err| format!("cannot resolve app data dir: {err}"))?;
    let _ = std::fs::create_dir_all(&data_dir);

    // 仅发行模式使用打包 sidecar;开发模式下 target/debug 里可能残留旧构建产物,
    // 必须走 SJTU_MONITOR_PYTHON 指定的源码后端(需要验证 sidecar 时可设 SJTU_MONITOR_RELEASE=1)。
    if release_mode() {
        if let Some(sidecar) = locate_sidecar(app) {
            return Ok(Backend {
                program: sidecar.to_string_lossy().to_string(),
                prefix: Vec::new(),
                bundled: true,
                root: data_dir.clone(),
                data_dir,
            });
        }
    }

    // 开发/源码模式:仓库内有 gui_backend.py,用 python 直接跑;数据仍写仓库根(行为不变)。
    let root = repo_root(app)?;
    let (program, prefix) = python_command(&root);
    Ok(Backend {
        program,
        prefix,
        bundled: false,
        data_dir: root.clone(),
        root,
    })
}

fn release_mode() -> bool {
    !cfg!(debug_assertions) || env::var("SJTU_MONITOR_RELEASE").ok().as_deref() == Some("1")
}

fn decode_utf8(bytes: Vec<u8>, context: &str) -> Result<String, String> {
    String::from_utf8(bytes)
        .map_err(|err| format!("{context} produced non-UTF-8 output: {err}"))
}

fn forward_process_stream<R>(stream: R, app: AppHandle, label: String)
where
    R: std::io::Read + Send + 'static,
{
    thread::spawn(move || {
        let mut reader = BufReader::new(stream);
        let mut bytes = Vec::new();
        loop {
            bytes.clear();
            match reader.read_until(b'\n', &mut bytes) {
                Ok(0) => break,
                Ok(_) => {
                    while matches!(bytes.last(), Some(b'\n' | b'\r')) {
                        bytes.pop();
                    }
                    let line = decode_utf8(bytes.clone(), "backend stream").unwrap_or_else(|err| {
                        format!("[编码错误] {err}")
                    });
                    let _ = app.emit(
                        "process-output",
                        ProcessEvent {
                            label: label.clone(),
                            line,
                        },
                    );
                }
                Err(err) => {
                    let _ = app.emit(
                        "process-output",
                        ProcessEvent {
                            label: label.clone(),
                            line: format!("[输出读取失败] {err}"),
                        },
                    );
                    break;
                }
            }
        }
    });
}

fn run_backend(app: &AppHandle, command: &str, payload: Option<Value>) -> Result<Value, String> {
    let backend = resolve_backend(app)?;
    let mut args = backend.base_args("gui_backend.py");
    args.push(command.to_string());
    let mut cmd = Command::new(&backend.program);
    cmd.args(args)
        .current_dir(&backend.data_dir)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8:strict")
        .env("PYTHONUNBUFFERED", "1")
        .env("SJTU_MONITOR_DATA_DIR", &backend.data_dir)
        .env("SJTU_MONITOR_RELEASE", if release_mode() { "1" } else { "0" })
        .stdin(if payload.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console_window(&mut cmd);
    let mut child = cmd
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
    let stdout = decode_utf8(output.stdout, "backend stdout")?.trim().to_string();
    if !output.status.success() {
        let stderr = decode_utf8(output.stderr, "backend stderr")?;
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
fn complete_onboarding(app: AppHandle) -> Result<Value, String> {
    run_backend(&app, "complete-onboarding", None)
}

#[tauri::command]
fn test_email(app: AppHandle) -> Result<Value, String> {
    run_backend(&app, "test-email", None)
}

#[tauri::command]
fn set_auto_swap(app: AppHandle, input: AutoSwapPayload) -> Result<Value, String> {
    let dry_run = if release_mode() { false } else { input.dry_run };
    run_backend(
        &app,
        "set-auto-swap",
        Some(serde_json::json!({
            "enabled": input.enabled,
            "dry_run": dry_run
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

    let backend = resolve_backend(&app)?;
    let mut args = backend.base_args(&input.script);
    args.extend(input.args);
    if input.debug && !release_mode() {
        args.push("--debug".to_string());
    }

    let mut cmd = Command::new(&backend.program);
    cmd.args(args)
        .current_dir(&backend.data_dir)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8:strict")
        .env("PYTHONUNBUFFERED", "1")
        .env("SJTU_MONITOR_DATA_DIR", &backend.data_dir)
        .env("SJTU_MONITOR_RELEASE", if release_mode() { "1" } else { "0" })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console_window(&mut cmd);
    let mut child = cmd
        .spawn()
        .map_err(|err| format!("failed to start {}: {err}", input.label))?;

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let label = input.label.clone();
    if let Some(stream) = stdout {
        forward_process_stream(stream, app.clone(), label.clone());
    }
    if let Some(stream) = stderr {
        forward_process_stream(stream, app.clone(), label.clone());
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
            complete_onboarding,
            set_auto_swap,
            test_email,
            start_process,
            stop_process,
            poll_processes
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::decode_utf8;

    #[test]
    fn decodes_chinese_backend_output_as_utf8() {
        let text = decode_utf8("中文编码验证：交我选".as_bytes().to_vec(), "test").unwrap();
        assert_eq!(text, "中文编码验证：交我选");
    }

    #[test]
    fn rejects_non_utf8_backend_output() {
        assert!(decode_utf8(vec![0xd6, 0xd0, 0xce, 0xc4], "test").is_err());
    }
}
