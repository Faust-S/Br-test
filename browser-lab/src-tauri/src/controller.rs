//! Open prototype controller for Obscura's public CDP interface.
//! Strict local-fixture policy. No hostile-site isolation, no T2 authority.
//! No browser-generated receipts or parallel workspace database.
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs,
    io,
    net::{Ipv4Addr, Ipv6Addr},
    path::PathBuf,
    time::Duration,
};
use tokio::{
    net::TcpStream,
    process::{Child, Command},
    time::{sleep, timeout},
};
use tokio_tungstenite::{
    connect_async,
    tungstenite::{client::IntoClientRequest, http::{header::AUTHORIZATION, HeaderValue}, Message},
    MaybeTlsStream, WebSocketStream,
};
use url::{Host, Url};
use uuid::Uuid;

type Transport = WebSocketStream<MaybeTlsStream<TcpStream>>;

#[derive(Deserialize)]
struct EngineReady {
    pid: u32,
    host: String,
    port: u16,
    websocket_path: String,
}

fn validate_engine_ready(data: &str, expected_pid: u32) -> io::Result<u16> {
    let ready: EngineReady = serde_json::from_str(data)
        .map_err(|_| denied("Invalid Obscura readiness record"))?;
    if ready.pid != expected_pid || ready.host != "127.0.0.1" ||
        ready.port == 0 || ready.websocket_path != "/devtools/browser" {
        return Err(denied("Obscura readiness identity or endpoint mismatch"));
    }
    Ok(ready.port)
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Action {
    Boot,
    Restart,
    Capture,
    NewTab { url: String },
    SelectTab { id: String },
    CloseTab { id: String },
    Navigate { url: String },
    Back,
    Forward,
    Reload,
    Resize { width: u32, height: u32 },
    Click { x: f64, y: f64 },
    Wheel { x: f64, y: f64, dx: f64, dy: f64 },
    Key { key: String, code: String, modifiers: u8 },
    Text { text: String },
}

#[derive(Clone, Serialize)]
pub struct Tab {
    id: String,
    url: String,
    title: String,
    back: bool,
    forward: bool,
}
#[derive(Serialize)]
pub struct View {
    active: Option<String>,
    tabs: Vec<Tab>,
    frame: Option<String>,
    width: u32,
    height: u32,
    status: String,
}
struct Page {
    tab: Tab,
    target: String,
    session: String,
}
pub struct Controller {
    owned: Option<Child>,
    connection: Option<Transport>,
    pages: Vec<Page>,
    active: Option<String>,
    request_id: u64,
    width: u32,
    height: u32,
    allowed: HashSet<String>,
    status: String,
    fixture_mode: bool,
}
impl Default for Controller {
    fn default() -> Self {
        Self {
            owned: None,
            connection: None,
            pages: Vec::new(),
            active: None,
            request_id: 0,
            width: 960,
            height: 620,
            allowed: HashSet::new(),
            status: "Not started".into(),
            fixture_mode: false,
        }
    }
}
impl Drop for Controller {
    fn drop(&mut self) {
        // A process owned by this controller only; not a substitute for a Job Object.
        if let Some(child) = self.owned.as_mut() {
            let _ = child.start_kill();
        }
    }
}
fn denied(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, message.to_owned())
}
fn fresh_cdp_token() -> String {
    // 244 bits of OS-backed randomness via two UUID v4 values. The token is
    // engine-process-local, never stored in the browser profile or argv.
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}
fn verified_ws_request(endpoint: &str, token: &str) -> io::Result<tokio_tungstenite::tungstenite::http::Request<()>> {
    let mut request = endpoint
        .into_client_request()
        .map_err(|e| denied(&format!("Invalid local CDP endpoint: {e}")))?;
    let authorization = HeaderValue::from_str(&format!("Bearer {token}"))
        .map_err(|_| denied("Invalid CDP authorization header"))?;
    request.headers_mut().insert(AUTHORIZATION, authorization);
    Ok(request)
}
fn verify_binary() -> io::Result<PathBuf> {
    let path = std::env::var_os("OBSCURA_LAB_BINARY")
        .ok_or_else(|| denied("OBSCURA_LAB_BINARY must name an adopted native executable"))?;
    let expected = std::env::var("OBSCURA_LAB_SHA256")
        .map_err(|_| denied("OBSCURA_LAB_SHA256 is required"))?;
    if expected.len() != 64 || !expected.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err(denied("Expected a SHA-256 digest"));
    }
    let path = fs::canonicalize(path)?;
    if !path.is_file() {
        return Err(denied("Obscura executable is not a regular file"));
    }
    let digest = hex::encode(Sha256::digest(fs::read(&path)?));
    if digest != expected.to_ascii_lowercase() {
        return Err(denied("Obscura binary hash mismatch"));
    }
    Ok(path)
}
fn is_blocked_ipv4(ip: Ipv4Addr) -> bool {
    ip.is_private() || ip.is_loopback() || ip.is_link_local() ||
    ip.is_unspecified() || ip.is_broadcast() || ip.is_multicast() ||
    ip.octets()[0] >= 224 || ip.octets()[0] == 0 ||
    (ip.octets()[0] == 100 && (64..=127).contains(&ip.octets()[1])) ||
    (ip.octets()[0] == 198 && (18..=19).contains(&ip.octets()[1]))
}
fn is_blocked_ipv6(ip: Ipv6Addr) -> bool {
    ip.is_loopback() || ip.is_unspecified() || ip.is_unique_local() ||
    ip.is_unicast_link_local() || ip.is_multicast()
}
fn is_fixture_endpoint(url: &Url) -> bool {
    // The test-only private-network flag must never accompany a public site.
    // This is a controller guard, NOT a network sandbox for subresources.
    matches!(url.host(), Some(Host::Ipv4(ip)) if ip == Ipv4Addr::LOCALHOST)
}

fn allowed_url(raw: &str, allow: &HashSet<String>, fixture_mode: bool) -> io::Result<String> {
    if raw == "about:blank" {
        return Ok(raw.to_owned());
    }
    let url = Url::parse(raw).map_err(|_| denied("Invalid web URL"))?;
    if url.username() != "" || url.password().is_some() {
        return Err(denied("Embedded credentials are not permitted"));
    }
    if url.scheme() != "https" && !(fixture_mode && url.scheme() == "http") {
        return Err(denied("Only HTTPS browsing is allowed outside a local test fixture"));
    }
    let host = url.host().ok_or_else(|| denied("URL has no host"))?;
    if fixture_mode && !is_fixture_endpoint(&url) {
        return Err(denied("Test-only private-network mode requires 127.0.0.1"));
    }
    match host {
        Host::Ipv4(ip) if is_blocked_ipv4(ip) && !(fixture_mode && ip.is_loopback()) =>
            return Err(denied("Private IP address blocked")),
        Host::Ipv6(ip) if is_blocked_ipv6(ip) && !(fixture_mode && ip.is_loopback()) =>
            return Err(denied("Private IPv6 address blocked")),
        Host::Domain(name) if name == "localhost" || name.ends_with(".localhost") ||
            name.ends_with(".local") => return Err(denied("Local hostname blocked")),
        _ => {}
    }
    if !allow.contains(&url.origin().ascii_serialization()) {
        return Err(denied("Top-level origin not explicitly granted"));
    }
    Ok(url.to_string())
}
fn grants() -> io::Result<(HashSet<String>, bool)> {
    let raw = std::env::var("OBSCURA_LAB_ALLOWED_ORIGINS")
        .map_err(|_| denied("OBSCURA_LAB_ALLOWED_ORIGINS must be set explicitly"))?;
    let fixture = std::env::var("OBSCURA_LAB_FIXTURE").ok().as_deref() == Some("1");
    let mut origins = HashSet::new();
    for grant in raw.split(',').map(str::trim) {
        let url = Url::parse(grant).map_err(|_| denied("Invalid origin grant"))?;
        if grant != url.origin().ascii_serialization() {
            return Err(denied("Origin grant must not contain paths, credentials or a trailing slash"));
        }
        if url.scheme() != "https" && !(fixture && url.scheme() == "http") {
            return Err(denied("Non-HTTPS origin grant refused"));
        }
        if fixture && !is_fixture_endpoint(&url) {
            return Err(denied("Fixture grants require the exact 127.0.0.1 origin"));
        }
        origins.insert(grant.to_owned());
    }
    if origins.is_empty() {
        return Err(denied("Empty origin grant"));
    }
    Ok((origins, fixture))
}
impl Controller {
    fn view(&self, frame: Option<String>) -> View {
        View {
            active: self.active.clone(),
            tabs: self.pages.iter().map(|p| p.tab.clone()).collect(),
            frame,
            width: self.width,
            height: self.height,
            status: self.status.clone(),
        }
    }
    fn selected(&self) -> io::Result<&Page> {
        self.pages.iter().find(|p| Some(&p.tab.id) == self.active.as_ref())
            .ok_or_else(|| denied("No active Obscura tab"))
    }
    fn guard(&self, url: &str) -> io::Result<String> {
        allowed_url(url, &self.allowed, self.fixture_mode)
    }
    async fn cdp(&mut self, method: &str, params: Value, session: Option<String>) -> io::Result<Value> {
        self.request_id += 1;
        let id = self.request_id;
        let mut message = json!({"id": id, "method": method, "params": params});
        if let Some(sid) = session {
            message["sessionId"] = sid.into();
        }
        let transport = self.connection.as_mut().ok_or_else(|| denied("Obscura CDP is not connected"))?;
        timeout(Duration::from_secs(20), transport.send(Message::Text(message.to_string().into())))
            .await.map_err(|_| denied("Timed out sending to Obscura"))?
            .map_err(|e| denied(&e.to_string()))?;
        loop {
            let frame = timeout(Duration::from_secs(20), transport.next())
                .await.map_err(|_| denied("CDP response timed out; action outcome uncertain"))?
                .ok_or_else(|| denied("CDP disconnected"))?
                .map_err(|e| denied(&e.to_string()))?;
            let Message::Text(data) = frame else { continue };
            let response: Value = serde_json::from_str(&data).map_err(|_| denied("Invalid CDP JSON"))?;
            if response["id"].as_u64() != Some(id) { continue; }
            if !response["error"].is_null() {
                return Err(denied(&format!("{method}: {}", response["error"])));
            }
            return Ok(response.get("result").cloned().unwrap_or(json!({})));
        }
    }
    async fn page(&mut self, method: &str, params: Value) -> io::Result<Value> {
        let session = self.selected()?.session.clone();
        self.cdp(method, params, Some(session)).await
    }
    async fn start(&mut self) -> io::Result<()> {
        if self.connection.is_some() { return Ok(()); }
        let binary = verify_binary()?;
        let (allow, fixture_mode) = grants()?;
        self.allowed = allow;
        self.fixture_mode = fixture_mode;
        let default_profile = dirs::data_local_dir().ok_or_else(|| denied("Missing local data directory"))?
            .join("ObscuraBrowserLab").join("profile-v1");
        // Only explicit local fixture runs can override the engine profile.
        // Normal browsing always uses its dedicated per-product directory.
        let profile = if fixture_mode {
            match std::env::var_os("OBSCURA_LAB_PROFILE_DIR") {
                Some(path) => {
                    let path = PathBuf::from(path);
                    if !path.is_absolute() { return Err(denied("Fixture profile must be an absolute path")); }
                    path
                }
                None => default_profile,
            }
        } else { default_profile };
        fs::create_dir_all(&profile)?;
        // No bind-close-rebind race. The engine chooses its port and reports
        // readiness atomically, including its PID.
        let ready_file = profile.join(format!(".obscura-cdp-{}.ready.json", Uuid::new_v4()));
        let mut child = Command::new(binary);
        child.args(["serve", "--host", "127.0.0.1", "--port", "0",
            "--workers", "1", "--max-connections", "1", "--ready-file"])
            .arg(&ready_file)
            .arg("--storage-dir").arg(&profile)
            .kill_on_drop(true)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .env_clear();
        for key in ["SystemRoot","WINDIR","PATH","Path","TEMP","TMP","HOME","USERPROFILE",
            "LOCALAPPDATA","APPDATA","LANG","DISPLAY","WAYLAND_DISPLAY",
            "XDG_RUNTIME_DIR","DBUS_SESSION_BUS_ADDRESS"] {
            if let Some(val) = std::env::var_os(key) {
                child.env(key,val);
            }
        }
        if fixture_mode { child.arg("--allow-private-network"); }
        // Upstream Obscura v0.2.6 already enforces this bearer token and
        // refuses WebSocket handshakes carrying a browser Origin header.
        // Never put the token on the command line, in JS, or in disk storage.
        let cdp_token = fresh_cdp_token();
        child.env("OBSCURA_CDP_TOKEN", &cdp_token);
        self.owned = Some(child.spawn()?);
        let identity = self.owned.as_ref().and_then(|c| c.id())
            .ok_or_else(|| denied("Owned Obscura PID unavailable"))?;
        let mut announced_port = None;
        for _ in 0..130 {
            if self.owned.as_mut().ok_or_else(|| denied("Owned process missing"))?
                .try_wait()?.is_some() { break; }
            if let Ok(contents) = fs::read_to_string(&ready_file) {
                announced_port = Some(validate_engine_ready(&contents, identity)?);
                break;
            }
            sleep(Duration::from_millis(120)).await;
        }
        let _ = fs::remove_file(&ready_file);
        if let Some(port) = announced_port {
            let endpoint = format!("ws://127.0.0.1:{port}/");
            let request = verified_ws_request(&endpoint, &cdp_token)?;
            for _ in 0..35 {
                if self.owned.as_mut().ok_or_else(|| denied("Owned process missing"))?
                    .try_wait()?.is_some() { break; }
                if let Ok(Ok((connection, _))) =
                    timeout(Duration::from_millis(500), connect_async(request.clone())).await {
                    self.connection = Some(connection);
                    break;
                }
                sleep(Duration::from_millis(120)).await;
            }
        }
        if self.connection.is_none() {
            if let Some(mut child) = self.owned.take() {
                let _ = child.start_kill();
                let _ = child.wait().await;
            }
            return Err(denied("Obscura process did not accept CDP"));
        }
        self.status = "Obscura CDP connected · laboratory mode".into();
        Ok(())
    }
    async fn restart_owned(&mut self) -> io::Result<()> {
        // Never discover a process by name or port and never terminate a
        // foreign process. All handles originate from this controller.
        let addresses = self.pages.iter().map(|p| p.tab.url.clone()).collect::<Vec<_>>();
        let active_index = self.pages.iter().position(|p| Some(&p.tab.id) == self.active.as_ref());
        for address in &addresses { self.guard(address)?; }

        self.connection.take();
        if let Some(mut child) = self.owned.take() {
            // Already-exited children are harmless; their exit is read back.
            if child.try_wait()?.is_none() {
                child.start_kill()?;
            }
            child.wait().await?;
        }
        self.pages.clear();
        self.active = None;
        self.status = "Restarting owned Obscura process".into();
        self.start().await?;
        if addresses.is_empty() {
            self.spawn_tab("about:blank".into()).await?;
        } else {
            for address in addresses { self.spawn_tab(address).await?; }
            if let Some(index) = active_index {
                if let Some(page) = self.pages.get(index) {
                    self.active = Some(page.tab.id.clone());
                    self.resize().await?;
                    self.refresh().await?;
                }
            }
        }
        Ok(())
    }

    async fn spawn_tab(&mut self, url: String) -> io::Result<()> {
        if self.pages.len() >= 10 { return Err(denied("10-tab limit")); }
        let url = self.guard(&url)?;
        let created = self.cdp("Target.createTarget", json!({"url":"about:blank"}), None).await?;
        let target = created["targetId"].as_str().ok_or_else(|| denied("Target ID missing"))?.to_owned();
        let connected = self.cdp("Target.attachToTarget", json!({"targetId":target,"flatten":true}), None).await?;
        let session = connected["sessionId"].as_str().ok_or_else(|| denied("Session ID missing"))?.to_owned();
        let id = Uuid::new_v4().to_string();
        self.pages.push(Page {
            tab: Tab { id:id.clone(), url:"about:blank".into(),
                title:"New tab".into(), back:false, forward:false },
            target, session:session.clone()
        });
        self.active = Some(id);
        self.cdp("Page.enable", json!({}), Some(session.clone())).await?;
        self.cdp("Runtime.enable", json!({}), Some(session)).await?;
        self.resize().await?;
        if url != "about:blank" { self.navigate(url).await?; }
        Ok(())
    }
    async fn resize(&mut self) -> io::Result<()> {
        if self.active.is_some() {
            self.page("Emulation.setDeviceMetricsOverride", json!({
                "width": self.width, "height": self.height, "deviceScaleFactor": 1, "mobile": false
            })).await?;
        }
        Ok(())
    }
    async fn navigate(&mut self, url: String) -> io::Result<()> {
        let url = self.guard(&url)?;
        self.page("Page.navigate", json!({"url":url})).await?;
        self.refresh().await
    }
    async fn refresh(&mut self) -> io::Result<()> {
        if self.active.is_none() { return Ok(()); }
        let evaluation = self.page("Runtime.evaluate", json!({
            "expression":"({url:location.href,title:document.title})", "returnByValue":true
        })).await?;
        let value = &evaluation["result"]["value"];
        let url = value["url"].as_str().unwrap_or("about:blank");
        if url != "about:blank" { self.guard(url)?; }
        let title = value["title"].as_str().filter(|s| !s.is_empty()).unwrap_or(url);
        let history = self.page("Page.getNavigationHistory", json!({})).await?;
        let index = history["currentIndex"].as_u64().unwrap_or(0) as usize;
        let total = history["entries"].as_array().map(|x| x.len()).unwrap_or(0);
        let id = self.active.clone();
        if let Some(p) = self.pages.iter_mut().find(|x| Some(&x.tab.id) == id.as_ref()) {
            p.tab.url = url.to_owned();
            p.tab.title = title.chars().take(120).collect();
            p.tab.back = index > 0;
            p.tab.forward = index + 1 < total;
        }
        Ok(())
    }
    async fn history(&mut self, step: i64) -> io::Result<()> {
        let h = self.page("Page.getNavigationHistory",json!({})).await?;
        let current = h["currentIndex"].as_i64().unwrap_or(0);
        let index = current + step;
        let items = h["entries"].as_array().ok_or_else(|| denied("History unavailable"))?;
        if index < 0 || index as usize >= items.len() { return Ok(()); }
        let item = &items[index as usize];
        let url = item["url"].as_str().ok_or_else(|| denied("History URL missing"))?;
        self.guard(url)?;
        let entry = item["id"].as_u64().ok_or_else(|| denied("History ID missing"))?;
        self.page("Page.navigateToHistoryEntry",json!({"entryId":entry})).await?;
        self.refresh().await
    }
    fn check_point(&self,x:f64,y:f64) -> io::Result<()> {
        if !x.is_finite() || !y.is_finite() || x < 0.0 || y < 0.0 ||
            x > self.width as f64 || y > self.height as f64 {
            return Err(denied("Pointer outside viewport"));
        }
        Ok(())
    }
    // Security Supervisor first slice: all CDP traffic must belong to a live,
    // originally spawned child. No PID lookup, no foreign process adoption.
    fn check_owned_process(&mut self) -> io::Result<()> {
        let alive = match self.owned.as_mut() {
            Some(child) => child.try_wait()?.is_none(),
            None => false,
        };
        if !alive {
            self.connection.take();
            self.status = "Owned Obscura process stopped; use Restart".into();
            return Err(denied("Obscura process stopped; restart owned engine to recover"));
        }
        Ok(())
    }

    pub async fn execute(&mut self,action:Action)->io::Result<View> {
        match action {
            Action::Boot => {
                self.start().await?;
                if self.pages.is_empty() { self.spawn_tab("about:blank".into()).await?; }
            }
            Action::Restart => self.restart_owned().await?,
            other => {
                self.check_owned_process()?;
                if self.connection.is_none() { return Err(denied("Obscura must be started first")); }
                match other {
                    Action::Capture => {
                        if self.active.is_none() { return Ok(self.view(None)); }
                        self.refresh().await?;
                        let value=self.page("Page.captureScreenshot",json!({"format":"png"})).await?;
                        let png=value["data"].as_str().ok_or_else(|| denied("Screenshot missing"))?;
                        if png.len()>16*1024*1024 || !png.starts_with("iVBORw0KGgo") {
                            return Err(denied("Invalid or oversized PNG"));
                        }
                        return Ok(self.view(Some(png.to_owned())));
                    }
                    Action::NewTab{url} => self.spawn_tab(url).await?,
                    Action::SelectTab{id} => {
                        if !self.pages.iter().any(|p| p.tab.id==id) { return Err(denied("Unknown tab")); }
                        self.active=Some(id);
                        self.resize().await?;
                        self.refresh().await?;
                    }
                    Action::CloseTab{id} => {
                        let index=self.pages.iter().position(|p| p.tab.id==id).ok_or_else(|| denied("Unknown tab"))?;
                        let removed=self.pages.remove(index);
                        self.cdp("Target.closeTarget",json!({"targetId":removed.target}),None).await?;
                        if self.active.as_deref()==Some(id.as_str()) {
                            self.active=self.pages.last().map(|p| p.tab.id.clone());
                        }
                        if self.pages.is_empty() { self.spawn_tab("about:blank".into()).await?; }
                        else { self.resize().await?; }
                    }
                    Action::Navigate{url} => self.navigate(url).await?,
                    Action::Back => self.history(-1).await?,
                    Action::Forward => self.history(1).await?,
                    Action::Reload => {
                        let current=self.selected()?.tab.url.clone();
                        self.guard(&current)?;
                        self.page("Page.reload",json!({})).await?;
                        self.refresh().await?;
                    }
                    Action::Resize{width,height} => {
                        if !(160..=4096).contains(&width) || !(160..=4096).contains(&height) {
                            return Err(denied("Invalid viewport size"));
                        }
                        self.width=width;self.height=height;
                        self.resize().await?;
                    }
                    Action::Click{x,y} => {
                        self.check_point(x,y)?;
                        for t in ["mousePressed","mouseReleased"] {
                            self.page("Input.dispatchMouseEvent",json!({
                                "type":t,"x":x,"y":y,"button":"left","clickCount":1
                            })).await?;
                        }
                    }
                    Action::Wheel{x,y,dx,dy} => {
                        self.check_point(x,y)?;
                        if !dx.is_finite() || !dy.is_finite() || dx.abs()>2500.0 || dy.abs()>2500.0 {
                            return Err(denied("Invalid wheel delta"));
                        }
                        self.page("Input.dispatchMouseEvent",json!({
                            "type":"mouseWheel","x":x,"y":y,"deltaX":dx,"deltaY":dy
                        })).await?;
                    }
                    Action::Key{key,code,modifiers} => {
                        if key.len()>50 || code.len()>50 || modifiers>15 { return Err(denied("Invalid key")); }
                        for t in ["keyDown","keyUp"] {
                            self.page("Input.dispatchKeyEvent",json!({
                                "type":t,"key":key,"code":code,"modifiers":modifiers
                            })).await?;
                        }
                    }
                    Action::Text{text} => {
                        if text.chars().count()>256 { return Err(denied("Text too long")); }
                        self.page("Input.insertText",json!({"text":text})).await?;
                    }
                    Action::Boot | Action::Restart => unreachable!(),
                }
            }
        }
        Ok(self.view(None))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_ready_identity_is_bound_to_spawned_pid_and_loopback() {
        let valid = r#"{"pid":1234,"host":"127.0.0.1","port":49152,"websocket_path":"/devtools/browser"}"#;
        assert_eq!(validate_engine_ready(valid, 1234).unwrap(), 49152);
        assert!(validate_engine_ready(valid, 5).is_err());
        for invalid in [
            r#"{"pid":1234,"host":"0.0.0.0","port":49152,"websocket_path":"/devtools/browser"}"#,
            r#"{"pid":1234,"host":"127.0.0.1","port":0,"websocket_path":"/devtools/browser"}"#,
            r#"{"pid":1234,"host":"127.0.0.1","port":49152,"websocket_path":"/json/version"}"#,
            "{}"
        ] {
            assert!(validate_engine_ready(invalid, 1234).is_err());
        }
    }
    #[test]
    fn cdp_token_is_ephemeral_and_authorization_not_exposed_to_urls() {
        let a = fresh_cdp_token();
        let b = fresh_cdp_token();
        assert_eq!(a.len(), 64);
        assert!(a.bytes().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, b);
        let req = verified_ws_request("ws://127.0.0.1:9222/", &a).unwrap();
        assert_eq!(req.headers().get(AUTHORIZATION).unwrap().to_str().unwrap(), format!("Bearer {a}"));
        assert_eq!(req.uri().to_string(), "ws://127.0.0.1:9222/");
    }
    #[test]
    fn top_level_navigation_refuses_ungranted_local_and_file_schemes() {
        let allow=HashSet::from(["https://example.org".into()]);
        assert!(allowed_url("https://example.org/path",&allow,false).is_ok());
        for item in ["file:///etc/passwd","http://example.org/",
            "https://example.org.evil.test/","https://127.0.0.1/",
            "https://user@example.org/", "javascript:alert(1)"] {
            assert!(allowed_url(item,&allow,false).is_err(),"{item}");
        }
    }
    #[test]
    fn missing_owner_refuses_cdp_and_leaves_restart_available() {
        let mut controller = Controller::default();
        let err = controller.check_owned_process().expect_err("missing owner must refuse");
        assert_eq!(err.kind(), io::ErrorKind::PermissionDenied);
        assert!(controller.connection.is_none());
        assert!(controller.status.contains("Restart"));
    }
    #[test]
    fn fixture_rejects_remote_origins_even_when_explicitly_granted() {
        let allow = HashSet::from(["http://127.0.0.1:8080".into(), "https://example.org".into()]);
        assert!(allowed_url("http://127.0.0.1:8080/path", &allow, true).is_ok());
        assert!(allowed_url("https://example.org/", &allow, true).is_err());
        assert!(allowed_url("http://127.0.0.2:8080/", &allow, true).is_err());
    }
    #[test]
    fn localhost_requires_explicit_fixture_allowance() {
        let allow=HashSet::from(["http://127.0.0.1:8080".into()]);
        assert!(allowed_url("http://127.0.0.1:8080/page",&allow,true).is_ok());
        assert!(allowed_url("http://127.0.0.1:8080/page",&allow,false).is_err());
        assert!(allowed_url("http://127.0.0.1:8090/page",&allow,true).is_err());
    }
}
