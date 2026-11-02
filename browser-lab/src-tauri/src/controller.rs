//! Open prototype controller for Obscura's public CDP interface.
//! Strict local-fixture policy. No hostile-site isolation, no T2 authority.
//! No browser-generated receipts or parallel workspace database.
use futures_util::{SinkExt, StreamExt};
use crate::session::{self, BrowserSession};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs,
    io::{self, Read},
    net::{Ipv4Addr, Ipv6Addr},
    path::{Path, PathBuf},
    time::{Duration, Instant},
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
    #[cfg(debug_assertions)]
    ProbeWorkspace,
    OpenSite { site: String, url: Option<String> },
    ResetSites,
    NewTab { url: String },
    SelectTab { id: String },
    CloseTab { id: String },
    Navigate { url: String },
    Back,
    Forward,
    Reload,
    Resize { width: u32, height: u32 },
    Click { x: f64, y: f64 },
    Move { x: f64, y: f64 },
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
    granted_sites: Vec<String>,
    site_picker_available: bool,
    debug_probe_available: bool,
}
struct Page {
    tab: Tab,
    target: String,
    session: String,
    pending_navigation: Option<(String, String, Instant)>,
    // Persisted background tabs keep their address without starting a page.
    // First activation performs a fresh, normal navigation in Obscura.
    deferred_url: Option<String>,
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
    granted_sites: HashSet<String>,
    status: String,
    fixture_mode: bool,
    bundle_resource_dir: Option<PathBuf>,
    browser_session_path: Option<PathBuf>,
    recovering_tabs: bool,
    stream_session: Option<String>,
    stream_latest: Option<String>,
    stream_ack_ids: Vec<i64>,
    last_metadata_refresh: Instant,
    // User-facing notice only. Never infer real consent from CDP console text.
    confirmation_unavailable: bool,
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
            granted_sites: HashSet::new(),
            status: "Not started".into(),
            fixture_mode: false,
            bundle_resource_dir: None,
            browser_session_path: None,
            recovering_tabs: false,
            stream_session: None,
            stream_latest: None,
            stream_ack_ids: Vec::new(),
            last_metadata_refresh: Instant::now(),
            confirmation_unavailable: false,
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


#[cfg(debug_assertions)]
const ARTBAZI_LAUNCH_PROBE: &str = r#"(function() {
    if (location.origin !== 'https://artbazi.ru' ||
        !/^\/services\/launch-navigator\/?$/.test(location.pathname))
        return {scope:'outside-artbazi-launch'};
    function position(el, control) {
        if (!el || !el.getBoundingClientRect) return null;
        var rect=el.getBoundingClientRect();
        var x=Math.round(rect.left+rect.width/2);
        var y=Math.round(rect.top+rect.height/2);
        // Read-only hit-test at the precise intended native mouse location.
        // A visible DOM rect does not prove the renderer will click that node.
        var hit=document.elementFromPoint(x,y);
        var label=control && control.closest ? control.closest('label') : null;
        var anotherControl=hit && hit.closest ?
            hit.closest('button,a[href],select,textarea,[contenteditable]') : null;
        return {x:x,y:y,
            visible:rect.width>0 && rect.height>0 && rect.top>=4 &&
                rect.bottom<=Math.min(innerHeight-10,700) &&
                rect.left>=0 && rect.right<=innerWidth,
            enabled:control ? !control.disabled : !el.disabled,
            hitTag:hit && hit.tagName ? hit.tagName.toLowerCase() : 'none',
            hitMatches:control ? !!(hit && (hit===control ||
                (label && label.contains(hit) && !anotherControl))) : null};
    }
    function radioPosition(input) {
        if (!input) return null;
        var direct=position(input,input);
        if (direct && direct.visible && direct.hitMatches) return direct;
        var label=input.closest('label');
        var broad=label ? position(label,input) : null;
        if (broad && broad.visible && broad.hitMatches) return broad;
        return direct || broad;
    }
    function button(label) {
        return Array.from(document.querySelectorAll('button'))
            .find(function(node){return (node.textContent||'').trim()===label;});
    }
    var situation=document.querySelector('input[type="radio"][name="situation"][value="leads"]');
    var resources=document.querySelector('input[type="radio"][name="resources"][value="time"]');
    var save=button('Сохранить в рабочей области');
    var transfer=document.querySelector('[data-testid="t2-public-transfer"]');
    var pending=false, statusCode='NONE';
    try {
        // Existence only: never expose Result contents or source storage keys.
        pending=sessionStorage.getItem('artbazi.t2.pending-public-result.v1')!==null;
    } catch (_) {statusCode='STORAGE_UNAVAILABLE';}
    var section=transfer&&transfer.closest('section');
    var visibleStatus=section&&section.querySelector('[role="status"]');
    var message=(visibleStatus&&visibleStatus.textContent||'');
    if (message.includes('защищённого HTTPS')) statusCode='INSECURE_CONTEXT';
    else if (message.includes('Результат сервиса не готов')) statusCode='INVALID_SOURCE';
    else if (message.includes('Изменилась')) statusCode='REVISION_CHANGED';
    else if (message && statusCode==='NONE') statusCode='OTHER_STATUS';
    return {scope:'artbazi-launch-navigator',
        controls:{
            // Click the actual native radio control. In Obscura a click on the
            // wrapping label's center does not reliably change the input.
            situation:radioPosition(situation),resources:radioPosition(resources),
            calculate:position(button('Получить результат')),
            save:position(save),transfer:position(transfer)},
        scrollY:Math.round(globalThis.scrollY||0),
        situationFocused:!!(situation&&document.activeElement===situation),
        resourcesFocused:!!(resources&&document.activeElement===resources),
        situationSelected:!!(situation&&situation.checked),
        resourcesSelected:!!(resources&&resources.checked),
        answersSelected:!!(situation&&situation.checked&&resources&&resources.checked),
        calculationVisible:!!save,
        publicEditionAvailable:!!transfer,
        secureContext:!!globalThis.isSecureContext,
        randomUUIDAvailable:!!(globalThis.crypto&&typeof crypto.randomUUID==='function'),
        pendingCandidatePresent:pending,
        transferStatusCode:statusCode};
})()"#;

#[cfg(debug_assertions)]
fn is_artbazi_launch_navigator(url: &str) -> bool {
    let Ok(parsed)=Url::parse(url) else { return false };
    parsed.origin().ascii_serialization()=="https://artbazi.ru" &&
        matches!(parsed.path(),"/services/launch-navigator"|"/services/launch-navigator/")
}

#[cfg(debug_assertions)]
const WORKSPACE_CONFIG_PROBE: &str = r#"(function() {
    var origin = location.origin;
    if (!['https://learn.iwxa.ru','https://artbazi.ru'].includes(origin) ||
        !location.pathname.startsWith('/workspace')) {
        return {scope:'outside-preview-workspace'};
    }
    var text = document.body ? (document.body.textContent || '') : '';
    var stage = text.includes('Loading domain configuration') ? 'config-loading' :
        text.includes('Activating profile') ? 'profile-loading' : 'not-detected';
    // Inspect the same real DOM that is visible in the window. In particular,
    // detect the actual overview, not only a fragile attribute selector.
    var hostMounted = false, overviewRendered = false, createDialog = false;
    var workspaceViewMounted = false, workspaceViewError = false;
    var buttons = {};
    var all = document.getElementsByTagName('*');
    for (var n = 0; n < all.length; n++) {
        var el = all[n];
        var ui = el.getAttribute('data-t2-ui');
        var testid = el.getAttribute('data-testid');
        if (ui === 'workspace') hostMounted = true;
        if (testid === 'overview-view') overviewRendered = true;
        if (testid === 'create-workspace-dialog') createDialog = true;
        if (testid === 'workspace-view') workspaceViewMounted = true;
        if (['workspace-record-not-found','workspace-record-unavailable',
            'workspace-record-error','workspace-profile-error'].includes(testid)) {
            workspaceViewError = true;
        }
        if ((testid === 'new-workspace-toggle' || testid === 'create-workspace-confirm')
            && el.getBoundingClientRect) {
            var rect = el.getBoundingClientRect();
            buttons[testid] = {x:Math.round(rect.left+rect.width/2),
                y:Math.round(rect.top+rect.height/2)};
        }
    }
    var root = document.querySelector('#root');
    // ArtBazi starts the domain Host via import() from its onload handler.
    // Record *read-only* lifecycle facts to distinguish missing bootstrap,
    // stalled module work and a mounted Host without inspecting user data.
    var hostShell = origin==='https://artbazi.ru' ?
        document.getElementById('artbazi-t2-host-container') : null;
    var hostBootstrap = origin==='https://artbazi.ru' ?
        !!document.querySelector('script[data-artbazi-t2-host]') : false;
    var hostFailure = !!(hostShell &&
        hostShell.querySelector('[role="alert"]'));
    var hostAssets = document.querySelectorAll(
        'script[src*="/workspace/assets/"],link[href*="/workspace/assets/"]').length;
    var moduleResources = null;
    try {
        moduleResources = performance.getEntriesByType('resource').filter(
            function(entry){return entry && typeof entry.name==='string' &&
                entry.name.includes('/workspace/assets/')}).length;
    } catch (_) {}
    // Debug-only readback of browser scheduler counters. No callbacks are
    // executed, no queues are drained and no Result or module source is read.
    // These lexical bindings may be unavailable in another engine revision:
    // report that explicitly rather than misclassifying absence as zero.
    var engineTasks = {
        available:false,queuedByPriority:null,queuedTotal:null,
        wakePending:null,retryScheduled:null,retryCount:null,
        dynamicScriptsPending:null,loadDelayingScriptsPending:null
    };
    try {
        if (typeof _browserPostedTaskQueues !== 'undefined' &&
            Array.isArray(_browserPostedTaskQueues)) {
            var counts = _browserPostedTaskQueues.map(function(queue) {
                return Array.isArray(queue) ? queue.length : 0;
            });
            engineTasks.available = true;
            engineTasks.queuedByPriority = counts;
            engineTasks.queuedTotal = counts.reduce(function(sum,n) {return sum+n;},0);
            engineTasks.wakePending = typeof _browserPostedTaskWakePending === 'boolean' ?
                _browserPostedTaskWakePending : null;
            engineTasks.retryScheduled = typeof _browserPostedTaskRetryTimer !== 'undefined' ?
                _browserPostedTaskRetryTimer !== null : null;
            engineTasks.retryCount = typeof _browserPostedTaskRetryCount === 'number' ?
                _browserPostedTaskRetryCount : null;
        }
        engineTasks.dynamicScriptsPending =
            typeof __obscura_hasPendingDynamicScripts === 'function' ?
                __obscura_hasPendingDynamicScripts() === true : null;
        engineTasks.loadDelayingScriptsPending =
            typeof __obscura_hasPendingLoadDelayingScripts === 'function' ?
                __obscura_hasPendingLoadDelayingScripts() === true : null;
    } catch (_) {
        // The debug probe may encounter a replaced realm. This has no
        // bearing on page correctness and must never interrupt rendering.
    }
    var moduleBoot = {
        engineTasks:engineTasks,
        bootstrapPresent:hostBootstrap,
        shellPresent:!!hostShell,
        shellVisible:!!(hostShell && hostShell.style.display!=='none'),
        failureVisible:hostFailure,
        readyState:document.readyState,
        hostAssetElements:hostAssets,
        loadedModuleResourceEntries:moduleResources
    };
    // Config endpoints were already verified with real HTTP 200 responses.
    // Repeating async fetches during every T2 hydration poll perturbs the
    // same task queue being diagnosed and can hide a scheduling bug.
    var mains = Array.from(document.querySelectorAll('main'));
    var domain = origin==='https://artbazi.ru' ?
        document.querySelector('main[aria-label="АртБази"]') : null;
    function point(node) {
        if (!node || !node.getBoundingClientRect) return null;
        var rect=node.getBoundingClientRect();
        return {x:Math.round(rect.left+rect.width/2),
            y:Math.round(rect.top+rect.height/2),
            visible:rect.width>0 && rect.height>0 && rect.top>=4 &&
                rect.bottom<=Math.min(innerHeight-10,700) &&
                rect.left>=0 && rect.right<=innerWidth,
            enabled:!node.disabled};
    }
    function scoped(selector) {return domain ? domain.querySelector(selector) : null;}
    var pending=scoped('section[aria-label="Подтверждение результата сервиса"]');
    var candidate=pending ? pending.querySelector('pre') : null;
    var verified=scoped('[data-testid="t2-public-verified-result"]');
    var resultList=scoped('section[aria-label="Список результатов"] button');
    var resultSection=scoped('section[aria-label="Просмотр результата"]');
    var resultText=resultSection ? resultSection.querySelector('pre') : null;
    var resultHeader=resultSection ? resultSection.querySelector('h2') : null;
    var eventList=scoped('section[aria-label="История выбранного результата"]');
    var focused=scoped('[data-testid="t2-public-workspace"]');
    var historyButton=Array.from(domain ? domain.querySelectorAll('button') : [])
        .find(function(node){return (node.textContent||'').trim()==='История изменений';});
    var knownRaw=resultText ? (resultText.textContent||'') : '';
    var resultControls={
        confirm:point(scoped('[data-testid="t2-public-confirm-save"]')),
        focusWorkspace:point(focused),
        selectSaved:point(resultList),history:point(historyButton)
    };
    var t2Result={
        focusSelected:!!(focused&&focused.value),
        focusChoices:focused ? Math.max(0,focused.options.length-1) : 0,
        pending:!!pending,
        pendingSourceMatched:!!(candidate&&
            (candidate.textContent||'').includes('service_id: launch-navigator')),
        verified:!!(verified&&verified.getAttribute('data-t2-object-id')&&
            verified.getAttribute('data-t2-workspace-id')),
        freshReceiptAndReadback:!!(verified&&
            (verified.textContent||'').includes('Результат сохранён в Kernel; квитанция и повторное чтение подтверждены.')),
        revisionOne:!!(resultHeader&&(resultHeader.textContent||'').includes('ревизия 1')),
        readbackMatches:knownRaw.includes('service_id: launch-navigator')&&
            knownRaw.includes('situation: leads')&&knownRaw.includes('resources: time'),
        savedEventVisible:!!(eventList&&
            (eventList.textContent||'').includes('Результат сохранён')&&
            (eventList.textContent||'').includes('Ревизия 1'))
    };
    return {scope:'public-workspace',stage:stage,hostMounted:hostMounted,
        t2Result:t2Result,resultControls:resultControls,
        moduleBoot:moduleBoot,
        overviewRendered:overviewRendered,createDialog:createDialog,
        workspaceViewMounted:workspaceViewMounted,workspaceViewError:workspaceViewError,
        buttons:buttons,rootChildren:root ? root.children.length : 0,
        mainTextLength:mains.reduce(function(n,element) {
            return n+(element.textContent||'').trim().length;
        },0)};
})()"#;


fn is_preview_workspace(url: &str) -> bool {
    let Ok(parsed) = Url::parse(url) else { return false; };
    parsed.path().starts_with("/workspace") &&
        session::PREVIEW_SITES.iter()
            .any(|(_, origin)| parsed.origin().ascii_serialization() == *origin)
}

fn denied(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, message.to_owned())
}

const TAB_FOCUS_JS: &str = r#"(function(reverse) {
    var nodes = Array.from(document.querySelectorAll(
        'a[href],area[href],button,input,select,textarea,[tabindex]'));
    nodes = nodes.filter(function(el) {
        if (el.disabled || el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
        if (el.tabIndex < 0) return false;
        if (el.closest && el.closest('[inert]')) return false;
        try {
            var style = getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' ||
                style.visibility === 'collapse') return false;
        } catch (_) {}
        return true;
    });
    if (!nodes.length) return false;
    // Positive tabindex precedes normal DOM order, per browser tab traversal.
    var ordered = nodes.map(function(el, order) { return {el:el, order:order}; });
    ordered.sort(function(a,b) {
        var at = a.el.tabIndex, bt = b.el.tabIndex;
        if (at === 0 && bt > 0) return 1;
        if (at > 0 && bt === 0) return -1;
        if (at > 0 && bt > 0 && at !== bt) return at - bt;
        return a.order - b.order;
    });
    var active = document.activeElement;
    var index = ordered.findIndex(function(item) { return item.el === active; });
    var next = index < 0 ? (reverse ? ordered.length - 1 : 0) :
        (index + (reverse ? -1 : 1) + ordered.length) % ordered.length;
    ordered[next].el.focus();
    return true;
})"#;

const SELECT_FOCUSED_TEXT_JS: &str = r#"(function() {
    var active = document.activeElement;
    if (!active) return false;
    var tag = (active.tagName || '').toUpperCase();
    if ((tag === 'INPUT' || tag === 'TEXTAREA') &&
        typeof active.setSelectionRange === 'function' &&
        !active.disabled) {
        try { active.setSelectionRange(0, (active.value || '').length); return true; }
        catch (_) { return false; }
    }
    if (active.isContentEditable && globalThis.getSelection &&
        document.createRange) {
        var sel = globalThis.getSelection();
        var range = document.createRange();
        range.selectNodeContents(active);
        sel.removeAllRanges();
        sel.addRange(range);
        return true;
    }
    return false;
})()"#;

fn editing_key_default(key: &str, modifiers: u8) -> Option<String> {
    // A native GUI text event is a user action, not a webpage-to-Host command.
    // Obscura 0.2.6 synthesizes keydown/keyup but does not implement the
    // browser's default focus traversal or select-all behavior.
    if key == "Tab" && modifiers & !8 == 0 {
        return Some(format!("{}({})", TAB_FOCUS_JS, modifiers & 8 != 0));
    }
    if key.eq_ignore_ascii_case("a") && modifiers & 6 != 0 && modifiers & !14 == 0 {
        return Some(SELECT_FOCUSED_TEXT_JS.to_owned());
    }
    None
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
fn verified_digest(path: &Path, expected: &str) -> io::Result<PathBuf> {
    if expected.len() != 64 || !expected.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err(denied("Expected a complete SHA-256 digest"));
    }
    let path = fs::canonicalize(path)?;
    if !path.is_file() { return Err(denied("Obscura executable missing")); }
    // Hash the packaged executable in a small bounded buffer rather than
    // reading the entire engine binary into transient browser-shell memory.
    let mut file = fs::File::open(&path)?;
    let mut hasher = Sha256::new();
    let mut chunk = [0u8; 64 * 1024];
    loop {
        let bytes = file.read(&mut chunk)?;
        if bytes == 0 { break; }
        hasher.update(&chunk[..bytes]);
    }
    let digest = hex::encode(hasher.finalize());
    if digest != expected.to_ascii_lowercase() {
        return Err(denied("Obscura executable does not match compiled SHA-256"));
    }
    Ok(path)
}

fn verify_binary(resource_dir: Option<&Path>) -> io::Result<PathBuf> {
    // A developer may adopt a separately inspected engine only in a DEBUG
    // build. Production binaries cannot redirect the engine using an env var.
    #[cfg(debug_assertions)]
    if let Some(value) = std::env::var_os("OBSCURA_LAB_BINARY") {
        let expected = std::env::var("OBSCURA_LAB_SHA256")
            .map_err(|_| denied("Adopted debug engine requires OBSCURA_LAB_SHA256"))?;
        return verified_digest(Path::new(&value), &expected);
    }

    let root = resource_dir.ok_or_else(|| denied("Tauri resource directory unavailable"))?;
    let directory = root.join("obscura");
    let expected = option_env!("IWXA_BUNDLED_OBSCURA_SHA256")
        .ok_or_else(|| denied("This build does not include an approved Obscura engine"))?;
    let binary_name = if cfg!(windows) { "obscura.exe" } else { "obscura" };
    // No user-supplied path is used for release engine selection; reject a
    // symlink that redirects outside the installed, packaged engine directory.
    let path = verified_digest(&directory.join(binary_name), expected)?;
    if path.parent() != Some(fs::canonicalize(&directory)?.as_path()) {
        return Err(denied("Bundled engine redirects outside its resource directory"));
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
fn parse_grants(raw: Option<&str>, fixture: bool) -> io::Result<HashSet<String>> {
    let Some(raw) = raw else {
        if fixture { return Err(denied("A fixture requires explicit loopback origin grants")); }
        return Ok(HashSet::new());
    };
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
    if origins.is_empty() { return Err(denied("Empty origin grant")); }
    Ok(origins)
}
fn validated_preview_destination(site: &str, destination: Option<&str>) -> io::Result<String> {
    let origin = session::preview_site_origin(site)
        .ok_or_else(|| denied("Unknown public preview site"))?;
    let default = format!("{origin}/");
    let requested = destination.unwrap_or(&default);
    if requested == "about:blank" {
        return Err(denied("Site approval must open its site"));
    }
    let exact = HashSet::from([origin.to_owned()]);
    allowed_url(requested, &exact, false)
}
fn grants() -> io::Result<(HashSet<String>, bool)> {
    let raw = match std::env::var("OBSCURA_LAB_ALLOWED_ORIGINS") {
        Ok(value) => Some(value),
        Err(std::env::VarError::NotPresent) => None,
        Err(std::env::VarError::NotUnicode(_)) =>
            return Err(denied("Invalid origin grant environment value")),
    };
    let fixture = std::env::var("OBSCURA_LAB_FIXTURE").ok().as_deref() == Some("1");
    if !cfg!(debug_assertions) && (raw.is_some() || fixture) {
        return Err(denied("Origin environment overrides are debug-only"));
    }
    Ok((parse_grants(raw.as_deref(), fixture)?, fixture))
}
impl Controller {
    // URL grants are rechecked every time; a copied session file cannot
    // escalate page privileges or resurrect no-longer-permitted origins.
    fn admitted_session(&self, saved: BrowserSession) -> Option<BrowserSession> {
        let mut tabs = Vec::new();
        let mut selected = None;
        for (index, raw) in saved.tabs.into_iter().enumerate().take(10) {
            if let Ok(url) = self.guard(&raw) {
                if index == saved.active { selected = Some(tabs.len()); }
                tabs.push(url);
            }
        }
        if tabs.is_empty() { return None; }
        Some(BrowserSession {
            version: session::SCHEMA_VERSION,
            active: selected.unwrap_or(0),
            tabs,
            granted_sites: saved.granted_sites,
        })
    }

    fn save_tab_session(&self) -> io::Result<()> {
        let Some(path) = self.browser_session_path.as_deref() else { return Ok(()) };
        if self.pages.is_empty() { return Ok(()) };
        let active = self.pages.iter()
            .position(|p| Some(&p.tab.id) == self.active.as_ref()).unwrap_or(0);
        let mut granted_sites = self.granted_sites.iter().cloned().collect::<Vec<_>>();
        granted_sites.sort();
        session::write(path, &BrowserSession {
            version: session::SCHEMA_VERSION,
            tabs: self.pages.iter().map(|p| p.tab.url.clone()).collect(),
            active,
            granted_sites,
        })
    }

    async fn reopen_saved_tabs(&mut self) -> io::Result<()> {
        let saved = self.browser_session_path.as_deref()
            .and_then(session::read)
            .and_then(|state| self.admitted_session(state));
        if let Some(saved) = saved {
            let active = saved.active;
            for url in saved.tabs {
                // Restore addresses in their original order but only navigate
                // the selected tab. Background sites must not boot React and
                // compete for the same engine during cold startup.
                self.stage_restored_tab(url).await?;
            }
            self.activate_restored_tab(active).await?;
        }
        if self.pages.is_empty() {
            self.spawn_tab("about:blank".to_string()).await?;
        }
        Ok(())
    }

    pub fn with_resource_dir(resource_dir: Option<PathBuf>) -> Self {
        let mut controller = Self::default();
        controller.bundle_resource_dir = resource_dir;
        controller
    }
    fn view(&self, frame: Option<String>) -> View {
        View {
            active: self.active.clone(),
            tabs: self.pages.iter().map(|p| p.tab.clone()).collect(),
            frame,
            width: self.width,
            height: self.height,
            status: if self.confirmation_unavailable {
                "Action not confirmed · JavaScript confirmation dialog unavailable".to_owned()
            } else { self.status.clone() },
            granted_sites: {
                let mut ids = self.granted_sites.iter().cloned().collect::<Vec<_>>();
                ids.sort();
                ids
            },
            site_picker_available: !self.fixture_mode,
            debug_probe_available: cfg!(debug_assertions) && !self.fixture_mode,
        }
    }
    fn selected(&self) -> io::Result<&Page> {
        self.pages.iter().find(|p| Some(&p.tab.id) == self.active.as_ref())
            .ok_or_else(|| denied("No active Obscura tab"))
    }
    fn guard(&self, url: &str) -> io::Result<String> {
        allowed_url(url, &self.allowed, self.fixture_mode)
    }
    // A message from a website is not trusted evidence of consent. This marker
    // only explains an engine-originated *denial* to the human in the status bar.
    // It never triggers, authorizes, retries or replays any site operation.
    // There is one native CDP reader. Responses and asynchronous screencast
    // events MUST pass through the same decoder; otherwise a command can
    // silently consume frames that arrived before its response.
    async fn read_cdp(&mut self, duration: Duration) -> io::Result<Option<Value>> {
        let transport = self.connection.as_mut()
            .ok_or_else(|| denied("Obscura CDP is not connected"))?;
        let packet = match timeout(duration, transport.next()).await {
            Err(_) => return Ok(None),
            Ok(None) => return Err(denied("Obscura CDP disconnected")),
            Ok(Some(Err(error))) => return Err(denied(&format!("CDP transport: {error}"))),
            Ok(Some(Ok(packet))) => packet,
        };
        let Message::Text(data) = packet else {
            if matches!(packet, Message::Close(_)) {
                return Err(denied("Obscura closed the CDP connection"));
            }
            return Ok(Some(json!({})));
        };
        let message: Value = serde_json::from_str(&data)
            .map_err(|_| denied("Invalid Obscura CDP JSON"))?;
        if message["method"] == "Page.screencastFrame" {
            self.ingest_screencast_frame(&message)?;
        }
        let active_session = self.selected().ok().map(|page| page.session.clone());
        if active_session.as_deref().is_some_and(|session| {
            is_unavailable_confirmation_notice(&message, session)
        }) {
            self.confirmation_unavailable = true;
        }
        Ok(Some(message))
    }

    fn ingest_screencast_frame(&mut self, message: &Value) -> io::Result<()> {
        if self.stream_session.as_deref() != message["sessionId"].as_str() {
            return Ok(());
        }
        // The numeric params.sessionId identifies a screencast stream and
        // is distinct from the outer CDP target sessionId string.
        let stream_id = message["params"]["sessionId"].as_i64()
            .ok_or_else(|| denied("Obscura screencast stream ID missing"))?;
        let data = message["params"]["data"].as_str()
            .ok_or_else(|| denied("Obscura screencast frame missing"))?;
        if data.len() > 16 * 1024 * 1024 || !data.starts_with("iVBORw0KGgo") {
            return Err(denied("Oversized or invalid Obscura screencast frame"));
        }
        if self.stream_ack_ids.len() >= 4 {
            return Err(denied("Obscura screencast backpressure exceeded"));
        }
        self.stream_latest = Some(data.to_owned());
        self.stream_ack_ids.push(stream_id);
        Ok(())
    }

    async fn send_cdp_unawaited(&mut self, method: &str, params: Value,
        session: String) -> io::Result<()> {
        self.request_id += 1;
        let message = json!({
            "id":self.request_id,"method":method,
            "params":params,"sessionId":session
        });
        let transport = self.connection.as_mut()
            .ok_or_else(|| denied("Obscura CDP is not connected"))?;
        timeout(Duration::from_secs(3), transport.send(Message::Text(message.to_string().into())))
            .await.map_err(|_| denied("Timed out acknowledging Obscura frame"))?
            .map_err(|e| denied(&format!("Obscura ACK failed: {e}")))?;
        Ok(())
    }

    async fn acknowledge_frames(&mut self) -> io::Result<()> {
        if let Some(session) = self.stream_session.clone() {
            for id in std::mem::take(&mut self.stream_ack_ids) {
                self.send_cdp_unawaited("Page.screencastFrameAck",
                    json!({"sessionId":id}),session.clone()).await?;
            }
        }
        Ok(())
    }

    async fn cdp(&mut self, method: &str, params: Value, session: Option<String>) -> io::Result<Value> {
        // An explicit, DEBUG-only 180s SPA experiment needs the CDP bridge
        // to outlive the engine watchdog; the normal product stays at 20s.
        let diagnostic = cfg!(debug_assertions) && !self.fixture_mode &&
            std::env::var("IWXA_SPA_DIAGNOSTIC_180S").as_deref() == Ok("1");
        let command_limit = if diagnostic {
            Duration::from_secs(200)
        } else { Duration::from_secs(20) };
        self.cdp_with_limit(method, params, session, command_limit).await
    }

    // A CDP command owns one absolute wall-clock budget, not a fresh budget
    // for every unsolicited console/network/screencast event. Otherwise a
    // busy or hostile page can keep the native controller hung indefinitely
    // by sending unrelated events before its missing command response.
    async fn cdp_with_limit(&mut self, method: &str, params: Value,
        session: Option<String>, command_limit: Duration) -> io::Result<Value> {
        let deadline = Instant::now() + command_limit;
        self.request_id += 1;
        let id = self.request_id;
        let mut message = json!({"id": id, "method": method, "params": params});
        if let Some(sid) = session {
            message["sessionId"] = sid.into();
        }
        let transport = self.connection.as_mut()
            .ok_or_else(|| denied("Obscura CDP is not connected"))?;
        let send_limit = deadline.saturating_duration_since(Instant::now());
        timeout(send_limit, transport.send(Message::Text(message.to_string().into())))
            .await.map_err(|_| denied("Timed out sending to Obscura"))?
            .map_err(|e| denied(&e.to_string()))?;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(denied("CDP response timed out; action outcome uncertain"));
            }
            let response = self.read_cdp(remaining).await?
                .ok_or_else(|| denied("CDP response timed out; action outcome uncertain"))?;
            if response["id"].as_u64() != Some(id) { continue; }
            if !response["error"].is_null() {
                return Err(denied(&format!("{method}: {}", response["error"])));
            }
            return Ok(response.get("result").cloned().unwrap_or(json!({})));
        }
    }

    async fn release_screencast(&mut self) {
        if let Some(session) = self.stream_session.take() {
            // If the target has already closed, the best-effort stop may fail.
            let _ = self.cdp("Page.stopScreencast",json!({}),Some(session)).await;
        }
        self.stream_latest = None;
        self.stream_ack_ids.clear();
    }

    async fn poll_screencast(&mut self) -> io::Result<Option<String>> {
        let active_session = self.selected()?.session.clone();
        if self.selected()?.tab.url == "about:blank" {
            self.release_screencast().await;
            return Ok(None);
        }
        if self.stream_session.as_deref() != Some(active_session.as_str()) {
            self.release_screencast().await;
            self.stream_session = Some(active_session.clone());
            let result = self.cdp("Page.startScreencast",json!({
                "format":"png","everyNthFrame":1,
                "maxWidth":self.width,"maxHeight":self.height
            }),Some(active_session)).await;
            match result {
                Ok(value) if value["obscuraAutonomousFrames"] == true => {}
                Ok(_) => {
                    self.stream_session = None;
                    return Err(denied("Obscura native screencast support is unavailable"));
                }
                Err(error) => {
                    self.stream_session = None;
                    return Err(error);
                }
            }
        }
        // Keep capture work bounded; idle pages do not trigger rasterization.
        // The native compositor generates frames only after page activity.
        for _ in 0..4 {
            if self.stream_latest.is_some() { break; }
            if self.read_cdp(Duration::from_millis(12)).await?.is_none() {
                break;
            }
        }
        let image = self.stream_latest.take();
        if image.is_some() {
            // Ack only frames actually consumed by a capture tick. Without
            // capture ticks (hidden window) the upstream 2-frame bound applies.
            self.acknowledge_frames().await?;
        }
        // The first real frame can arrive before the 2s metadata interval.
        // Refresh promptly so a stale loading overlay does not hide the page.
        let first_navigation_frame = image.is_some() &&
            self.selected()?.pending_navigation.is_some();
        if first_navigation_frame ||
            self.last_metadata_refresh.elapsed() >= Duration::from_secs(2) {
            self.refresh().await?;
            self.last_metadata_refresh = Instant::now();
        }
        Ok(image)
    }
    async fn page(&mut self, method: &str, params: Value) -> io::Result<Value> {
        let session = self.selected()?.session.clone();
        self.cdp(method, params, Some(session)).await
    }
    async fn start(&mut self) -> io::Result<()> {
        if self.connection.is_some() { return Ok(()); }
        let binary = verify_binary(self.bundle_resource_dir.as_deref())?;
        let (mut allow, fixture_mode) = grants()?;
        self.fixture_mode = fixture_mode;
        let default_profile = dirs::data_local_dir().ok_or_else(|| denied("Missing local data directory"))?
            .join("ObscuraBrowserLab").join("profile-v1");
        // An explicit profile override is available to DEBUG test builds only.
        // Release builds always use the native product profile, without env grants.
        let profile = if cfg!(debug_assertions) {
            match std::env::var_os("OBSCURA_LAB_PROFILE_DIR") {
                Some(path) => {
                    let path = PathBuf::from(path);
                    if !path.is_absolute() { return Err(denied("Debug profile must be absolute")); }
                    path
                }
                None => default_profile,
            }
        } else { default_profile };
        fs::create_dir_all(&profile)?;
        self.browser_session_path = Some(profile.join("browser-tabs-v1.json"));
        self.granted_sites.clear();
        if !fixture_mode {
            if let Some(saved) = self.browser_session_path.as_deref().and_then(session::read) {
                for id in saved.granted_sites {
                    if let Some(origin) = session::preview_site_origin(&id) {
                        self.granted_sites.insert(id);
                        allow.insert(origin.to_owned());
                    }
                }
            }
        }
        self.allowed = allow;
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
        // Engine environment is intentionally sanitized above. Previous
        // attempts set RUST_LOG in the GUI harness but it never reached
        // Obscura and both engine stdio handles were discarded. This is an
        // isolated, DEBUG-only experiment in the public real-site test.
        // No user-controlled timeout or log setting reaches release builds.
        // Narrow, opt-in trace for the real-site GUI. Unlike the retired
        // 180-second diagnostic this changes neither page budgets nor timeouts.
        // The child environment is cleared above, so RUST_LOG must be added
        // explicitly to the owned child and its stderr must be forwarded.
        if cfg!(debug_assertions) && !fixture_mode &&
            std::env::var("IWXA_BROWSER_ENGINE_TRACE").as_deref() == Ok("1") {
            child.env("RUST_LOG", "warn,obscura_browser::page=info,obscura_js::module_loader=debug");
            child.stdout(std::process::Stdio::inherit());
            child.stderr(std::process::Stdio::inherit());
        }
        if cfg!(debug_assertions) && !fixture_mode &&
            std::env::var("IWXA_SPA_DIAGNOSTIC_180S").as_deref() == Ok("1") {
            child.env("OBSCURA_SCRIPT_DEADLINE_MS", "180000");
            child.env("OBSCURA_MODULE_BUDGET_MS", "180000");
            // These independent upstream ceilings are 30s navigation and
            // 60s CDP by default. Without raising them, a 180s JS budget
            // would never be exercisable by the real integrated GUI.
            child.env("OBSCURA_NAV_TIMEOUT_MS", "200000");
            child.env("OBSCURA_CDP_COMMAND_TIMEOUT_MS", "200000");
            child.env("RUST_LOG", "obscura_browser::page=info,obscura_js=warn");
            child.stdout(std::process::Stdio::inherit());
            child.stderr(std::process::Stdio::inherit());
            eprintln!("IWXA_SPA_DIAGNOSTIC_ACTIVE script_ms=180000 module_ms=180000 nav_ms=200000 cdp_ms=200000 bridge_s=200");
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
        self.status = "Obscura connected · approved sites only".into();
        Ok(())
    }
    async fn restart_owned(&mut self) -> io::Result<()> {
        // Never discover a process by name or port and never terminate a
        // foreign process. All handles originate from this controller.
        let addresses = self.pages.iter().map(|p| p.tab.url.clone()).collect::<Vec<_>>();
        let active_index = self.pages.iter().position(|p| Some(&p.tab.id) == self.active.as_ref());
        for address in &addresses { self.guard(address)?; }

        self.stream_session = None;
        self.stream_latest = None;
        self.stream_ack_ids.clear();
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
            for address in addresses { self.stage_restored_tab(address).await?; }
            self.activate_restored_tab(active_index.unwrap_or(0)).await?;
        }
        Ok(())
    }

    async fn stage_restored_tab(&mut self, url: String) -> io::Result<()> {
        let allowed = self.guard(&url)?;
        self.spawn_tab("about:blank".to_owned()).await?;
        if allowed != "about:blank" {
            let page = self.pages.last_mut()
                .ok_or_else(|| denied("Restored tab was not created"))?;
            page.tab.url = allowed.clone();
            page.tab.title = "Restored tab · open to load".into();
            page.deferred_url = Some(allowed);
        }
        Ok(())
    }
    async fn load_active_deferred(&mut self) -> io::Result<bool> {
        let active = self.active.clone();
        let page = self.pages.iter_mut()
            .find(|p| Some(&p.tab.id) == active.as_ref())
            .ok_or_else(|| denied("Restored tab not selected"))?;
        let Some(url) = page.deferred_url.take() else { return Ok(false); };
        // The engine still has about:blank. Reset only this tab's UI address
        // before navigating so the existing pending-navigation guard can
        // distinguish the true old document from the saved destination.
        page.tab.url = "about:blank".into();
        self.navigate(url).await?;
        Ok(true)
    }
    async fn activate_restored_tab(&mut self, index: usize) -> io::Result<()> {
        if let Some(page) = self.pages.get(index) {
            self.active = Some(page.tab.id.clone());
            self.resize().await?;
            self.load_active_deferred().await?;
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
            target, session:session.clone(), pending_navigation: None,
            deferred_url: None
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
    fn mark_pending_navigation(&mut self, url: String, previous: String) {
        let active = self.active.clone();
        if let Some(page) = self.pages.iter_mut().find(|p| Some(&p.tab.id) == active.as_ref()) {
            page.pending_navigation = if url != previous {
                Some((url.clone(), previous, Instant::now()))
            } else { None };
            page.tab.url = url.clone();
            page.tab.title = if url == "about:blank" {
                "New tab".to_owned()
            } else {
                "Loading page...".to_owned()
            };
        }
        self.status = if url == "about:blank" {
            "Obscura connected · approved sites only".to_owned()
        } else { "Loading approved website".to_owned() };
    }
    async fn navigate(&mut self, url: String) -> io::Result<()> {
        let url = self.guard(&url)?;
        let previous = self.selected()?.tab.url.clone();
        self.release_screencast().await;
        self.page("Page.navigate", json!({"url":url})).await?;
        self.mark_pending_navigation(url, previous);
        Ok(())
    }
    async fn refresh(&mut self) -> io::Result<()> {
        if self.active.is_none() { return Ok(()); }
        let evaluation = self.page("Runtime.evaluate", json!({
            "expression":r#"(() => {
                var mounted=false, overview=false, detail=false;
                if (location.pathname.startsWith('/workspace')) {
                    var nodes=document.getElementsByTagName('*');
                    for (var i=0; i<nodes.length; i++) {
                        var el=nodes[i];
                        if (el.getAttribute('data-t2-ui')==='workspace') mounted=true;
                        var id=el.getAttribute('data-testid');
                        if (id==='overview-view') overview=true;
                        if (id==='workspace-view') detail=true;
                        if (mounted && (overview || detail)) break;
                    }
                }
                return {url:location.href,title:document.title,
                    ready:document.readyState,t2Mounted:mounted,
                    t2Overview:overview,t2Details:detail};
            })()"#,
            "returnByValue":true
        })).await?;
        let value = &evaluation["result"]["value"];
        let url = value["url"].as_str().unwrap_or("about:blank");
        if url != "about:blank" { self.guard(url)?; }
        let active = self.active.clone();
        let pending = self.pages.iter().find(|p| Some(&p.tab.id) == active.as_ref())
            .and_then(|p| p.pending_navigation.as_ref());
        if pending.is_some_and(|(wanted, old, started)|
            url == old && url != wanted && started.elapsed() < Duration::from_secs(25)) {
            return Ok(());
        }
        let title = value["title"].as_str().filter(|s| !s.is_empty()).unwrap_or(url);
        let history = self.page("Page.getNavigationHistory", json!({})).await?;
        let index = history["currentIndex"].as_u64().unwrap_or(0) as usize;
        let total = history["entries"].as_array().map(|x| x.len()).unwrap_or(0);
        let id = self.active.clone();
        let mut navigation_changed = false;
        if let Some(p) = self.pages.iter_mut().find(|x| Some(&x.tab.id) == id.as_ref()) {
            navigation_changed = p.tab.url != url;
            p.tab.url = url.to_owned();
            p.tab.title = title.chars().take(120).collect();
            p.tab.back = index > 0;
            p.tab.forward = index + 1 < total;
            p.pending_navigation = None;
        }
        self.status = if url == "about:blank" {
            "Obscura connected · approved sites only".into()
        } else if is_preview_workspace(url) {
            // HTML readiness is not T2 readiness. Do not show a green
            // "ready" indicator until the canonical Host actually mounted.
            if value["t2Overview"] == true {
                "Workspace ready · overview".into()
            } else if value["t2Details"] == true {
                "Workspace ready · record".into()
            } else if value["t2Mounted"] == true {
                "Workspace Host mounted · loading view".into()
            } else {
                "Workspace initializing · reload if it stays blank".into()
            }
        } else if value["ready"] == "complete" {
            "Page ready · approved origin".into()
        } else { "Loading approved website".into() };
        if navigation_changed && !self.recovering_tabs {
            self.save_tab_session()?;
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
        let destination = self.guard(url)?;
        let entry = item["id"].as_u64().ok_or_else(|| denied("History ID missing"))?;
        let previous = self.selected()?.tab.url.clone();
        self.page("Page.navigateToHistoryEntry",json!({"entryId":entry})).await?;
        // History navigation can be asynchronous for HTTPS pages. Do not
        // immediately query and re-save the previous document as the target.
        self.mark_pending_navigation(destination, previous);
        Ok(())
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
            self.stream_session = None;
            self.stream_latest = None;
            self.stream_ack_ids.clear();
            self.status = "Owned Obscura process stopped; use Restart".into();
            return Err(denied("Obscura process stopped; restart owned engine to recover"));
        }
        Ok(())
    }

    pub async fn execute(&mut self,action:Action)->io::Result<View> {
        // Keep a declined confirmation visible until the next meaningful
        // user operation. Capture ticks, moves and debug probes do not clear it.
        let clear_denial = match &action {
            Action::Capture | Action::Move { .. } | Action::Resize { .. } => false,
            #[cfg(debug_assertions)]
            Action::ProbeWorkspace => false,
            _ => true,
        };
        if clear_denial { self.confirmation_unavailable = false; }
        let persist = matches!(&action,
            Action::Boot | Action::Restart | Action::NewTab{..} |
            Action::OpenSite{..} | Action::ResetSites | Action::SelectTab{..} | Action::CloseTab{..} | Action::Navigate{..} |
            Action::Back | Action::Forward | Action::Reload);
        match action {
            Action::Boot => {
                self.start().await?;
                if self.pages.is_empty() {
                    self.recovering_tabs = true;
                    let result = self.reopen_saved_tabs().await;
                    self.recovering_tabs = false;
                    result?;
                }
            }
            Action::Restart => self.restart_owned().await?,
            other => {
                self.check_owned_process()?;
                if self.connection.is_none() { return Err(denied("Obscura must be started first")); }
                match other {
                    Action::Capture => {
                        if self.active.is_none() { return Ok(self.view(None)); }
                        let frame = self.poll_screencast().await?;
                        return Ok(self.view(frame));
                    }
                    #[cfg(debug_assertions)]
                    Action::ProbeWorkspace => {
                        if self.fixture_mode { return Err(denied("No probe in fixture mode")); }
                        let selected_url=self.selected()?.tab.url.clone();
                        let page_url=Url::parse(&selected_url)
                            .map_err(|_| denied("Invalid tab URL"))?;
                        let known = session::PREVIEW_SITES.iter()
                            .any(|(_, origin)| page_url.origin().ascii_serialization() == *origin);
                        let launch_ui=is_artbazi_launch_navigator(&selected_url);
                        if !known || (!page_url.path().starts_with("/workspace") && !launch_ui) {
                            return Err(denied("Debug probe requires a fixed approved public page"));
                        }
                        let expression=if launch_ui { ARTBAZI_LAUNCH_PROBE } else { WORKSPACE_CONFIG_PROBE };
                        let result = self.page("Runtime.evaluate", json!({
                            "expression":expression,
                            "returnByValue":true
                        })).await?;
                        // Fixed allowlisted endpoints only: no credentials, URLs
                        // with query parameters, page contents or user Results.
                        eprintln!("IWXA_BROWSER_WORKSPACE_PROBE {}", json!({
                            "site": if page_url.host_str() == Some("learn.iwxa.ru") {
                                "learn"
                            } else { "artbazi" },
                            "result":result["result"]["value"],
                            "exception": !result["exceptionDetails"].is_null()
                        }));
                    }
                    Action::NewTab{url} => self.spawn_tab(url).await?,
                    Action::OpenSite{site,url} => {
                        if self.fixture_mode {
                            return Err(denied("Public site picker disabled in fixture mode"));
                        }
                        // Validate before granting: the UI may pass a deep link,
                        // but only under the exact compiled HTTPS origin.
                        let destination = validated_preview_destination(&site, url.as_deref())?;
                        let origin = session::preview_site_origin(&site)
                            .ok_or_else(|| denied("Unknown public preview site"))?;
                        self.granted_sites.insert(site);
                        self.allowed.insert(origin.to_owned());
                        if self.active.is_some() { self.navigate(destination).await?; }
                        else { self.spawn_tab(destination).await?; }
                    }
                    Action::ResetSites => {
                        if self.fixture_mode {
                            return Err(denied("Public site picker disabled in fixture mode"));
                        }
                        self.release_screencast().await;
                        let targets = self.pages.iter().map(|p| p.target.clone()).collect::<Vec<_>>();
                        for target in targets {
                            self.cdp("Target.closeTarget", json!({"targetId":target}), None).await?;
                        }
                        self.pages.clear();
                        self.active = None;
                        self.granted_sites.clear();
                        for (_, origin) in session::PREVIEW_SITES {
                            self.allowed.remove(*origin);
                        }
                        self.spawn_tab("about:blank".to_owned()).await?;
                    }
                    Action::SelectTab{id} => {
                        if !self.pages.iter().any(|p| p.tab.id==id) { return Err(denied("Unknown tab")); }
                        self.release_screencast().await;
                        self.active=Some(id);
                        self.resize().await?;
                        if !self.load_active_deferred().await? {
                            self.refresh().await?;
                        }
                    }
                    Action::CloseTab{id} => {
                        let index=self.pages.iter().position(|p| p.tab.id==id).ok_or_else(|| denied("Unknown tab"))?;
                        let removed=self.pages.remove(index);
                        if self.stream_session.as_deref() == Some(removed.session.as_str()) {
                            self.release_screencast().await;
                        }
                        self.cdp("Target.closeTarget",json!({"targetId":removed.target}),None).await?;
                        if self.active.as_deref()==Some(id.as_str()) {
                            self.active=self.pages.last().map(|p| p.tab.id.clone());
                        }
                        if self.pages.is_empty() { self.spawn_tab("about:blank".into()).await?; }
                        else {
                            self.resize().await?;
                            self.load_active_deferred().await?;
                        }
                    }
                    Action::Navigate{url} => self.navigate(url).await?,
                    Action::Back => {
                        self.release_screencast().await;
                        self.history(-1).await?
                    }
                    Action::Forward => {
                        self.release_screencast().await;
                        self.history(1).await?
                    }
                    Action::Reload => {
                        let current=self.selected()?.tab.url.clone();
                        self.guard(&current)?;
                        self.release_screencast().await;
                        self.page("Page.reload",json!({})).await?;
                        self.refresh().await?;
                    }
                    Action::Resize{width,height} => {
                        if !(160..=4096).contains(&width) || !(160..=4096).contains(&height) {
                            return Err(denied("Invalid viewport size"));
                        }
                        if self.width != width || self.height != height {
                            self.release_screencast().await;
                            self.width=width;self.height=height;
                            self.resize().await?;
                        }
                    }
                    Action::Click{x,y} => {
                        self.check_point(x,y)?;
                        for t in ["mousePressed","mouseReleased"] {
                            self.page("Input.dispatchMouseEvent",json!({
                                "type":t,"x":x,"y":y,"button":"left","clickCount":1
                            })).await?;
                        }
                    }
                    Action::Move{x,y} => {
                        self.check_point(x,y)?;
                        self.page("Input.dispatchMouseEvent",json!({
                            "type":"mouseMoved","x":x,"y":y
                        })).await?;
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
                        if let Some(script) = editing_key_default(&key, modifiers) {
                            // Read-only desktop key events alone do not move
                            // focus in upstream Obscura. Apply precisely the
                            // missing user-requested default on that page.
                            self.page("Runtime.evaluate", json!({
                                "expression":script,"returnByValue":false
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
        if persist && !self.recovering_tabs {
            self.save_tab_session()?;
        }
        Ok(self.view(None))
    }
}
const UNSUPPORTED_CONFIRM_NOTICE: &str =
    "Obscura window.confirm: native dialog unavailable; action declined";

fn is_unavailable_confirmation_notice(message: &Value, active_session: &str) -> bool {
    if message["method"] != "Runtime.consoleAPICalled"
        || message["sessionId"].as_str() != Some(active_session)
        || !matches!(message["params"]["type"].as_str(), Some("warn" | "warning")) {
        return false;
    }
    message["params"]["args"].as_array().is_some_and(|args| {
        args.len() == 1 && args[0]["type"] == "string"
            && args[0]["value"].as_str() == Some(UNSUPPORTED_CONFIRM_NOTICE)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test(flavor = "current_thread")]
    async fn cdp_deadline_cannot_be_extended_by_unrelated_events() {
        // In-process protocol peer only. The peer keeps publishing valid
        // unsolicited events instead of responding to the requested command.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            let request = socket.next().await.unwrap().unwrap();
            assert!(request.is_text());
            for n in 0..100_u32 {
                let event = json!({"method":"Network.dataReceived",
                    "params":{"dataLength":n}}).to_string();
                if socket.send(Message::Text(event.into())).await.is_err() { break; }
                sleep(Duration::from_millis(3)).await;
            }
        });
        let (socket, _) = connect_async(format!("ws://{address}")).await.unwrap();
        let mut controller = Controller::default();
        controller.connection = Some(socket);
        let started = Instant::now();
        let error = controller.cdp_with_limit(
            "Runtime.evaluate", json!({"expression":"1+1"}), None,
            Duration::from_millis(70)
        ).await.expect_err("a notification flood must not renew the CDP deadline");
        assert!(error.to_string().contains("response timed out"), "{error}");
        assert!(started.elapsed() < Duration::from_millis(250),
            "the absolute deadline should not be extended by protocol events");
        peer.abort();
    }

    #[tokio::test(flavor = "current_thread")]
    async fn cdp_deadline_accepts_matching_reply_after_unrelated_event() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(stream).await.unwrap();
            let request = socket.next().await.unwrap().unwrap();
            let id: u64 = serde_json::from_str::<Value>(request.to_text().unwrap())
                .unwrap()["id"].as_u64().unwrap();
            socket.send(Message::Text(json!({
                "method":"Network.loadingFinished","params":{"requestId":"ignored"}
            }).to_string().into())).await.unwrap();
            socket.send(Message::Text(json!({
                "id":id,"result":{"value":"ok"}
            }).to_string().into())).await.unwrap();
        });
        let (socket, _) = connect_async(format!("ws://{address}")).await.unwrap();
        let mut controller = Controller::default();
        controller.connection = Some(socket);
        let value = controller.cdp_with_limit(
            "Runtime.evaluate", json!({}), None, Duration::from_secs(1)
        ).await.expect("matching response should not be lost");
        assert_eq!(value["value"], "ok");
        peer.await.unwrap();
    }

    #[test]
    fn unsupported_confirmation_is_visible_only_in_the_active_session() {
        let message = json!({
            "method": "Runtime.consoleAPICalled",
            "sessionId": "session-own",
            "params": {"type": "warn", "args": [{
                "type": "string", "value": UNSUPPORTED_CONFIRM_NOTICE
            }]}
        });
        assert!(is_unavailable_confirmation_notice(&message, "session-own"));
        assert!(!is_unavailable_confirmation_notice(&message, "session-other"));
        let mut forged = message.clone();
        forged["params"]["args"][0]["value"] = json!("Approve the operation");
        assert!(!is_unavailable_confirmation_notice(&forged, "session-own"));
        forged = message.clone();
        forged["method"] = json!("Page.screencastFrame");
        assert!(!is_unavailable_confirmation_notice(&forged, "session-own"));
        forged = message.clone();
        forged["params"]["args"].as_array_mut().unwrap().push(json!({
            "type": "string", "value": "second argument"
        }));
        assert!(!is_unavailable_confirmation_notice(&forged, "session-own"));
    }
    #[test]
    fn packaged_engine_integrity_check_reads_multiple_bounded_chunks() {
        let file = std::env::temp_dir().join(format!("iwxa-engine-digest-{}", Uuid::new_v4()));
        let data = vec![0x5au8; 2 * 64 * 1024 + 19];
        fs::write(&file, &data).unwrap();
        let digest = hex::encode(Sha256::digest(&data));
        assert_eq!(verified_digest(&file, &digest).unwrap(), fs::canonicalize(&file).unwrap());
        assert!(verified_digest(&file, &"0".repeat(64)).is_err());
        assert!(verified_digest(&file, "short").is_err());
        fs::remove_file(file).unwrap();
    }
    #[test]
    fn editing_shortcuts_restrict_native_default_actions() {
        assert!(editing_key_default("Tab", 0).unwrap().ends_with("(false)"));
        assert!(editing_key_default("Tab", 8).unwrap().ends_with("(true)"));
        assert!(editing_key_default("a", 2).is_some());
        assert!(editing_key_default("A", 4).is_some());
        for (key,modifiers) in [("Tab", 2),("Tab", 1),("a", 0),("a", 1),("l", 2)] {
            assert!(editing_key_default(key, modifiers).is_none());
        }
    }
    #[test]
    fn workspace_ready_is_exactly_scoped_to_the_two_public_site_origins() {
        assert!(is_preview_workspace("https://learn.iwxa.ru/workspace/"));
        assert!(is_preview_workspace("https://artbazi.ru/workspace/#/overview"));
        assert!(!is_preview_workspace("https://learn.iwxa.ru.evil.example/workspace/"));
        assert!(!is_preview_workspace("http://learn.iwxa.ru/workspace/"));
        assert!(!is_preview_workspace("https://unknown.example/workspace/"));
        assert!(!is_preview_workspace("https://learn.iwxa.ru/"));
        assert!(!is_preview_workspace("about:blank"));
    }
    #[test]
    fn artbazi_launch_probe_rejects_unrelated_origins_and_paths() {
        assert!(is_artbazi_launch_navigator("https://artbazi.ru/services/launch-navigator/"));
        assert!(is_artbazi_launch_navigator("https://artbazi.ru/services/launch-navigator"));
        for url in [
            "https://learn.iwxa.ru/services/launch-navigator/",
            "https://artbazi.ru.evil.example/services/launch-navigator/",
            "http://artbazi.ru/services/launch-navigator/",
            "https://artbazi.ru/services/launch-navigator/other",
            "https://artbazi.ru/services/launch-navigator-malicious/",
            "about:blank",
        ] { assert!(!is_artbazi_launch_navigator(url), "{url}"); }
    }
    #[test]
    fn cdp_screencast_rejects_wrong_session_and_oversized_frames() {
        let mut controller=Controller::default();
        controller.stream_session=Some("page-1".into());
        let wrong=json!({"sessionId":"page-2","params":{"sessionId":7,"data":"iVBORw0KGgo="}});
        controller.ingest_screencast_frame(&wrong).unwrap();
        assert!(controller.stream_latest.is_none());
        let invalid=json!({"sessionId":"page-1","params":{"sessionId":7,"data":"bogus"}});
        assert!(controller.ingest_screencast_frame(&invalid).is_err());
        let valid=json!({"sessionId":"page-1","params":{"sessionId":9,"data":"iVBORw0KGgo="}});
        controller.ingest_screencast_frame(&valid).unwrap();
        assert_eq!(controller.stream_latest.as_deref(),Some("iVBORw0KGgo="));
        assert_eq!(controller.stream_ack_ids,[9]);
    }

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
    fn restored_tabs_drop_ungranted_origins_without_losing_safe_selection() {
        let mut c = Controller::default();
        c.allowed = HashSet::from(["https://learn.iwxa.ru".into()]);
        let saved = BrowserSession {
            version: session::SCHEMA_VERSION,
            tabs: vec![
                "https://not-approved.example/path".into(),
                "https://learn.iwxa.ru/workspace/".into(),
                "file:///etc/passwd".into(),
                "about:blank".into(),
            ],
            active: 1,
            granted_sites: Vec::new(),
        };
        let safe = c.admitted_session(saved).unwrap();
        assert_eq!(safe.tabs, [
            "https://learn.iwxa.ru/workspace/".to_string(),
            "about:blank".to_string(),
        ]);
        assert_eq!(safe.active, 0);
    }

    #[test]
    fn restored_tabs_do_not_regrant_expired_fixtures() {
        let mut c = Controller::default();
        c.allowed = HashSet::from(["https://learn.iwxa.ru".into()]);
        let saved = BrowserSession {
            version: session::SCHEMA_VERSION,
            tabs: vec!["http://127.0.0.1:9999/".into()],
            active: 0,
            granted_sites: Vec::new(),
        };
        assert!(c.admitted_session(saved).is_none());
    }

    #[test]
    fn localhost_requires_explicit_fixture_allowance() {
        let allow=HashSet::from(["http://127.0.0.1:8080".into()]);
        assert!(allowed_url("http://127.0.0.1:8080/page",&allow,true).is_ok());
        assert!(allowed_url("http://127.0.0.1:8080/page",&allow,false).is_err());
        assert!(allowed_url("http://127.0.0.1:8090/page",&allow,true).is_err());
    }
    #[test]
    fn typed_approved_site_links_are_validated_natively() {
        assert_eq!(
            validated_preview_destination("learn", Some("https://learn.iwxa.ru/workspace/#results")).unwrap(),
            "https://learn.iwxa.ru/workspace/#results"
        );
        assert_eq!(validated_preview_destination("artbazi", None).unwrap(), "https://artbazi.ru/");
        for (id,raw) in [
            ("learn", "https://evil.example/"),
            ("learn", "https://learn.iwxa.ru.evil.example/"),
            ("learn", "http://learn.iwxa.ru/"),
            ("learn", "file:///etc/passwd"),
            ("learn", "about:blank"),
            ("learn", "https://user:pass@learn.iwxa.ru/"),
            ("missing", "https://learn.iwxa.ru/"),
        ] {
            assert!(validated_preview_destination(id, Some(raw)).is_err(), "{id}:{raw}");
        }
    }
    #[test]
    fn no_environment_grants_required_for_normal_browsing() {
        assert!(parse_grants(None, false).unwrap().is_empty());
        assert!(parse_grants(None, true).is_err());
        assert!(parse_grants(Some("http://127.0.0.1:8123"), true).is_ok());
        assert!(parse_grants(Some("https://learn.iwxa.ru"), true).is_err());
    }
    #[test]
    fn preview_catalog_only_grants_exact_https_origins() {
        assert_eq!(session::preview_site_origin("learn"), Some("https://learn.iwxa.ru"));
        assert_eq!(session::preview_site_origin("artbazi"), Some("https://artbazi.ru"));
        for id in ["www", "https://example.org", "learn.evil", "../learn", ""] {
            assert!(session::preview_site_origin(id).is_none());
        }
        let allowed = HashSet::from(["https://learn.iwxa.ru".to_owned()]);
        assert!(allowed_url("https://learn.iwxa.ru/workspace", &allowed, false).is_ok());
        assert!(allowed_url("https://learn.iwxa.ru.evil.example/", &allowed, false).is_err());
        assert!(allowed_url("https://artbazi.ru/", &allowed, false).is_err());
    }

}
