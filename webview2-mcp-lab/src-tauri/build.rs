fn main(){
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["sync_pane","browser_url","browser_command","browser_tabs","browser_select_tab","browser_create_tab","browser_close_tab"])
    )).expect("tauri build failed");
}
