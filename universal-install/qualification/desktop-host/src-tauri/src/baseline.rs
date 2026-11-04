#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
// Isolated visual baseline. Same Tauri configuration, window, WebView2 and
// frontend assets as the real host, but no CliPort state or native IPC handler.
// For measurement only: not a substitute for the installed GUI acceptance.
fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("Run the Tauri/WebView2 baseline without Program Connection");
}
