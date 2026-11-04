//! Host-neutral native CLI port for Tauri/Rust. No Python, Node, service or store.
//! A product host decides installation, authorization, lifetime and persistence.
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Read;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, UNIX_EPOCH};

pub const CONTRACT: &str = "IWXA_PROGRAM_CONNECTION/1.0.0";
const MAX_OUTPUT: u64 = 131_072;
const TIMEOUT: Duration = Duration::from_secs(20);

fn args(value: &Value) -> Result<Vec<String>, &'static str> {
    let rows = value.as_array().ok_or("CLI_ARGUMENTS_INVALID")?;
    if rows.len() > 24 { return Err("CLI_ARGUMENTS_INVALID"); }
    rows.iter().map(|v| {
        let s = v.as_str().ok_or("CLI_ARGUMENTS_INVALID")?;
        if s.len() > 2048 || s.contains('\0') { return Err("CLI_ARGUMENTS_INVALID"); }
        Ok(s.to_owned())
    }).collect()
}
fn cli_id(id: &str) -> bool {
    (3..=80).contains(&id.len()) &&
    id.starts_with(|c: char| c.is_ascii_uppercase()) &&
    id.bytes().all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
}
fn op_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 &&
    id.starts_with(|c: char| c.is_ascii_lowercase()) &&
    id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}
fn placeholder(s: &str) -> Option<&str> {
    if s.starts_with('{') && s.ends_with('}') && s.len() > 2 {
        let name = &s[1..s.len()-1];
        if op_id(name) { return Some(name); }
    }
    None
}
fn bind(template: &[String], input: &Value) -> Result<Vec<String>, &'static str> {
    let empty = Map::new();
    let params = if input.is_null() { &empty }
        else { input.as_object().ok_or("CLI_INPUT_INVALID")? };
    if params.len() > 8 { return Err("CLI_INPUT_INVALID"); }
    let wanted: BTreeSet<&str> = template.iter().filter_map(|s| placeholder(s)).collect();
    if params.len() != wanted.len() || params.keys().any(|k| !wanted.contains(k.as_str())) {
        return Err("CLI_INPUT_INVALID");
    }
    template.iter().map(|s| {
        if let Some(key) = placeholder(s) {
            let value = params.get(key).and_then(Value::as_str).ok_or("CLI_INPUT_INVALID")?;
            if value.len() > 16384 || value.contains('\0') { return Err("CLI_INPUT_INVALID"); }
            Ok(value.to_owned())
        } else { Ok(s.clone()) }
    }).collect()
}
fn version_token(output: &str, wanted: &str) -> bool {
    for (i, _) in output.match_indices(wanted) {
        let prev = output[..i].chars().next_back();
        let next = output[i+wanted.len()..].chars().next();
        let separate = |c: Option<char>| {
            c.is_none_or(|x| !(x.is_ascii_alphanumeric() || "._+-".contains(x)))
        };
        if separate(prev) && separate(next) { return true; }
    }
    false
}
#[derive(Clone, Debug, Eq, PartialEq)]
struct FileIdentity { length: u64, modified_ns: Option<u128> }
fn file_identity(path: &PathBuf) -> Option<FileIdentity> {
    let m = fs::symlink_metadata(path).ok()?;
    if m.file_type().is_symlink() || !m.file_type().is_file() { return None; }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if m.permissions().mode() & 0o111 == 0 { return None; }
    }
    Some(FileIdentity {
        length: m.len(),
        modified_ns: m.modified().ok().and_then(|m| m.duration_since(UNIX_EPOCH).ok())
            .map(|dur| dur.as_nanos()),
    })
}
fn execute(path: &PathBuf, argv: &[String]) -> Result<(i32, String), &'static str> {
    let mut child = Command::new(path).args(argv).stdin(Stdio::null())
        .stdout(Stdio::piped()).stderr(Stdio::piped())
        .spawn().map_err(|_| "CLI_EXECUTION_FAILED")?;
    let stdout = child.stdout.take().ok_or("CLI_EXECUTION_FAILED")?;
    let stderr = child.stderr.take().ok_or("CLI_EXECUTION_FAILED")?;
    let out_thread = thread::spawn(move || {
        let mut buffer = Vec::new();
        stdout.take(MAX_OUTPUT + 1).read_to_end(&mut buffer).map(|_| buffer)
    });
    let err_thread = thread::spawn(move || {
        let mut buffer = Vec::new();
        stderr.take(MAX_OUTPUT + 1).read_to_end(&mut buffer).map(|_| buffer)
    });
    let start = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(s)) => break Ok(s),
            Ok(None) if start.elapsed() < TIMEOUT => thread::sleep(Duration::from_millis(10)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err("CLI_TIMED_OUT");
            },
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err("CLI_EXECUTION_FAILED");
            }
        }
    };
    let out = out_thread.join().map_err(|_| "CLI_EXECUTION_FAILED")?
        .map_err(|_| "CLI_EXECUTION_FAILED")?;
    let err = err_thread.join().map_err(|_| "CLI_EXECUTION_FAILED")?
        .map_err(|_| "CLI_EXECUTION_FAILED")?;
    let code = status?.code().unwrap_or(-1);
    if out.len() as u64 > MAX_OUTPUT || err.len() as u64 > MAX_OUTPUT {
        return Err("CLI_OUTPUT_TOO_LARGE");
    }
    Ok((code, String::from_utf8_lossy(&out).into_owned()))
}

pub struct CliPort {
    program_id: String,
    executable: PathBuf,
    version_args: Vec<String>,
    version_contains: String,
    operations: BTreeMap<String, Vec<String>>,
    identity: Option<FileIdentity>,
    verified: BTreeSet<String>,
}

impl CliPort {
    /// The application host supplies a trusted manifest and chosen executable.
    /// UI requests never choose arbitrary paths or commands.
    pub fn from_manifest(spec: &Value, executable: PathBuf) -> Result<Self, &'static str> {
        if spec.get("schema").and_then(Value::as_str) != Some("IWXA_CLI_PROGRAM/1") {
            return Err("CLI_SPEC_INVALID");
        }
        let id = spec.get("program_id").and_then(Value::as_str).ok_or("CLI_SPEC_INVALID")?;
        if !cli_id(id) { return Err("CLI_PROGRAM_INVALID"); }
        let version = spec.get("version_contains").and_then(Value::as_str)
            .ok_or("CLI_VERSION_INVALID")?;
        if version.is_empty() || version.len() > 128 { return Err("CLI_VERSION_INVALID"); }
        let version_args = args(spec.get("version_args").ok_or("CLI_ARGUMENTS_INVALID")?)?;
        let operations = spec.get("operations").and_then(Value::as_object)
            .ok_or("CLI_OPERATIONS_INVALID")?;
        if operations.is_empty() || operations.len() > 32 { return Err("CLI_OPERATIONS_INVALID"); }
        let mut declared = BTreeMap::new();
        for (name, argv) in operations {
            if !op_id(name) { return Err("CLI_OPERATION_INVALID"); }
            declared.insert(name.clone(), args(argv)?);
        }
        if !executable.is_absolute() { return Err("CLI_EXECUTABLE_INVALID"); }
        Ok(Self {
            program_id: id.to_owned(), executable, version_args,
            version_contains: version.to_owned(), operations: declared,
            identity: None, verified: BTreeSet::new()
        })
    }
    fn observation(&self, status: &str, detail: Value) -> Value {
        let mut out = json!({
            "schema": CONTRACT, "program_id": self.program_id,
            "transport": "cli", "status": status
        });
        if let (Some(base), Some(extra)) = (out.as_object_mut(), detail.as_object()) {
            for (key, value) in extra { base.insert(key.clone(), value.clone()); }
        }
        out
    }
    fn disconnect_state(&mut self) {
        self.identity = None;
        self.verified.clear();
    }
    pub fn inspect(&self) -> Value {
        if file_identity(&self.executable).is_some() {
            self.observation("DISCOVERED", json!({
                "installed": true, "connected": false,
                "path": self.executable.display().to_string(),
                "architecture": "UNKNOWN", "ownership": "UNKNOWN",
                "interfaces": ["cli"], "process": "UNKNOWN"
            }))
        } else {
            self.observation("NOT_FOUND", json!({
                "installed": false, "connected": false,
                "architecture": "UNKNOWN", "ownership": "UNKNOWN",
                "interfaces": [], "process": "UNKNOWN"
            }))
        }
    }
    pub fn connect(&mut self) -> Value {
        let new_id = file_identity(&self.executable);
        if new_id.is_none() {
            self.disconnect_state();
            return self.observation("NOT_FOUND", json!({"installed":false,"connected":false}));
        }
        if self.identity != new_id { self.verified.clear(); }
        let (code, output) = match execute(&self.executable, &self.version_args) {
            Ok(result) => result,
            Err(error) => {
                self.disconnect_state();
                return self.observation(error, json!({"installed":true,"connected":false}));
            }
        };
        if code != 0 || !version_token(&output, &self.version_contains) {
            self.disconnect_state();
            return self.observation("VERSION_UNVERIFIED", json!({"installed":true,"connected":false}));
        }
        self.identity = new_id;
        self.observation("CONNECTED", json!({
            "installed":true, "connected":true,
            "path":self.executable.display().to_string(),
            "version":output.trim().chars().take(256).collect::<String>()
        }))
    }
    pub fn verify(&mut self) -> Value {
        if self.identity.is_none() {
            return self.observation("NOT_CONNECTED", json!({"connected":false}));
        }
        if file_identity(&self.executable) != self.identity {
            self.disconnect_state();
            return self.observation("CONNECTION_LOST", json!({"connected":false}));
        }
        self.connect()
    }
    pub fn capabilities(&self) -> Value {
        self.observation(if self.identity.is_some() {"DECLARED"} else {"NOT_CONNECTED"}, json!({
            "connected": self.identity.is_some(),
            "operations": self.operations.keys().map(|name| json!({
                "name":name, "declared":true, "verified":self.verified.contains(name)
            })).collect::<Vec<_>>()
        }))
    }
    pub fn invoke(&mut self, name: &str, params: &Value) -> Value {
        if self.identity.is_none() {
            return self.observation("NOT_CONNECTED", json!({"executed":false}));
        }
        if file_identity(&self.executable) != self.identity {
            self.disconnect_state();
            return self.observation("CONNECTION_LOST", json!({"executed":false}));
        }
        let template = match self.operations.get(name) {
            Some(t) => t,
            None => return self.observation("OPERATION_UNSUPPORTED", json!({"executed":false}))
        };
        let argv = match bind(template, params) {
            Ok(v) => v,
            Err(e) => return self.observation(e, json!({"executed":false}))
        };
        let start = Instant::now();
        let (code, output) = match execute(&self.executable, &argv) {
            Ok(value) => value,
            Err(e) => return self.observation(e, json!({"executed":false}))
        };
        if code != 0 {
            return self.observation("EXECUTION_FAILED", json!({"executed":false,"exit_code":code}));
        }
        self.verified.insert(name.to_owned());
        self.observation("EXECUTED", json!({
            "executed":true,"operation":name, "output":output,
            "elapsed_ms":(start.elapsed().as_secs_f64()*1000.0*1000.0).round()/1000.0
        }))
    }
    pub fn disconnect(&mut self) -> Value {
        self.disconnect_state();
        self.observation("DISCONNECTED", json!({"connected":false}))
    }
    pub fn recover(&mut self) -> Value { self.connect() }

    /// JSON envelope shared by a Tauri command and JS/TS createHostAdapter.
    /// Host should serialize access to this mutable connection.
    pub fn dispatch(&mut self, envelope: &Value) -> Value {
        if envelope.get("schema").and_then(Value::as_str) != Some(CONTRACT) {
            return self.observation("PORT_SCHEMA_INVALID", json!({"executed":false}));
        }
        let request = envelope.get("request").unwrap_or(&Value::Null);
        match envelope.get("operation").and_then(Value::as_str) {
            Some("inspect") => self.inspect(),
            Some("connect") => self.connect(),
            Some("verify") => self.verify(),
            Some("capabilities") => self.capabilities(),
            Some("invoke") => {
                let name = request.get("operation").and_then(Value::as_str).unwrap_or("");
                self.invoke(name,request.get("params").unwrap_or(&Value::Null))
            },
            Some("disconnect") => self.disconnect(),
            Some("recover") => self.recover(),
            _ => self.observation("PORT_OPERATION_UNSUPPORTED", json!({"executed":false}))
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn versions_have_exact_boundaries() {
        assert!(version_token("jq-1.8.1\n", "jq-1.8.1"));
        assert!(!version_token("jq-1.8.10", "jq-1.8.1"));
        assert!(!version_token("xjq-1.8.1", "jq-1.8.1"));
        assert!(version_token("yq version v4.54.1", "v4.54.1"));
    }
    #[test]
    fn input_binding_never_interpolates_shell() {
        let args = vec!["-n".into(), "{expression}".into()];
        assert_eq!(bind(&args,&json!({"expression":"6 * 7"})).unwrap()[1],"6 * 7");
        assert!(bind(&args,&json!({"wrong":"6 * 7"})).is_err());
        assert!(bind(&args,&json!({"expression":"42","extra":"foo"})).is_err());
    }
}
