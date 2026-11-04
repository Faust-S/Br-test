use serde_json::{json, Value};
use std::{env, path::PathBuf, process::{Child, Command}, time::{SystemTime, UNIX_EPOCH}};

pub const SCHEMA: &str = "IWXA_PRESENTATION_BRIDGE/1.0.0";

#[cfg(windows)]
mod os {
    use std::mem::zeroed;

    #[repr(C)]
    #[derive(Copy, Clone)]
    struct FileTime { low: u32, high: u32 }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> isize;
        fn GetProcessTimes(handle: isize, created: *mut FileTime,
            exited: *mut FileTime, kernel: *mut FileTime, user: *mut FileTime) -> i32;
        fn CloseHandle(handle: isize) -> i32;
    }

    #[link(name = "user32")]
    unsafe extern "system" {
        fn EnumWindows(callback: Option<unsafe extern "system" fn(isize, isize) -> i32>, data: isize) -> i32;
        fn GetWindowThreadProcessId(hwnd: isize, pid: *mut u32) -> u32;
        fn IsWindowVisible(hwnd: isize) -> i32;
        fn GetWindowTextLengthW(hwnd: isize) -> i32;
        fn SetForegroundWindow(hwnd: isize) -> i32;
        fn GetForegroundWindow() -> isize;
    }

    pub fn process_stamp(pid: u32) -> Option<u64> {
        if pid == 0 { return None; }
        unsafe {
            let handle = OpenProcess(0x1000, 0, pid);
            if handle == 0 { return None; }
            let mut created: FileTime = zeroed();
            let mut exited: FileTime = zeroed();
            let mut kernel: FileTime = zeroed();
            let mut user: FileTime = zeroed();
            let success = GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user);
            CloseHandle(handle);
            (success != 0).then_some((u64::from(created.high) << 32) | u64::from(created.low))
        }
    }

    unsafe extern "system" fn enum_window(hwnd: isize, data: isize) -> i32 {
        let state = &mut *(data as *mut (u32, Option<isize>));
        let mut owner = 0u32;
        GetWindowThreadProcessId(hwnd, &mut owner);
        if owner == state.0 && IsWindowVisible(hwnd) != 0 && GetWindowTextLengthW(hwnd) > 0 {
            state.1 = Some(hwnd);
            return 0;
        }
        1
    }

    pub fn window_for(pid: u32) -> Option<isize> {
        let mut state = (pid, None::<isize>);
        unsafe { EnumWindows(Some(enum_window), (&mut state as *mut _) as isize); }
        state.1
    }

    pub fn focus(hwnd: isize) -> bool {
        unsafe {
            SetForegroundWindow(hwnd);
            GetForegroundWindow() == hwnd
        }
    }
}

#[cfg(not(windows))]
mod os {
    pub fn process_stamp(_: u32) -> Option<u64> { None }
    pub fn window_for(_: u32) -> Option<isize> { None }
    pub fn focus(_: isize) -> bool { false }
}

struct Bound {
    pid: u32,
    stamp: u64,
    window: Option<isize>,
    binding_ref: String,
    child: Option<Child>,
}

pub struct Port {
    target: Option<(PathBuf, Vec<String>)>,
    reattach: Option<(u32, u64)>,
    bound: Option<Bound>,
}

impl Port {
    pub fn from_env() -> Self {
        let target = env::var_os("IWXA_U04_TARGET_EXE").map(PathBuf::from)
            .and_then(|executable| {
                let args: Vec<String> = serde_json::from_str(
                    &env::var("IWXA_U04_TARGET_ARGS").unwrap_or_else(|_| "[]".into())
                ).ok()?;
                (executable.is_absolute() && executable.is_file()
                    && args.len() <= 64
                    && args.iter().all(|s| s.len() <= 16384 && !s.contains('\0')))
                    .then_some((executable, args))
            });
        let reattach = env::var("IWXA_U04_REATTACH_PID").ok()
            .and_then(|s| s.parse::<u32>().ok())
            .zip(env::var("IWXA_U04_REATTACH_STAMP").ok()
                .and_then(|s| s.parse::<u64>().ok()));
        Self { target, reattach, bound: None }
    }

    fn binding_view(bound: &Bound) -> Value {
        json!({"appRef":"U04_TEST_PROGRAM","hostRef":"U04_DESKTOP_HOST",
            "bindingRef":bound.binding_ref,
            "windowRef":bound.window.map(|h| h.to_string())})
    }

    fn view(bound: &Bound, status: &str, extras: Value) -> Value {
        let mut response = json!({"schema":SCHEMA,"status":status,
            "binding":Self::binding_view(bound),"processId":bound.pid,
            "processStamp":bound.stamp.to_string()});
        if let (Some(to), Some(from)) = (response.as_object_mut(), extras.as_object()) {
            for (k, v) in from { to.insert(k.clone(), v.clone()); }
        }
        response
    }

    pub fn handle(&mut self, operation: &str) -> Value {
        match operation {
            "capabilities" => json!({"schema":SCHEMA, "status":"CAPABILITIES",
                "reattachAvailable":self.reattach.is_some(),
                "qualificationMode":env::var_os("IWXA_U04_QUALIFICATION").is_some(),
                "capabilities":[
                  {"mode":"LAUNCH","supported":cfg!(windows)&&self.target.is_some(),
                    "reason":if cfg!(windows) {"HOST_TARGET_REQUIRED"} else {"PLATFORM_NOT_IMPLEMENTED"}},
                  {"mode":"FOCUS","supported":cfg!(windows)},
                  {"mode":"LINKED_WINDOW","supported":cfg!(windows)},
                  {"mode":"EMBEDDED_VIEW","supported":false,"reason":"NO_OFFICIAL_TOOLKIT_EMBED_API"}
                ]}),
            "open" => self.open(),
            "activate" => self.activate(),
            "closeBinding" => self.detach(),
            "getBinding" => match &self.bound {
                Some(bound) => Self::view(bound, "BOUND", json!({})),
                None => json!({"schema":SCHEMA,"status":"BINDING_NOT_FOUND"})
            },
            _ => json!({"schema":SCHEMA,"status":"OPERATION_UNSUPPORTED"})
        }
    }

    fn open(&mut self) -> Value {
        if !cfg!(windows) {
            return json!({"schema":SCHEMA,"status":"PLATFORM_UNSUPPORTED"});
        }
        if self.bound.is_some() {
            return json!({"schema":SCHEMA,"status":"ALREADY_BOUND"});
        }
        let (pid, stamp, child, spawned) = if let Some((pid, expected)) = self.reattach {
            match os::process_stamp(pid) {
                Some(observed) if observed == expected => (pid, expected, None, false),
                _ => return json!({"schema":SCHEMA,"status":"PROCESS_IDENTITY_CHANGED"})
            }
        } else {
            let Some((binary, args)) = self.target.as_ref() else {
                return json!({"schema":SCHEMA,"status":"HOST_TARGET_UNAVAILABLE"});
            };
            let mut command = Command::new(binary);
            command.args(args);
            #[cfg(windows)]
            { use std::os::windows::process::CommandExt;
              // Detached GUI process must not open an extra console.
              command.creation_flags(0x08000000);
            }
            let child = match command.spawn() {
                Ok(child) => child,
                Err(_) => return json!({"schema":SCHEMA,"status":"LAUNCH_FAILED"})
            };
            let pid = child.id();
            let Some(stamp) = os::process_stamp(pid) else {
                return json!({"schema":SCHEMA,"status":"PROCESS_NOT_OBSERVABLE"});
            };
            (pid, stamp, Some(child), true)
        };
        let window = os::window_for(pid);
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos()).unwrap_or(0);
        let bound = Bound {pid, stamp, window, child,
            binding_ref:format!("u04-{pid}-{nonce}")};
        let result = Self::view(&bound,
            if window.is_some() {"WINDOW_OBSERVED"} else {"LAUNCHED_NO_WINDOW"},
            json!({"spawned":spawned}));
        self.bound = Some(bound);
        result
    }

    fn activate(&mut self) -> Value {
        let Some(bound) = self.bound.as_mut() else {
            return json!({"schema":SCHEMA,"status":"BINDING_NOT_FOUND","focused":false});
        };
        if bound.child.as_mut().is_some_and(|c| matches!(c.try_wait(),Ok(Some(_)))) {
            return Self::view(bound, "PROCESS_EXITED", json!({"focused":false}));
        }
        if os::process_stamp(bound.pid) != Some(bound.stamp) {
            return Self::view(bound, "PROCESS_IDENTITY_CHANGED", json!({"focused":false}));
        }
        let Some(hwnd) = os::window_for(bound.pid) else {
            bound.window = None;
            return Self::view(bound, "WINDOW_NOT_FOUND", json!({"focused":false}));
        };
        bound.window = Some(hwnd);
        let focused = os::focus(hwnd);
        Self::view(bound, if focused {"WINDOW_FOCUSED"} else {"WINDOW_FOCUS_DENIED"},
                   json!({"focused":focused}))
    }

    fn detach(&mut self) -> Value {
        let Some(bound) = self.bound.take() else {
            return json!({"schema":SCHEMA,"status":"BINDING_NOT_FOUND"});
        };
        // Drop the child HANDLE, never call kill(), terminate(), or WM_CLOSE.
        Self::view(&bound,"BINDING_CLOSED",json!({"processTerminated":false}))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn embedded_is_never_invented() {
        let mut port = Port {target:None, reattach:None, bound:None};
        let caps = port.handle("capabilities");
        assert_eq!(caps["capabilities"][3]["supported"],false);
        assert_eq!(caps["capabilities"][3]["reason"],"NO_OFFICIAL_TOOLKIT_EMBED_API");
        assert_eq!(port.handle("activate")["status"],"BINDING_NOT_FOUND");
        assert_eq!(port.handle("closeBinding")["status"],"BINDING_NOT_FOUND");
    }
}
