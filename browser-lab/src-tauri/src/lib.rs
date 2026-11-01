mod controller;
use controller::{Action, Controller, View};
use tauri::State;
use tokio::sync::Mutex;

#[tauri::command]
async fn command(action: Action, controller: State<'_, Mutex<Controller>>) -> Result<View, String> {
    let mut owned = controller.lock().await;
    owned.execute(action).await.map_err(|e| e.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .manage(Mutex::new(Controller::default()))
        .invoke_handler(tauri::generate_handler![command])
        .run(tauri::generate_context!())
        .expect("Browser lab failed to start");
}
