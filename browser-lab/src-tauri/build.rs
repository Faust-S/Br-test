use sha2::{Digest, Sha256};
use std::{fs, path::PathBuf};

fn main() {
    // Resources are generated locally from a pinned Obscura checkout. If
    // present, embed the EXACT engine binary digest into the Rust executable.
    // The manifest file next to the engine is informational, not a trust root.
    let binary = PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").expect("manifest dir"))
        .join("resources").join("obscura")
        .join(if cfg!(windows) { "obscura.exe" } else { "obscura" });
    println!("cargo:rerun-if-changed={}", binary.display());
    if binary.is_file() {
        let bytes = fs::read(&binary).expect("Read staged Obscura binary");
        let sha256 = hex::encode(Sha256::digest(bytes));
        let expected_path = binary.with_file_name("obscura.sha256");
        let expected = fs::read_to_string(&expected_path)
            .expect("Staged Obscura requires a SHA-256 identity file");
        assert_eq!(expected.trim().to_ascii_lowercase(), sha256,
            "Staged Obscura digest does not match");
        println!("cargo:rerun-if-changed={}", expected_path.display());
        println!("cargo:rustc-env=IWXA_BUNDLED_OBSCURA_SHA256={sha256}");
    }
    tauri_build::build();
}
