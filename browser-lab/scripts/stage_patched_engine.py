"""Stage a public patched Obscura for a Tauri installer, never commit binaries.

Usage: OBSCURA_PATCHED_BINARY=/absolute/path/to/binary python
       browser-lab/scripts/stage_patched_engine.py

The Rust build script embeds the staged binary SHA-256. Release mode refuses an
arbitrary OBSCURA_LAB_BINARY override. This digest is tamper detection and does
not replace signed release distribution or OS sandboxing.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

REPO=Path(__file__).resolve().parents[2]
STAGE=REPO/"browser-lab"/"src-tauri"/"resources"/"obscura"
UPSTREAM="8534b401898ed17b62f69bdc3d5eb186bffb0d60"

def main():
    raw=os.environ.get("OBSCURA_PATCHED_BINARY")
    if not raw:
        raise RuntimeError("OBSCURA_PATCHED_BINARY must identify the locally built patched Obscura")
    source=Path(raw).resolve(strict=True)
    if not source.is_file():raise RuntimeError("Patched engine path is not a file")
    expected="obscura.exe" if os.name=="nt" else "obscura"
    if source.name.lower()!=expected.lower():
        raise RuntimeError(f"Not the expected native Obscura executable: {source.name}")
    STAGE.mkdir(parents=True,exist_ok=True)
    for target in STAGE.iterdir():
        if target.is_file():target.unlink()
    target=STAGE/expected
    shutil.copyfile(source,target)
    sha=hashlib.sha256(target.read_bytes()).hexdigest()
    if os.name!="nt":target.chmod(0o755)
    (STAGE/"obscura.sha256").write_text(sha+"\n",encoding="ascii")
    (STAGE/"provenance.json").write_text(json.dumps({
        "source":"https://github.com/h4ckf0r0day/obscura",
        "upstream_commit":UPSTREAM,
        "engine_version":"0.2.6",
        "public_overrides":"engine-overrides/obscura-v0.2.6",
        "sha256":sha,
        "note":"experimental public engine, no signing or hostile-site sandbox acceptance"
    },indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"staged":str(target),"sha256":sha,"upstream":UPSTREAM}))
if __name__=="__main__":main()
