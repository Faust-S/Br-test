// Public product-host parity lab. Native pane attaches to an existing Tauri WebviewWindow.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod pane_geometry;
use std::path::PathBuf;
use tauri::{Emitter, Manager, PhysicalPosition, PhysicalSize, Rect, State, Webview, WebviewUrl, WebviewWindowBuilder};
use tauri::{ipc::CapabilityBuilder, webview::{DownloadEvent, NewWindowResponse, WebviewBuilder}};

struct PaneState { fixture_origin:String, shell_url:Option<tauri::Url>, public_sites:bool }

// The public smoke test uses a fresh lab profile and only these public origins.
// Production browser navigation is not constrained to this qualification list.
const PUBLIC_HOSTS:&[&str]=&["github.com","iwxa.ru","www.iwxa.ru","learn.iwxa.ru","artbazi.ru","www.artbazi.ru"];

fn browsing_destination_allowed(fixture_origin:&str,public_sites:bool,url:&tauri::Url)->bool {
    if !url.username().is_empty() || url.password().is_some() { return false; }
    if url.origin().ascii_serialization()==fixture_origin { return true; }
    public_sites && url.scheme()=="https" && url.port().is_none() &&
        url.host_str().is_some_and(|host|PUBLIC_HOSTS.contains(&host))
}

// HTTP IPC carries an Origin header without the document path.
fn shell_location_allowed(expected:Option<&tauri::Url>, actual:&tauri::Url)->bool {
    if let Some(expected)=expected { return actual==expected; }
    ((matches!(actual.scheme(),"http"|"https") && actual.host_str()==Some("tauri.localhost")) ||
        (actual.scheme()=="tauri" && actual.host_str()==Some("localhost"))) &&
        matches!(actual.path(),"/"|"/index.html") &&
        actual.port().is_none() && actual.username().is_empty() && actual.password().is_none() &&
        actual.query().is_none() && actual.fragment().is_none()
}

#[cfg(test)]
mod shell_tests {
    use super::*;
    #[test]
    fn exact_remote_shell_document() {
        let expected:tauri::Url="http://127.0.0.1:12345/desktop/index.html".parse().unwrap();
        assert!(shell_location_allowed(Some(&expected),&expected));
        for url in ["http://127.0.0.1:12345/desktop/other.html",
            "http://127.0.0.1:12345/desktop/index.html?unexpected",
            "http://127.0.0.1:12346/desktop/index.html",
            "https://127.0.0.1:12345/desktop/index.html",
            "http://127.0.0.1:12345/",
            "http://tauri.localhost/"] {
            assert!(!shell_location_allowed(Some(&expected),&url.parse().unwrap()),"{url}");
        }
    }
    #[test]
    fn public_smoke_destinations_are_explicit_and_not_shell() {
        let fixture="http://127.0.0.1:12345";
        let public:tauri::Url="https://github.com/ChromeDevTools/chrome-devtools-mcp".parse().unwrap();
        assert!(!browsing_destination_allowed(fixture,false,&public));
        assert!(browsing_destination_allowed(fixture,true,&public));
        assert!(browsing_destination_allowed(fixture,false,&"http://127.0.0.1:12345/form".parse().unwrap()));
        for url in ["http://github.com/","https://github.com.evil/","https://user@github.com/",
            "https://github.com:444/","http://127.0.0.1:12346/desktop/index.html","file:///C:/test"] {
            assert!(!browsing_destination_allowed(fixture,true,&url.parse().unwrap()),"{url}");
        }
    }
    #[test]
    fn bundled_shell_document_only() {
        for url in ["http://tauri.localhost/","https://tauri.localhost/index.html","tauri://localhost/"] {
            assert!(shell_location_allowed(None,&url.parse().unwrap()),"{url}");
        }
        for url in ["http://tauri.localhost.evil/","http://tauri.localhost/other","http://tauri.localhost:1234/","http://127.0.0.1/"] {
            assert!(!shell_location_allowed(None,&url.parse().unwrap()),"{url}");
        }
    }
}

fn trusted_guest(webview:&Webview) -> Result<Webview,String> {
    if webview.label()!="shell" { return Err("trusted_shell_required".into()); }
    let actual=webview.url().map_err(|e|e.to_string())?;
    let state=webview.state::<PaneState>();
    if !shell_location_allowed(state.shell_url.as_ref(),&actual) { return Err("trusted_shell_document_required".into()); }
    webview.window().get_webview("guest").ok_or_else(||"browser_guest_unavailable".into())
}

#[tauri::command]
fn sync_pane(webview: Webview, visible:bool, left:f64, top:f64, right:f64, bottom:f64,
    viewport_width:f64, viewport_height:f64) -> Result<(),String> {
    let guest=trusted_guest(&webview)?;
    if !visible {
        guest.hide().map_err(|e|e.to_string())?;
        println!("LAB_PANE_HIDDEN");
        return Ok(());
    }
    let size=webview.window().inner_size().map_err(|e|e.to_string())?;
    let bounds=match pane_geometry::fit(size.width,size.height,viewport_width,viewport_height,left,top,right,bottom) {
        Some(value)=>value,
        None=>{ guest.hide().map_err(|e|e.to_string())?;return Err("invalid_pane_geometry".into()); }
    };
    guest.set_bounds(Rect{
        position:PhysicalPosition::new(bounds.x as i32,bounds.y as i32).into(),
        size:PhysicalSize::new(bounds.width,bounds.height).into()
    }).map_err(|e|e.to_string())?;
    guest.show().map_err(|e|e.to_string())?;
    println!("LAB_PANE_BOUNDS {} {} {} {}",bounds.x,bounds.y,bounds.width,bounds.height);
    Ok(())
}

#[tauri::command]
fn browser_url(webview:Webview)->Result<String,String>{
    trusted_guest(&webview)?.url().map(|u|u.to_string()).map_err(|e|e.to_string())
}

#[tauri::command]
fn browser_command(webview:Webview,state:State<'_,PaneState>,operation:String,address:Option<String>)->Result<(),String>{
    let guest=trusted_guest(&webview)?;
    match operation.as_str(){
        "navigate"=>{
            let url:tauri::Url=address.ok_or("address_required")?.parse().map_err(|_|"invalid_address")?;
            if !browsing_destination_allowed(&state.fixture_origin,state.public_sites,&url){return Err("outside_lab_origin".into());}
            guest.navigate(url).map_err(|e|e.to_string())
        },
        "reload"=>guest.reload().map_err(|e|e.to_string()),
        "back"|"forward"=>{
            let backward=operation=="back";
            #[cfg(windows)]
            {
                let shell=webview.clone();
                guest.with_webview(move|platform|{
                    let result=unsafe{platform.controller().CoreWebView2().and_then(|core|{
                        if backward {core.GoBack()} else {core.GoForward()}
                    })};
                    if let Err(error)=result {
                        let _=shell.emit("lab-browser-error",error.to_string());
                    }
                }).map_err(|e|e.to_string())
            }
            #[cfg(not(windows))]
            {let _=backward;Err("windows_native_history_required".into())}
        },
        _=>Err("unknown_browser_operation".into())
    }
}

fn main(){
    let profile=PathBuf::from(std::env::var_os("IWXA_LAB_PROFILE").expect("IWXA_LAB_PROFILE"));
    assert!(profile.is_absolute(),"absolute lab profile required");
    let entry:tauri::Url=std::env::var("IWXA_LAB_URL").expect("IWXA_LAB_URL").parse().expect("fixture URL");
    assert!(entry.scheme()=="http" && entry.host_str()==Some("127.0.0.1") && entry.port().is_some() &&
        entry.username().is_empty() && entry.password().is_none() && entry.path()=="/form","local fixture only");
    let origin=entry.origin();
    let public_sites=std::env::var("IWXA_LAB_PUBLIC_SITES").ok().as_deref()==Some("1");
    let remote_shell:Option<tauri::Url>=std::env::var("IWXA_LAB_SHELL_URL").ok().map(|s|s.parse().expect("shell URL"));
    if let Some(url)=&remote_shell{
        assert!(url.scheme()=="http" && url.host_str()==Some("127.0.0.1") && url.port().is_some() &&
            url.username().is_empty() && url.password().is_none() && url.path()=="/desktop/index.html" &&
            url.origin()!=origin,"trusted shell must have its own loopback origin");
    }
    // Consume the explicit shell profile setting before creating any WebView2.
    // A global WebView2 override would otherwise affect the guest as well.
    let shell_data=std::env::var_os("WEBVIEW2_USER_DATA_FOLDER").filter(|s|!s.is_empty())
        .map(PathBuf::from).unwrap_or_else(||profile.join("shell"));
    std::env::remove_var("WEBVIEW2_USER_DATA_FOLDER");
    assert!(std::env::var_os("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS").is_none(),"global browser arguments are not permitted in the lab");
    let guest_data=profile.join("guest");
    assert!(shell_data.is_absolute(),"absolute shell profile required");
    std::fs::create_dir_all(&shell_data).expect("shell profile");
    std::fs::create_dir_all(&guest_data).expect("guest profile");
    let shell_real=shell_data.canonicalize().expect("canonical shell profile");
    let guest_real=guest_data.canonicalize().expect("canonical guest profile");
    let install=std::env::current_exe().expect("exe").parent().expect("exe parent").canonicalize().expect("install path");
    assert!(!shell_real.starts_with(&guest_real) && !guest_real.starts_with(&shell_real) &&
        !shell_real.starts_with(&install) && !guest_real.starts_with(&install),"distinct profiles outside install directory required");
    let downloads=profile.join("downloads");
    std::fs::create_dir_all(&downloads).expect("lab download directory");

    tauri::Builder::default()
        .manage(PaneState{fixture_origin:origin.ascii_serialization(),shell_url:remote_shell.clone(),public_sites})
        .invoke_handler(tauri::generate_handler![sync_pane,browser_url,browser_command])
        .setup(move|app|{
            let shell_url=if let Some(url)=&remote_shell{
                app.add_capability(CapabilityBuilder::new("lab-trusted-remote-shell")
                    .local(false).webview("shell").remote(format!("{}/*",url.origin().ascii_serialization()))
                    .permission("core:event:allow-listen").permission("core:event:allow-unlisten").permission("allow-sync-pane").permission("allow-browser-url").permission("allow-browser-command"))?;
                WebviewUrl::External(url.clone())
            }else{WebviewUrl::App("index.html".into())};
            let expected_shell=remote_shell.clone();
            let shell=WebviewWindowBuilder::new(app,"shell",shell_url)
                .title("IWXA WebView2 MCP Lab").inner_size(1040.0,680.0).min_inner_size(640.0,420.0)
                .data_directory(shell_data.clone()).devtools(false).visible(false).center()
                .on_navigation(move|url|shell_location_allowed(expected_shell.as_ref(),url))
                .on_page_load(|view,payload|{
                    println!("LAB_SHELL_PAGE {:?} {}",payload.event(),payload.url());
                    let _=view.eval_with_callback(
                        r#"JSON.stringify({ready:document.readyState,api:typeof window.__TAURI__,title:document.title,status:document.getElementById('status')?.textContent||'',href:location.href})"#,
                        |result|println!("LAB_SHELL_DOCUMENT {result}"));
                })
                .on_new_window(|_,_|NewWindowResponse::Deny).build()?;
            let window=shell.as_ref().window();
            let fixture_origin=origin.ascii_serialization();
            let download_origin=origin.clone();
            let download_path=downloads.join("iwxa-result.txt");
            let guest=window.add_child(
                WebviewBuilder::new("guest",WebviewUrl::External(entry.clone()))
                    .data_directory(guest_data.clone())
                    .additional_browser_args("--remote-debugging-port=0 --remote-debugging-address=127.0.0.1 --no-first-run")
                    .devtools(false)
                    .on_navigation(move|url|browsing_destination_allowed(&fixture_origin,public_sites,url))
                    .on_page_load(|view,payload|{
                        let url=payload.url().to_string();
                        let _=view.emit_to("shell","lab-browser-url",url);
                    })
                    .on_download(move|_,event|match event{
                        DownloadEvent::Requested{url,destination}=>{
                            if url.origin()!=download_origin || url.path()!="/download" || download_path.exists(){return false;}
                            *destination=download_path.clone();println!("LAB_DOWNLOAD_REQUESTED");true
                        },
                        DownloadEvent::Finished{success,..}=>{println!("LAB_DOWNLOAD_FINISHED success={success}");true},
                        _=>false,
                    })
                    .on_new_window(|_,_|NewWindowResponse::Deny),
                PhysicalPosition::new(0,0),PhysicalSize::new(1,1)
            )?;
            guest.hide()?;
            shell.show()?;
            println!("LAB_READY shell=WebviewWindow guest=child separate_profiles=true");
            Ok(())
        })
        .run(tauri::generate_context!()).expect("native WebView2 dock failed");
}
