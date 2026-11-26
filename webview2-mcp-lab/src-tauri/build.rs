fn main(){
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["sync_pane","browser_url","browser_command"])
    )).expect("tauri build failed");
}
