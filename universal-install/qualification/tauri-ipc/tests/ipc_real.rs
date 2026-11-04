use iwxa_program_native_port::{CliPort, CONTRACT};
use iwxa_program_native_port::tauri_host::iwxa_program_connection;
use serde_json::{json, Value};
use std::{env,fs,path::PathBuf,sync::Mutex};
use tauri::{WebviewWindowBuilder, ipc::{CallbackFn,InvokeBody}, webview::InvokeRequest};

fn ipc(webview: &tauri::WebviewWindow<tauri::test::MockRuntime>, envelope: Value) -> Value {
    let response = tauri::test::get_ipc_response(
        webview,
        InvokeRequest {
            cmd: "iwxa_program_connection".into(),
            callback: CallbackFn(0),
            error: CallbackFn(1),
            url: "http://tauri.localhost".parse().expect("test URL"),
            body: InvokeBody::Json(json!({"envelope":envelope})),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.to_string(),
        }
    ).expect("Tauri command IPC returned an error");
    response.deserialize::<Value>().expect("valid JSON IPC response")
}

#[test]
fn real_tauri_ipc_runs_host_selected_jq() {
    let binary = PathBuf::from(env::var("IWXA_CLI_EXECUTABLE")
        .expect("qualification requires a real pinned executable"));
    let catalog: Value = serde_json::from_slice(&fs::read(
        env::var("IWXA_CLI_CATALOG").expect("qualification catalog"))).unwrap();
    let spec = catalog["programs"].as_array().unwrap().iter()
        .find(|p| p["program_id"]=="JQ_CLI_V1").expect("official jq manifest");
    let port = CliPort::from_manifest(spec,binary).expect("valid host-owned port");

    let app = tauri::test::mock_builder()
        .manage(Mutex::new(port))
        .invoke_handler(tauri::generate_handler![iwxa_program_connection])
        .build(tauri::test::mock_context(tauri::test::noop_assets()))
        .expect("build native Tauri command harness");
    let webview = WebviewWindowBuilder::new(&app,"main",Default::default())
        .build().expect("create Tauri test webview");

    let envelope = |operation: &str, request: Value| json!({
        "schema":CONTRACT,"operation":operation,"request":request
    });
    assert_eq!(ipc(&webview,envelope("inspect",json!({})) )["status"],"DISCOVERED");
    assert_eq!(ipc(&webview,envelope("connect",json!({})) )["status"],"CONNECTED");
    let caps = ipc(&webview,envelope("capabilities",json!({})));
    assert_eq!(caps["operations"][0]["verified"],false);

    let result = ipc(&webview,envelope("invoke",
        json!({"operation":"evaluate","params":{"expression":"6 * 7"}})));
    assert_eq!(result["status"],"EXECUTED");
    assert_eq!(result["output"].as_str().unwrap().trim(),"42");
    assert_eq!(ipc(&webview,envelope("capabilities",json!({})))
        ["operations"][0]["verified"],true);

    assert_eq!(ipc(&webview,envelope("invoke",
        json!({"operation":"unsupported"})))["status"],"OPERATION_UNSUPPORTED");
    assert_eq!(ipc(&webview,json!({"schema":"INVALID","operation":"invoke","request":{}}))
        ["status"],"PORT_SCHEMA_INVALID");
    assert_eq!(ipc(&webview,envelope("disconnect",json!({})))["status"],"DISCONNECTED");
    assert_eq!(ipc(&webview,envelope("invoke",
        json!({"operation":"evaluate","params":{"expression":"6 * 7"}})))
        ["status"],"NOT_CONNECTED");
    assert_eq!(ipc(&webview,envelope("recover",json!({})))["status"],"CONNECTED");
    println!("{}",json!({"schema":"IWXA_TAURI_NATIVE_IPC/1",
        "status":"PASS","program":"jq-1.8.1","transport":"tauri-native-ipc",
        "real_binary":true,"mock_runtime":true,"installed_product_gui":false,
        "no_python_core":true,"no_daemon":true}));
}
