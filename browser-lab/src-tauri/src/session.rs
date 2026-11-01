//! Browser-owned tab/session metadata. NOT an IWXA Result or Workspace store.
//! Only URLs and the selected tab index persist; page data stays in Obscura.
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::{self, Write},
    path::Path,
};
use uuid::Uuid;

const MAX_BYTES: usize = 64 * 1024;
const MAX_TABS: usize = 10;
pub(crate) const SCHEMA_VERSION: u8 = 1;

pub(crate) const PREVIEW_SITES: &[(&str, &str)] = &[
    ("learn", "https://learn.iwxa.ru"),
    ("artbazi", "https://artbazi.ru"),
];

pub(crate) fn preview_site_origin(id: &str) -> Option<&'static str> {
    PREVIEW_SITES.iter().find(|(key, _)| *key == id).map(|(_, origin)| *origin)
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub(crate) struct BrowserSession {
    pub version: u8,
    pub tabs: Vec<String>,
    pub active: usize,
    #[serde(default)]
    pub granted_sites: Vec<String>,
}

fn valid_session(saved: &BrowserSession) -> bool {
    saved.version == SCHEMA_VERSION &&
        !saved.tabs.is_empty() && saved.tabs.len() <= MAX_TABS &&
        saved.active < saved.tabs.len() &&
        saved.tabs.iter().all(|url| url.len() <= 4096) &&
        saved.granted_sites.len() <= PREVIEW_SITES.len() &&
        saved.granted_sites.iter().enumerate().all(|(index, id)|
            preview_site_origin(id).is_some() &&
            !saved.granted_sites[..index].contains(id))
}

pub(crate) fn read(path: &Path) -> Option<BrowserSession> {
    // A profile is mutable user data: fail closed on corruption, oversized
    // input, symlinks, or a schema we don't understand.
    let meta = fs::symlink_metadata(path).ok()?;
    if !meta.file_type().is_file() || meta.len() > MAX_BYTES as u64 {
        return None;
    }
    let bytes = fs::read(path).ok()?;
    if bytes.len() > MAX_BYTES { return None; }
    let saved: BrowserSession = serde_json::from_slice(&bytes).ok()?;
    if !valid_session(&saved) { return None; }
    Some(saved)
}

pub(crate) fn write(path: &Path, saved: &BrowserSession) -> io::Result<()> {
    if !valid_session(saved) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "Invalid browser session"));
    }
    let bytes = serde_json::to_vec(saved)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
    if bytes.len() > MAX_BYTES {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "Oversized browser session"));
    }
    let parent = path.parent().ok_or_else(||
        io::Error::new(io::ErrorKind::InvalidInput, "Session has no parent directory"))?;
    fs::create_dir_all(parent)?;
    let temp = parent.join(format!(".iwxa-tabs-{}.tmp", Uuid::new_v4()));
    let result = (|| {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)] {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temp)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        drop(file);
        // Same-directory atomic replacement: a crash must expose either the
        // previous complete session or this complete session, never partial JSON.
        fs::rename(&temp, path)?;
        #[cfg(unix)] {
            fs::File::open(parent)?.sync_all()?;
        }
        Ok::<(), io::Error>(())
    })();
    if result.is_err() { let _ = fs::remove_file(&temp); }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn replaces_session_atomically_and_preserves_selected_tab() {
        let root = std::env::temp_dir().join(format!("iwxa-session-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let file = root.join("browser-tabs-v1.json");
        assert!(read(&file).is_none());
        let before = BrowserSession {
            version: SCHEMA_VERSION,
            tabs: vec!["about:blank".into(), "https://example.org/".into()],
            active: 1,
            granted_sites: vec!["learn".into()],
        };
        write(&file, &before).unwrap();
        assert_eq!(read(&file), Some(before));
        let after = BrowserSession {
            version: SCHEMA_VERSION, tabs: vec!["about:blank".into()], active: 0,
            granted_sites: Vec::new()
        };
        write(&file, &after).unwrap();
        assert_eq!(read(&file), Some(after));
        assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn corrupt_or_oversized_session_is_not_restored() {
        let root = std::env::temp_dir().join(format!("iwxa-session-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let file = root.join("browser-tabs-v1.json");
        fs::write(&file, b"{broken").unwrap();
        assert!(read(&file).is_none());
        fs::write(&file, vec![b'a'; MAX_BYTES + 1]).unwrap();
        assert!(read(&file).is_none());
        let incompatible = BrowserSession {
            version: 255, tabs: vec!["about:blank".into()], active: 0,
            granted_sites: Vec::new()
        };
        assert!(write(&file, &incompatible).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn old_files_are_readable_and_unlisted_sites_cannot_be_persisted() {
        let root = std::env::temp_dir().join(format!("iwxa-grants-{}", Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let file = root.join("browser-tabs-v1.json");
        fs::write(&file, r#"{"version":1,"tabs":["about:blank"],"active":0}"#).unwrap();
        let mut saved = read(&file).unwrap();
        assert!(saved.granted_sites.is_empty());
        saved.granted_sites = vec!["https://unknown.example".into()];
        assert!(write(&file, &saved).is_err());
        saved.granted_sites = vec!["learn".into(), "learn".into()];
        assert!(write(&file, &saved).is_err());
        saved.granted_sites = vec!["artbazi".into()];
        write(&file, &saved).unwrap();
        assert_eq!(read(&file).unwrap().granted_sites, vec!["artbazi"]);
        fs::remove_dir_all(root).unwrap();
    }

}
