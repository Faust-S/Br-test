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
    let broken = port.invoke("evaluate", &json!({"expression":"("}));
    if status(&broken) != "EXECUTION_FAILED" || broken["executionOutcome"] != "UNKNOWN" ||
       port.capabilities()["operations"][0]["verified"] != false {
        return Err("RUST_FAILED_EXECUTION_RETAINED_PROOF".into());
    }
    let restored = port.invoke("evaluate", &json!({"expression":"6 * 7"}));
    if status(&restored) != "EXECUTED" || restored["executionOutcome"] != "ACKNOWLEDGED" ||
       port.capabilities()["operations"][0]["verified"] != true {
        return Err("RUST_EXECUTION_PROOF_NOT_RESTORED".into());
    }
    // The same real jq/yq executable must support opt-in bounded, larger
    // documents through the native port, without replacing the default cap.
    for bad in [json!(true),json!(0),json!(1023),json!(4194305),json!("524288")] {
        let mut rejected = spec.clone();
        rejected["max_output_bytes"] = bad;
        if CliPort::from_manifest(&rejected, file.clone()).is_ok() {
            return Err("RUST_OUTPUT_LIMIT_UNVALIDATED".into());
        }
    }
    let query = if args[2]=="JQ_CLI_V1" {"query_json"} else {"query_yaml"};
    let suffix = if args[2]=="JQ_CLI_V1" {"json"} else {"yaml"};
    let big_file = std::env::temp_dir().join(format!(
        "iwxa-rust-big-{}-{}.{}",std::process::id(),
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?
            .as_nanos(),suffix));
    let large_check = (|| -> Result<usize,Box<dyn std::error::Error>> {
        if args[2]=="JQ_CLI_V1" {
            let rows = (0..4000).map(|i| json!({"id":i,
                "name":format!("Номенклатура склада № {i:04}"),
                "quantity":i%9})).collect::<Vec<_>>();
            fs::write(&big_file,serde_json::to_vec(&json!({"rows":rows}))?)?;
        } else {
            let mut document=String::from("rows:\n");
            for i in 0..4000 {
                document.push_str(&format!("  - id: {i}\n    name: \"Номенклатура склада № {i:04}\"\n    quantity: {}\n",i%9));
            }
            fs::write(&big_file,document)?;
        }
        let request=json!({"expression":".","input":big_file.display().to_string()});
        let mut capped=spec.clone();
        capped["max_output_bytes"]=json!(131072);
        let mut old=CliPort::from_manifest(&capped,file.clone())?;
        if status(&old.connect())!="CONNECTED" ||
           status(&old.invoke(query,&request))!="CLI_OUTPUT_TOO_LARGE" {
            return Err("RUST_DEFAULT_CAP_LARGE_OUTPUT_FALSE_SUCCESS".into());
        }
        let accepted=port.invoke(query,&request);
        if status(&accepted)!="EXECUTED" ||
           accepted["executionOutcome"]!="ACKNOWLEDGED" {
            return Err("RUST_LARGE_DOCUMENT_EXECUTION_FAILED".into());
        }
        let output=accepted["output"].as_str().ok_or("RUST_LARGE_DOCUMENT_NO_OUTPUT")?;
        if !(131072..1048576).contains(&output.len()) {
            return Err("RUST_LARGE_DOCUMENT_BOUNDS_INVALID".into());
        }
        let readback:Value=serde_json::from_str(output)?;
        if readback["rows"].as_array().map(|a|a.len())!=Some(4000) {
            return Err("RUST_LARGE_DOCUMENT_READBACK_FAILED".into());
        }
        Ok(output.len())
    })();
    let _ = fs::remove_file(&big_file);
    let large_document_bytes=large_check?;
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
    // Use an isolated copy of the actual native executable: changing its
    // contents invalidates the previous connection before another invoke.
    let temp = std::env::temp_dir().join(format!("iwxa-rust-identity-{}-{}",
        std::process::id(), std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?.as_nanos()));
    fs::create_dir(&temp)?;
    let shadow = temp.join(file.file_name().ok_or("NO_BINARY_FILENAME")?);
    let check = (|| -> Result<(), Box<dyn std::error::Error>> {
        fs::copy(&file,&shadow)?;
        let mut shadow_port = CliPort::from_manifest(spec,shadow.clone())?;
        if status(&shadow_port.connect()) != "CONNECTED" {
            return Err("RUST_SHADOW_CONNECT_FAILED".into());
        }
        if status(&shadow_port.invoke("evaluate",&json!({"expression":"6 * 7"})))
            != "EXECUTED" { return Err("RUST_SHADOW_INVOKE_FAILED".into()); }
        // Official binaries can be executable but read-only. Only the
        // disposable fixture copy is made writable before intentional mutation.
        let mut permissions = fs::metadata(&shadow)?.permissions();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            permissions.set_mode(permissions.mode() | 0o200);
        }
        #[cfg(not(unix))]
        permissions.set_readonly(false);
        fs::set_permissions(&shadow,permissions)?;
        use std::io::Write;
        let mut changed = fs::OpenOptions::new().append(true).open(&shadow)?;
        changed.write_all(b"\n")?;
        drop(changed);
        if status(&shadow_port.capabilities()) != "CONNECTION_LOST" {
            return Err("RUST_BINARY_CHANGE_NOT_DETECTED_IN_CAPABILITIES".into());
        }
        if shadow_port.capabilities()["operations"][0]["verified"] != false {
            return Err("RUST_BINARY_STALE_CAPABILITY".into());
        }
        if status(&shadow_port.verify()) != "NOT_CONNECTED" {
            return Err("RUST_VERIFY_AFTER_DISCONNECT_FAILED".into());
        }
        Ok(())
    })();
    let _ = fs::remove_file(&shadow);
    let _ = fs::remove_dir(&temp);
    check?;
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
        "reconnected":true,
        "large_document_bytes":large_document_bytes,
        "default_output_cap_refused_large_document":true,
        "native_output_limit_bytes":1048576,
        "python_runtime_required":false,
        "node_runtime_required":false,"additional_daemon":false
    }));
    Ok(())
}
