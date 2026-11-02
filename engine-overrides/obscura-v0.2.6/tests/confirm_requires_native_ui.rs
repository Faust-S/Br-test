//! A site-origin confirm() must not approve any action without a real dialog.
//! The headless v0.2 native engine has no synchronous user-confirmation UI.
use obscura_dom::parse_html;
use obscura_js::runtime::ObscuraJsRuntime;
use serde_json::json;

#[test]
fn native_confirm_requires_user_action() {
    let mut runtime = ObscuraJsRuntime::new();
    runtime.set_dom(parse_html("<!doctype html><html><body></body></html>"));
    runtime.run_page_init();
    let result = runtime.evaluate(
        r#"(() => [
            window.confirm("Transfer Result to T2?"),
            globalThis.confirm("Delete saved workspace?"),
            window.prompt("Not supported", "default")
        ])()"#,
    ).expect("user-dialog fallback should remain callable");
    assert_eq!(result, json!([false, false, null]),
        "headless engine must never silently authorize a user action");
}
