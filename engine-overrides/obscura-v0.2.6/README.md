# Public IndexedDB persistence patch for Obscura v0.2.6

Pinned upstream: `h4ckf0r0day/obscura@8534b401898ed17b62f69bdc3d5eb186bffb0d60` (Apache-2.0). This is an openly developed engine experiment, not private IWXA source code.

Changes are limited to the existing native IndexedDB service and browser context initialization. Commits stage all object stores before publishing, write a temporary snapshot to the *existing engine profile* directory, sync the file, atomically rename, then publish the memory state. Deletes persist before success. Incomplete transactions and aborts do not reach disk; damaged snapshots cause startup to fail closed for IndexedDB. Cross-process profile locking prevents two processes from silently overwriting a shared snapshot.

The public workflow builds patched Obscura and tests a real 3-store commit/abort followed by two new engine processes. This does not create a second Workspace store. It is not T2 acceptance, does not encrypt IndexedDB, and does not yet qualify high-throughput performance, crash windows or the full browser sandbox. Results must be inspected before declaring PASS.

## Third-party notices

The two overridden source files originate from `h4ckf0r0day/obscura` at pinned commit `8534b401898ed17b62f69bdc3d5eb186bffb0d60` (Obscura v0.2.6). Licensed under Apache License, Version 2.0; see [LICENSE](./LICENSE), copied verbatim from upstream.

**Modifications by the IWXA public browser experiment:** `crates/obscura-js/src/indexed_db.rs` adds profile-scoped persisted transaction snapshots and recovery checks; `crates/obscura-browser/src/context.rs` supplies the active profile to the existing IndexedDB service. These modifications are not endorsed by the upstream authors.
