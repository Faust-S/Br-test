//! Host-neutral native CLI port for Tauri/Rust. No Python, Node, service or store.
//! A product host decides installation, authorization, lifetime and persistence.
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Read;
use std::path::PathBuf;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, UNIX_EPOCH};

#[cfg(feature = "tauri-host")]
pub mod tauri_host;

pub const CONTRACT: &str = "IWXA_PROGRAM_CONNECTION/1.0.0";
const DEFAULT_OUTPUT: u64 = 131_072;
const MAX_APPROVED_OUTPUT: u64 = 4 * 1024 * 1024;
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
// The native port manages only children it spawned. It never kills an
// already-running GUI or a host-owned process outside this invocation.
#[cfg(unix)]
unsafe extern "C" {
    fn kill(pid: i32, signal: i32) -> i32;
}


#[cfg(windows)]
#[link(name="kernel32")]
unsafe extern "system" {
    fn CreateJobObjectW(attributes: *const std::ffi::c_void, name: *const u16)
        -> *mut std::ffi::c_void;
    fn AssignProcessToJobObject(job: *mut std::ffi::c_void, process: *mut std::ffi::c_void)
        -> i32;
    fn TerminateJobObject(job: *mut std::ffi::c_void, exit_code: u32) -> i32;
    fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
}

#[cfg(windows)]
struct NativeCliJob(*mut std::ffi::c_void);
#[cfg(windows)]
impl NativeCliJob {
    fn assign(child: &std::process::Child) -> Option<Self> {
        use std::os::windows::io::AsRawHandle;
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() { return None; }
            if AssignProcessToJobObject(handle, child.as_raw_handle()) == 0 {
                let _ = CloseHandle(handle);
                return None;
            }
            Some(Self(handle))
        }
    }
    fn terminate(&self) -> bool {
        unsafe { TerminateJobObject(self.0, 1) != 0 }
    }
}
#[cfg(windows)]
impl Drop for NativeCliJob {
    fn drop(&mut self) {
        unsafe { let _ = CloseHandle(self.0); }
    }
}
#[cfg(not(windows))]
struct NativeCliJob;
#[cfg(not(windows))]
impl NativeCliJob {
    fn assign(_child: &std::process::Child) -> Option<Self> { None }
}

fn terminate_cli_tree(child: &mut std::process::Child, job: Option<&NativeCliJob>) {
    #[cfg(unix)]
    {
        let _ = job;
        if let Ok(pid) = i32::try_from(child.id()) {
            // Each CLI is spawned in a private process group.
            unsafe { let _ = kill(-pid, 9); }
        }
    }
    #[cfg(windows)]
    {
        let terminated = job.is_some_and(NativeCliJob::terminate);
        if !terminated && child.try_wait().ok().flatten().is_none() {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            let pid = child.id().to_string();
            let _ = Command::new("taskkill.exe")
                .args(["/PID", pid.as_str(), "/T", "/F"])
                .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null())
                .creation_flags(CREATE_NO_WINDOW).status();
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn execute(path: &PathBuf, argv: &[String], max_output_bytes: u64) -> Result<(i32, String), &'static str> {
    let mut command = Command::new(path);
    command.args(argv).stdin(Stdio::null())
        .stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // Suppress a transient console when called from an installed GUI.
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command.spawn().map_err(|_| "CLI_EXECUTION_FAILED")?;
    // Attach immediately: descendants inherit this Windows Job Object.
    // Normal completion only closes the job handle; it never terminates them.
    let native_job = NativeCliJob::assign(&child);
    let stdout = child.stdout.take().ok_or("CLI_EXECUTION_FAILED")?;
    let stderr = child.stderr.take().ok_or("CLI_EXECUTION_FAILED")?;
    // The reader stops at the approved limit+1. Without a signal the child can fill
    // its pipe and block until the 20-second timeout instead of failing fast.
    let oversized = Arc::new(AtomicBool::new(false));
    let out_limit = Arc::clone(&oversized);
    let out_done = Arc::new(AtomicBool::new(false));
    let out_finished = Arc::clone(&out_done);
    let out_thread = thread::spawn(move || {
        let mut buffer = Vec::new();
        let result = stdout.take(max_output_bytes + 1).read_to_end(&mut buffer);
        if buffer.len() as u64 > max_output_bytes {
            out_limit.store(true, Ordering::Release);
        }
        out_finished.store(true, Ordering::Release);
        result.map(|_| buffer)
    });
    let err_limit = Arc::clone(&oversized);
    let err_done = Arc::new(AtomicBool::new(false));
    let err_finished = Arc::clone(&err_done);
    let err_thread = thread::spawn(move || {
        let mut buffer = Vec::new();
        let result = stderr.take(DEFAULT_OUTPUT + 1).read_to_end(&mut buffer);
        if buffer.len() as u64 > DEFAULT_OUTPUT {
            err_limit.store(true, Ordering::Release);
        }
        err_finished.store(true, Ordering::Release);
        result.map(|_| buffer)
    });
    let start = Instant::now();
    let status = loop {
        if oversized.load(Ordering::Acquire) {
            terminate_cli_tree(&mut child, native_job.as_ref());
            break Err("CLI_OUTPUT_TOO_LARGE");
        }
        match child.try_wait() {
            Ok(Some(s)) => break Ok(s),
            Ok(None) if start.elapsed() < TIMEOUT => thread::sleep(Duration::from_millis(10)),
            Ok(None) => {
                terminate_cli_tree(&mut child, native_job.as_ref());
                break Err("CLI_TIMED_OUT");
            },
            Err(_) => {
                terminate_cli_tree(&mut child, native_job.as_ref());
                break Err("CLI_EXECUTION_FAILED");
            }
        }
    };
    // A child can exit while one of its descendants still holds stdout open.
    // Never block the native host indefinitely on a reader thread.
    let remaining = if status.is_ok() {
        TIMEOUT.saturating_sub(start.elapsed())
    } else {
        Duration::from_millis(800)
    };
    let reader_deadline = Instant::now() + remaining;
    while (!out_done.load(Ordering::Acquire) || !err_done.load(Ordering::Acquire))
        && Instant::now() < reader_deadline {
        thread::sleep(Duration::from_millis(10));
    }
    if !out_done.load(Ordering::Acquire) || !err_done.load(Ordering::Acquire) {
        if status.is_ok() { terminate_cli_tree(&mut child, native_job.as_ref()); }
        return Err(status.err().unwrap_or("CLI_TIMED_OUT"));
    }
    let out = out_thread.join().map_err(|_| "CLI_EXECUTION_FAILED")?
        .map_err(|_| "CLI_EXECUTION_FAILED")?;
    let err = err_thread.join().map_err(|_| "CLI_EXECUTION_FAILED")?
        .map_err(|_| "CLI_EXECUTION_FAILED")?;
    let code = status?.code().unwrap_or(-1);
    if out.len() as u64 > max_output_bytes || err.len() as u64 > DEFAULT_OUTPUT {
        return Err("CLI_OUTPUT_TOO_LARGE");
    }
    let output = String::from_utf8(out).map_err(|_| "CLI_OUTPUT_ENCODING_UNSUPPORTED")?;
    Ok((code, output))
}

pub struct CliPort {
    program_id: String,
    executable: PathBuf,
    version_args: Vec<String>,
    version_contains: String,
    operations: BTreeMap<String, Vec<String>>,
    max_output_bytes: u64,
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
        let max_output_bytes = match spec.get("max_output_bytes") {
            Some(value) => {
                let bytes = value.as_u64().ok_or("CLI_OUTPUT_LIMIT_INVALID")?;
                if !(1024..=MAX_APPROVED_OUTPUT).contains(&bytes) {
                    return Err("CLI_OUTPUT_LIMIT_INVALID");
                }
                bytes
            },
            None => DEFAULT_OUTPUT,
        };
        Ok(Self {
            program_id: id.to_owned(), executable, version_args,
            version_contains: version.to_owned(), operations: declared,
            max_output_bytes, identity: None, verified: BTreeSet::new()
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
        let (code, output) = match execute(&self.executable, &self.version_args, DEFAULT_OUTPUT) {
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
    pub fn capabilities(&mut self) -> Value {
        let lost = self.identity.is_some()
            && file_identity(&self.executable) != self.identity;
        if lost { self.disconnect_state(); }
        self.observation(if lost {"CONNECTION_LOST"} else if self.identity.is_some() {
            "DECLARED"
        } else {"NOT_CONNECTED"}, json!({
            "connected": self.identity.is_some(),
            "operations": self.operations.keys().map(|name| json!({
                "name":name, "declared":true, "verified":self.verified.contains(name)
            })).collect::<Vec<_>>()
        }))
    }
    pub fn invoke(&mut self, name: &str, params: &Value) -> Value {
        if self.identity.is_none() {
            return self.observation("NOT_CONNECTED", json!({"executed":false,"executionOutcome":"NONE"}));
        }
        if file_identity(&self.executable) != self.identity {
            self.disconnect_state();
            return self.observation("CONNECTION_LOST", json!({"executed":false,"executionOutcome":"NONE"}));
        }
        let template = match self.operations.get(name) {
            Some(t) => t,
            None => return self.observation("OPERATION_UNSUPPORTED", json!({"executed":false,"executionOutcome":"NONE"}))
        };
        let argv = match bind(template, params) {
            Ok(v) => v,
            Err(e) => return self.observation(e, json!({"executed":false,"executionOutcome":"NONE"}))
        };
        let start = Instant::now();
        let (code, output) = match execute(&self.executable, &argv, self.max_output_bytes) {
            Ok(value) => value,
            Err(e) => {
                self.verified.remove(name);
                return self.observation(e, json!({"executed":false,"executionOutcome":"UNKNOWN"}))
            }
        };
        if code != 0 {
            self.verified.remove(name);
            return self.observation("EXECUTION_FAILED",
                json!({"executed":false,"executionOutcome":"UNKNOWN","exit_code":code}));
        }
        self.verified.insert(name.to_owned());
        self.observation("EXECUTED", json!({
            "executed":true,"executionOutcome":"ACKNOWLEDGED","operation":name, "output":output,
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
    #[cfg(windows)]
    #[test]
    fn background_cli_has_no_console_window() {
        // The Windows hosted runner installs Python before running Rust tests.
        // This checks the actual child process, not merely the flag constant.
        let args = vec![
            "-c".to_string(),
            "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())".to_string()
        ];
        let (code, output) = execute(&PathBuf::from("python"), &args, DEFAULT_OUTPUT)
            .expect("Windows Python CLI must launch");
        assert_eq!(code, 0);
        assert_eq!(output.trim(), "0", "GUI-owned CLI must not create a console");
    }
    #[test]
    fn excessive_cli_output_is_rejected_before_timeout() {
        // Python is available in the existing cross-platform CI matrix.
        for stream in ["stdout", "stderr"] {
            let code = format!(
                "import sys,time; sys.{stream}.buffer.write(b'x'*262144); \
sys.{stream}.flush(); time.sleep(30)"
            );
            let start = Instant::now();
            let result = execute(&PathBuf::from("python"), &["-c".into(), code], DEFAULT_OUTPUT);
            assert_eq!(result, Err("CLI_OUTPUT_TOO_LARGE"));
            assert!(start.elapsed() < Duration::from_secs(8),
                "oversized {stream} blocked until timeout");
        }
    }
    #[test]
    fn overflow_terminates_native_descendant_before_external_file_write() {
        let root = std::env::temp_dir().join(format!(
            "iwxa-rust-cli-tree-{}-{}",
            std::process::id(),
            std::time::SystemTime::now().duration_since(UNIX_EPOCH)
                .expect("time").as_nanos()
        ));
        fs::create_dir_all(&root).expect("test directory");
        let started = root.join("parent started.txt");
        let marker = root.join("orphan effect.txt");
        let script = concat!(
            "import subprocess,sys,time\n",
            "from pathlib import Path\n",
            "started,marker=sys.argv[1:3]\n",
            "sub='import sys,time;from pathlib import Path;'",
            "+'time.sleep(1.5);Path(sys.argv[1]).write_text(\"ORPHAN\")'\n",
            "subprocess.Popen([sys.executable,'-c',sub,marker],",
            "stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr)\n",
            "Path(started).write_text('STARTED')\n",
            "sys.stdout.buffer.write(b'X'*262144);sys.stdout.flush()\n",
            "time.sleep(4)\n"
        );
        let args = [
            "-c".to_string(),
            script.to_string(),
            started.display().to_string(),
            marker.display().to_string()
        ];
        let result = execute(&PathBuf::from("python"), &args, DEFAULT_OUTPUT);
        assert_eq!(result, Err("CLI_OUTPUT_TOO_LARGE"));
        assert!(started.exists(), "real nested CLI never started");
        thread::sleep(Duration::from_millis(1700));
        assert!(!marker.exists(), "aborted Rust CLI left a descendant alive");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn non_utf8_output_is_not_reported_as_valid_text() {
        let args = ["-c".to_string(),
            "import sys;sys.stdout.buffer.write(bytes([255]))".to_string()];
        assert_eq!(execute(&PathBuf::from("python"), &args, DEFAULT_OUTPUT),
            Err("CLI_OUTPUT_ENCODING_UNSUPPORTED"));
    }
    #[test]
    fn approved_large_stdout_is_bounded_and_readable() {
        let rows = ["-c".to_string(),
            "import sys;sys.stdout.write('A'*262144)".to_string()];
        assert_eq!(execute(&PathBuf::from("python"), &rows, DEFAULT_OUTPUT),
            Err("CLI_OUTPUT_TOO_LARGE"));
        let (code, output) = execute(&PathBuf::from("python"), &rows, 524288)
            .expect("approved larger output must complete");
        assert_eq!(code, 0);
        assert_eq!(output.len(),262144);
        assert!(output.bytes().all(|byte|byte==b'A'));
        let huge = ["-c".to_string(),
            "import sys;sys.stdout.write('B'*786432)".to_string()];
        assert_eq!(execute(&PathBuf::from("python"), &huge, 524288),
            Err("CLI_OUTPUT_TOO_LARGE"));
    }
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
