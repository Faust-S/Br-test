#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
// Reuse the installed Tauri qualification host: U01 CLI and U04 UI are independent.
use iwxa_program_native_port::{CliPort, CONTRACT};
use serde_json::{json, Value};
use std::{env, fs, path::PathBuf, sync::Mutex, time::Instant};
use tauri::State;

#[path = "../../../../../u04-presentation/native/windows_port.rs"]
mod windows_port;

#[tauri::command]
fn iwxa_u04_presentation(operation: String,
    port: State<'_, Mutex<windows_port::Port>>) -> Result<Value, String> {
    let mut p = port.lock().map_err(|_| "PRESENTATION_PORT_LOCK_UNAVAILABLE")?;
    let response = p.handle(&operation);
    if operation != "capabilities" && operation != "getBinding" {
        if let Some(path) = env::var_os("IWXA_U04_RECEIPT").map(PathBuf::from) {
            if !path.is_absolute() {return Err("U04_RECEIPT_PATH_INVALID".into());}
            let record = json!({"schema":"IWXA_U04_GUI_RECEIPT/1","host_pid":std::process::id(),
                "operation":operation,"response":response});
            fs::write(path, serde_json::to_vec(&record).map_err(|e| e.to_string())?)
                .map_err(|e| format!("U04_RECEIPT_WRITE_FAILED: {e}"))?;
        }
    }
    Ok(response)
}

#[tauri::command]
fn qualification_evidence(port: State<'_, Mutex<CliPort>>) -> Result<Value, String> {
    let mut connection = port.lock().map_err(|_| "HOST_LOCK_UNAVAILABLE")?;
    let caps = connection.capabilities();
    if caps["operations"][0]["verified"] != true {
        return Err("CAPABILITY_NOT_VERIFIED".into());
    }
    let start = Instant::now();
    let proof = connection.invoke("evaluate", &json!({"expression":"6 * 7"}));
    if proof["status"] != "EXECUTED" ||
        proof["output"].as_str().unwrap_or("").trim() != "42" {
        return Err("NATIVE_EXECUTION_NOT_PROVED".into());
    }
    let evidence = json!({
        "schema":"IWXA_INSTALLED_TAURI_GUI/1",
        "status":"PASS","pid":std::process::id(),
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
    let mut builder = tauri::Builder::default()
        .manage(Mutex::new(windows_port::Port::from_env()));
    if env::var_os("IWXA_U04_QUALIFICATION").is_none() {
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
        builder = builder.manage(Mutex::new(port));
    }
    builder.invoke_handler(tauri::generate_handler![
        iwxa_program_native_port::tauri_host::iwxa_program_connection,
        qualification_evidence,iwxa_u04_presentation
    ])
    .run(tauri::generate_context!())
    .expect("Run real installed Tauri GUI host");
}
