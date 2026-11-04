//! Optional real Tauri v2 command. Host retains state/approval/permissions.
use crate::{CliPort, CONTRACT};
use serde_json::{json, Value};
use std::sync::Mutex;
use tauri::State;

/// Existing product manages the selected port via .manage(Mutex::new(port)).
/// Only approved operations in that port's host-selected manifest are executed.
#[tauri::command(async)]
pub fn iwxa_program_connection(
    envelope: Value,
    port: State<'_, Mutex<CliPort>>,
) -> Value {
    match port.lock() {
        Ok(mut connection) => connection.dispatch(&envelope),
        Err(_) => json!({
            "schema": CONTRACT, "transport": "cli",
            "status": "PORT_STATE_UNAVAILABLE", "executed": false
        }),
    }
}
