use iwxa_program_native_port::{CliPort, CONTRACT};
use serde_json::{json, Value};
use std::fs;
use std::path::PathBuf;

fn status(value: &Value) -> &str { value["status"].as_str().unwrap_or("") }
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 4 { return Err("EXPECTED_CATALOG_ID_EXECUTABLE".into()); }
    let catalog: Value = serde_json::from_slice(&fs::read(&args[1])?)?;
    let spec = catalog["programs"].as_array().ok_or("CATALOG_NOT_ARRAY")?
        .iter().find(|v| v["program_id"] == args[2]).ok_or("PROGRAM_NOT_FOUND")?;
    let file = PathBuf::from(&args[3]);
    let mut port = CliPort::from_manifest(spec, file.clone())?;
    if status(&port.inspect()) != "DISCOVERED" { return Err("RUST_DISCOVERY_FAILED".into()); }
    if status(&port.invoke("evaluate", &json!({"expression":"6 * 7"}))) != "NOT_CONNECTED" {
        return Err("RUST_INVOKE_WITHOUT_CONNECTION".into());
    }
    if status(&port.connect()) != "CONNECTED" { return Err("RUST_CONNECT_FAILED".into()); }
    if port.capabilities()["operations"][0]["verified"] != false {
        return Err("RUST_FALSE_CAPABILITY_PROOF".into());
    }
    let wrong = port.invoke("evaluate", &json!({"not_declarated":"7 * 8"}));
    if status(&wrong) != "CLI_INPUT_INVALID" { return Err("RUST_INPUT_NOT_REJECTED".into()); }
    let envelope = |expression: &str| json!({
        "schema": CONTRACT, "operation":"invoke",
        "request":{"operation":"evaluate","params":{"expression":expression}}
    });
    let result = port.dispatch(&envelope("6 * 7"));
    if status(&result) != "EXECUTED" ||
       result["output"].as_str().unwrap_or("").trim() != "42" {
        return Err("RUST_EXECUTION_FAILED".into());
    }
    if port.dispatch(&envelope("7 * 8"))["output"].as_str().unwrap_or("").trim() != "56" {
        return Err("RUST_DYNAMIC_INPUT_FAILED".into());
    }
    if port.capabilities()["operations"][0]["verified"] != true {
        return Err("RUST_CAPABILITY_NOT_VERIFIED".into());
    }
    if status(&port.invoke("undefined", &Value::Null)) != "OPERATION_UNSUPPORTED" {
        return Err("RUST_UNDECLARED_OPERATION".into());
    }
    if status(&port.disconnect()) != "DISCONNECTED" ||
       status(&port.invoke("evaluate", &Value::Null)) != "NOT_CONNECTED" {
        return Err("RUST_DISCONNECT_FAILED".into());
    }
    if status(&port.recover()) != "CONNECTED" ||
       status(&port.verify()) != "CONNECTED" {
        return Err("RUST_RECOVER_FAILED".into());
    }
    port.disconnect();
    let mut wrong_spec = spec.clone();
    wrong_spec["version_contains"] = Value::from("impossible-999.999");
    let mut negative = CliPort::from_manifest(&wrong_spec,file)?;
    if status(&negative.connect()) != "VERSION_UNVERIFIED" {
        return Err("RUST_BAD_VERSION_ACCEPTED".into());
    }
    println!("{}",json!({
        "schema":"IWXA_RUST_NATIVE_HOST_QUALIFICATION/1",
        "status":"PASS", "program_id":args[2],
        "real_cli":true, "value":"42",
        "reconnected":true,"python_runtime_required":false,
        "node_runtime_required":false,"additional_daemon":false
    }));
    Ok(())
}
