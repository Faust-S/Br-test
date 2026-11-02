mod controller;
mod session;
use controller::{Action, Controller, View};
use tauri::{Manager, State};
use tokio::sync::Mutex;
use tauri::ipc::Response;

#[tauri::command]
async fn command(action: Action, controller: State<'_, Mutex<Controller>>) -> Result<View, String> {
    let mut owned = controller.lock().await;
    owned.execute(action).await.map_err(|e| e.to_string())
}

/// Raw bytes are returned as an ArrayBuffer, avoiding a second JSON/base64
/// copy through React. Empty bytes mean no damaged frame is available.
#[tauri::command]
async fn capture_frame(controller: State<'_, Mutex<Controller>>) -> Result<Response, String> {
    let mut owned = controller.lock().await;
    let png = owned.capture_png().await.map_err(|e| e.to_string())?;
    Ok(Response::new(png))
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let resource_dir = app.path().resource_dir().ok();
            app.manage(Mutex::new(Controller::with_resource_dir(resource_dir)));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![command, capture_frame])
        .build(tauri::generate_context!())
        .expect("Browser lab failed to build")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                // Tauri may call process::exit without dropping managed State.
                // Terminate and reap only the Obscura child this controller
                // originally spawned, even when the user closes the OS window.
                if let Some(state) = app.try_state::<Mutex<Controller>>() {
                    let mut owner = state.blocking_lock();
                    owner.terminate_on_exit();
                }
            }
        });
}
