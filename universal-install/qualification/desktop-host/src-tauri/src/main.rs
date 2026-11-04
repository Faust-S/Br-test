#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
// Public test-only Tauri host: one window, one real CLI connector, no product Core.
use iwxa_program_native_port::{CliPort, CONTRACT};
use serde_json::{json, Value};
use std::{env, fs, path::PathBuf, sync::Mutex, time::Instant};
use tauri::State;

#[tauri::command]
fn qualification_evidence(port: State<'_, Mutex<CliPort>>) -> Result<Value, String> {
    let mut connection = port.lock().map_err(|_| "HOST_LOCK_UNAVAILABLE")?;
    let caps = connection.capabilities();
    if caps["operations"][0]["verified"] != true {
        return Err("CAPABILITY_NOT_VERIFIED".into());
    }
    // Independent native readback: the real program must work in this process.
    let start = Instant::now();
    let proof = connection.invoke("evaluate", &json!({"expression":"6 * 7"}));
    if proof["status"] != "EXECUTED" ||
        proof["output"].as_str().unwrap_or("").trim() != "42" {
        return Err("NATIVE_EXECUTION_NOT_PROVED".into());
    }
    let evidence = json!({
        "schema":"IWXA_INSTALLED_TAURI_GUI/1",
        "status":"PASS", "pid":std::process::id(),
        "native_contract":CONTRACT,
        "real_cli_output":"42",
        "invoke_ms":start.elapsed().as_secs_f64()*1000.0,
        "transport":"WebView2-TauriIPC-Rust-CLI",
        "unverified_backend_claim":false
    });
    let path = PathBuf::from(env::var_os("IWXA_GUI_RECEIPT")
        .ok_or("RECEIPT_PATH_UNAVAILABLE")?);
    if !path.is_absolute() {
        return Err("RECEIPT_PATH_NOT_ABSOLUTE".into());
    }
    fs::write(&path,serde_json::to_vec(&evidence).map_err(|e| e.to_string())?)
        .map_err(|e| format!("WRITE_RECEIPT_FAILED: {e}"))?;
    Ok(evidence)
}

fn main() {
    let binary = PathBuf::from(env::var_os("IWXA_CLI_EXECUTABLE")
        .expect("Test host requires host-selected pinned jq binary"));
    let manifest = PathBuf::from(env::var_os("IWXA_CLI_CATALOG")
        .expect("Test host requires approved connector catalog"));
    let data: Value = serde_json::from_slice(&fs::read(manifest)
        .expect("Read public approved connector catalog"))
        .expect("Parse public connector catalog");
    let spec = data["programs"].as_array().expect("Expected approved list")
        .iter().find(|p| p["program_id"]=="JQ_CLI_V1")
        .expect("Test jq connector must be declared");
    let port = CliPort::from_manifest(spec,binary).expect("Valid host-owned CLI port");
    tauri::Builder::default()
        .manage(Mutex::new(port))
        .invoke_handler(tauri::generate_handler![
            iwxa_program_native_port::tauri_host::iwxa_program_connection,
            qualification_evidence
        ])
        .run(tauri::generate_context!())
        .expect("Run real installed Tauri GUI host");
}
