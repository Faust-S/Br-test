"""Real installed LibreOffice through existing U01 CliConnection.

Produces a disposable synthetic DOCX/PDF and a source/readback receipt. This is
NOT an installed Agent Result receipt. Never downloads an office program.
"""
from __future__ import annotations

import hashlib
import os
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from iwxa_universal_install.document_operations import (
    convert_to_pdf_via_u01, digest, discover_document_programs,
    make_document_cli_spec, pdf_text_coverage, verify_cold_readback,
)
from iwxa_universal_install.program_connection import CliConnection


def program():
    for name in ("soffice.com", "libreoffice", "soffice"):
        item = shutil.which(name)
        if item and Path(item).resolve().is_file():
            return str(Path(item).resolve())
    return None


def run(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    exe = program()
    if not exe:
        return {"status": "BLOCKED_NOT_INSTALLED" if os.environ.get("IWXA_REQUIRE_INSTALLED_LIBREOFFICE") == "1" else "SKIPPED_NOT_INSTALLED", "program": "LIBREOFFICE", "real_cli": False}
    version_result = subprocess.run([exe, "--version"], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, timeout=10, check=False)
    match = re.search(r"LibreOffice\s+(\d+(?:\.\d+){2,})", version_result.stdout)
    if version_result.returncode or not match:
        return {"status": "BLOCKED_VERSION_NOT_VERIFIED", "real_cli": False}
    version = match.group(1)
    binary_digest = hashlib.sha256(Path(exe).read_bytes()).hexdigest()
    spec = make_document_cli_spec("LIBREOFFICE", exe,
                                  binary_sha256=binary_digest, version_contains=version)
    owner = CliConnection(spec, executable=exe)
    observed = owner.inspect()
    if observed.get("status") != "DISCOVERED" or observed.get("installation_verified") is not True:
        return {"status": "FAIL_U01_INSPECT", "observed_status": observed.get("status")}
    connected = owner.connect()
    if connected.get("status") != "CONNECTED":
        return {"status": "FAIL_U01_CONNECT", "observed_status": connected.get("status")}
    try:
        from docx import Document
        from pypdf import PdfReader
        document = Document()
        document.add_heading("IWXA Office document qualification", level=1)
        document.add_paragraph("Real user scenario, synthetic source data")
        document.add_paragraph("Invoice total 1500 USD")
        source = out / "original.docx"
        document.save(source)
        source_sha = digest(source.read_bytes())
        result = convert_to_pdf_via_u01(owner, source_path=source,
                                        expected_source_sha256=source_sha,
                                        scratch_root=out, approved=True)
        target = out / "converted.pdf"
        with target.open("xb") as writer:
            writer.write(result.output_bytes)
            writer.flush()
        parsed = PdfReader(str(target))
        text = "\n".join(p.extract_text() or "" for p in parsed.pages)
        if "IWXA Office document qualification" not in text or "1500 USD" not in text:
            raise RuntimeError("PDF_TEXT_READBACK_MISMATCH")
        # Cold file readback, not a cached converter observation.
        new_source = source.read_bytes()
        new_output = target.read_bytes()
        if digest(new_source) != source_sha or digest(new_output) != result.output_sha256:
            raise RuntimeError("COLD_READBACK_FAILED")
        return {"status": "PASS", "transport": "U01_CLI_CONNECTION",
                "real_cli": True, "program": "LIBREOFFICE", "program_version": version,
                "program_binary_sha256": binary_digest,
                "source_sha256": source_sha, "output_sha256": result.output_sha256,
                "pages": len(parsed.pages), "text_readback": True,
                "source_preserved": True, "new_file_readback": True,
                "task_result_persisted": False,
                "source_file": source.name, "output_file": target.name,
                "receipt_authority": "QUALIFICATION_ONLY"}
    finally:
        owner.disconnect()


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) == 2 else Path(tempfile.mkdtemp(prefix="iwxa-office-u01-"))
    try:
        report = run(destination)
        (destination / "REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
        sys.exit(0 if report["status"] in ("PASS", "SKIPPED_NOT_INSTALLED") else 1)
    except Exception as exc:
        report = {"status": "FAIL", "reason": type(exc).__name__ + ":" + str(exc)}
        (destination / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
        sys.exit(1)
