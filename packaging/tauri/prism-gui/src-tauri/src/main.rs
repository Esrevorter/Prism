// Prism Wallet — Tauri installer shell (spec §13 "Double-click installer mandate").
//
// The end user double-clicks this app's icon; nothing else. We spawn the
// PyInstaller-frozen `prism-gui` sidecar (embedded CPython + JSON-RPC daemon
// + GUI static bundle) as a hidden background process, wait for its
// health-confirmed status line, and load the wallet UI in our own webview.
// No terminal ever appears on any platform (console=False in prism_gui.spec,
// windows_subsystem="windows" here).
//
// Sidecar placement: packaging/build_tauri.sh copies dist/prism-gui(.exe) to
// src-tauri/binaries/prism-gui-<rust-target-triple> before `tauri build`.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use tauri::api::process::{CommandChild, CommandEvent};
use tauri::{Manager, State};

/// Shared handle to the running daemon so we can kill it on app exit
/// (acceptance criterion (c): quit/uninstall leaves no orphan daemons).
/// `Command::spawn` hands back a `CommandChild` (Tauri's wrapper around the
/// OS child process) — not a `std::process::Child`.
struct Daemon(Mutex<Option<CommandChild>>);

/// Wallet endpoint discovered from the sidecar's --json-status line.
/// Empty until the daemon reports healthy.
struct WalletUrl(Mutex<String>);

/// Spawn (or reuse) the bundled prism-gui daemon. Returns once the daemon
/// has confirmed /health itself (the launcher only prints the status JSON
/// after its internal health gate passes — never a race).
#[tauri::command]
fn start_daemon(
    app: tauri::AppHandle,
    daemon: State<Daemon>,
    wallet: State<WalletUrl>,
) -> Result<String, String> {
    // Already running? Reuse.
    {
        let cur = wallet.0.lock().unwrap();
        if !cur.is_empty() {
            return Ok(cur.clone());
        }
    }

    let (mut rx, child) = tauri::api::process::Command::new_sidecar("prism-gui")
        .map_err(|e| format!("sidecar lookup failed: {e}"))?
        .args(["--no-browser", "--json-status"])
        .spawn()
        .map_err(|e| format!("sidecar spawn failed: {e}"))?;

    // Consume stdout until the JSON status line arrives (cold onefile unpack
    // can take ~15s; give it generous patience via the event stream).
    // `rx` is a tokio mpsc Receiver, so `recv()` is async — block_on it.
    let mut url = String::new();
    while let Some(event) = tauri::async_runtime::block_on(rx.recv()) {
        match event {
            CommandEvent::Stdout(line) => {
                let trimmed = line.trim_start();
                if trimmed.starts_with('{') && trimmed.contains("\"url\"") {
                    if let Ok(v) = serde_json::from_str::<serde_json::Value>(trimmed) {
                        if let Some(u) = v.get("url").and_then(|x| x.as_str()) {
                            url = u.to_string();
                            break;
                        }
                    }
                }
            }
            CommandEvent::Error(e) => {
                let _ = child.kill();
                return Err(format!("daemon error: {e}"));
            }
            CommandEvent::Terminated(payload) => {
                return Err(format!("daemon exited early with code {:?}", payload.code))
            }
            _ => {}
        }
    }
    if url.is_empty() {
        let _ = child.kill();
        return Err("daemon did not report a wallet URL in time".into());
    }

    *wallet.0.lock().unwrap() = url.clone();
    *daemon.0.lock().unwrap() = Some(child);

    // Point the main window at the live wallet UI. Tauri v1 has no
    // `Window::navigate` (that's a v2 API), so drive location.replace from
    // inside the webview via an injected script on the window itself
    // (`AppHandle` has no `eval`; `Window` does).
    if let Some(w) = app.get_window("main") {
        let js = format!(
            "window.location.replace({});",
            serde_json::to_string(&url).map_err(|e| e.to_string())?
        );
        let _ = w.eval(&js);
    }
    Ok(url)
}

/// Frontend helper: current wallet endpoint (empty string until ready).
/// Also used by the boot page as a polling fallback in case the injected
/// navigation script races the page load.
#[tauri::command]
fn wallet_url(wallet: State<WalletUrl>) -> String {
    wallet.0.lock().unwrap().clone()
}

fn main() {
    tauri::Builder::default()
        .manage(Daemon(Mutex::new(None)))
        .manage(WalletUrl(Mutex::new(String::new())))
        .invoke_handler(tauri::generate_handler![start_daemon, wallet_url])
        .build(tauri::generate_context!())
        .expect("error while building Prism Wallet shell")
        .run(|app, event| {
            // Criterion (c): killing the app must leave no orphan daemon.
            if let tauri::RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app.try_state::<Daemon>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
