// Public compatibility lab. No browser engine, no Agent runtime, no Cherry source.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{path::PathBuf, sync::atomic::{AtomicBool, Ordering}};
use tauri::{Manager, PhysicalPosition, PhysicalSize, Rect, State, Webview, WebviewUrl, Window, WindowEvent};
use tauri::{webview::{NewWindowResponse, WebviewBuilder}, window::WindowBuilder};

struct PaneState(AtomicBool);

fn bounds(x: u32, width: u32, height: u32) -> Rect {
    Rect {
        position: PhysicalPosition::new(x as i32, 0).into(),
        size: PhysicalSize::new(width.max(1), height.max(1)).into(),
    }
}

fn layout(window: &Window, visible: bool) -> tauri::Result<()> {
    let size = window.inner_size()?;
    if size.width < 2 || size.height == 0 { return Ok(()); }
    let shell = window.get_webview("shell").ok_or_else(|| tauri::Error::WebviewNotFound)?;
    let guest = window.get_webview("guest").ok_or_else(|| tauri::Error::WebviewNotFound)?;
    let split = (size.width as u64 * 38 / 100) as u32;
    if visible {
        shell.set_bounds(bounds(0, split, size.height))?;
        guest.set_bounds(bounds(split, size.width - split, size.height))?;
        guest.show()?;
    } else {
        guest.hide()?;
        shell.set_bounds(bounds(0, size.width, size.height))?;
        shell.set_focus()?;
    }
    Ok(())
}

#[tauri::command]
fn set_pane(webview: Webview, state: State<'_, PaneState>, visible: bool) -> Result<(), String> {
    if webview.label() != "shell" { return Err("trusted_shell_required".into()); }
    layout(&webview.window(), visible).map_err(|e| e.to_string())?;
    state.inner().0.store(visible, Ordering::SeqCst);
    Ok(())
}

fn main() {
    // The standalone experiment deliberately accepts only a local fixture, not personal profiles.
    let profile = PathBuf::from(std::env::var_os("IWXA_LAB_PROFILE").expect("IWXA_LAB_PROFILE"));
    assert!(profile.is_absolute(), "absolute lab profile required");
    let entry: tauri::Url = std::env::var("IWXA_LAB_URL").expect("IWXA_LAB_URL").parse().expect("fixture URL");
    assert!(entry.scheme() == "http" && entry.host_str() == Some("127.0.0.1") &&
        entry.port().is_some() && entry.username().is_empty() && entry.password().is_none() &&
        entry.path() == "/form", "local fixture only");
    let origin = entry.origin();
    let shell_data = profile.join("shell");
    let guest_data = profile.join("guest");
    std::fs::create_dir_all(&shell_data).expect("shell profile");
    std::fs::create_dir_all(&guest_data).expect("guest profile");

    tauri::Builder::default()
        .manage(PaneState(AtomicBool::new(true)))
        .invoke_handler(tauri::generate_handler![set_pane])
        .setup(move |app| {
            let window = WindowBuilder::new(app, "lab")
                .title("IWXA WebView2 MCP Lab")
                .inner_size(1040.0, 680.0)
                .min_inner_size(640.0, 420.0)
                .visible(false)
                .center()
                .build()?;
            let size = window.inner_size()?;
            let split = (size.width as u64 * 38 / 100) as u32;
            // Create both native children in setup on the UI thread.
            window.add_child(
                WebviewBuilder::new("shell", WebviewUrl::App("index.html".into()))
                    .data_directory(shell_data.clone())
                    .devtools(false)
                    .on_new_window(|_, _| NewWindowResponse::Deny),
                PhysicalPosition::new(0, 0),
                PhysicalSize::new(split.max(1), size.height.max(1)),
            )?;
            let fixture_origin = origin.clone();
            window.add_child(
                WebviewBuilder::new("guest", WebviewUrl::External(entry.clone()))
                    .data_directory(guest_data.clone())
                    .additional_browser_args("--remote-debugging-port=0 --remote-debugging-address=127.0.0.1 --no-first-run")
                    .devtools(false)
                    .on_navigation(move |url| url.origin() == fixture_origin)
                    .on_new_window(|_, _| NewWindowResponse::Deny),
                PhysicalPosition::new(split as i32, 0),
                PhysicalSize::new((size.width-split).max(1), size.height.max(1)),
            )?;
            let owner = window.clone();
            window.on_window_event(move |event| {
                if matches!(event, WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. }) {
                    let visible = owner.state::<PaneState>().inner().0.load(Ordering::SeqCst);
                    if let Err(error) = layout(&owner, visible) {
                        eprintln!("LAB_LAYOUT_ERROR {error}");
                    }
                }
            });
            window.show()?;
            layout(&window, true)?;
            println!("LAB_READY shell=local guest=external separate_profiles=true");
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("native WebView2 lab failed");
}
