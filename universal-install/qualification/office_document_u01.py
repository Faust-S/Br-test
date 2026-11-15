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
    ocr_image_via_u01, ocr_pdf_via_u01,
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


def run_ocr(out: Path) -> dict:
    """Real installed Tesseract U01 roundtrip; no software installation here."""
    from PIL import Image, ImageDraw, ImageFont
    exe = shutil.which('tesseract')
    if not exe or not Path(exe).is_file():
        return {'status': 'BLOCKED_NOT_INSTALLED' if os.environ.get('IWXA_REQUIRE_INSTALLED_TESSERACT') == '1' else 'SKIPPED_NOT_INSTALLED',
                'program': 'TESSERACT', 'real_cli': False}
    exe = str(Path(exe).resolve())
    version = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=10)
    match = re.search(r'tesseract\s+(\d+(?:\.\d+){2,})', version.stdout + version.stderr, flags=re.I)
    if version.returncode != 0 or match is None:
        return {'status': 'BLOCKED_VERSION_NOT_VERIFIED', 'program': 'TESSERACT'}
    binary_sha = hashlib.sha256(Path(exe).read_bytes()).hexdigest()
    spec = make_document_cli_spec('TESSERACT', exe, binary_sha256=binary_sha,
                                  version_contains=match.group(1))
    connection = CliConnection(spec, executable=exe)
    if connection.inspect().get('installation_verified') is not True:
        return {'status': 'FAIL_U01_INSPECT', 'program': 'TESSERACT'}
    if connection.connect().get('status') != 'CONNECTED':
        return {'status': 'FAIL_U01_CONNECT', 'program': 'TESSERACT'}
    try:
        # Guaranteed synthetic data; the recognized file is not a user document.
        image = Image.new('RGB', (1150, 280), 'white')
        draw = ImageDraw.Draw(image)
        font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        font = ImageFont.truetype(font_path, 56) if Path(font_path).is_file() else ImageFont.load_default()
        draw.text((45, 65), 'INVOICE 1500 USD', fill='black', font=font)
        source = out / 'ocr-invoice.png'
        image.save(source, format='PNG')
        source_sha = digest(source.read_bytes())
        result = ocr_image_via_u01(connection, source_path=source,
                                  expected_source_sha256=source_sha,
                                  scratch_root=out, approved=True, language='eng')
        text = result.output_bytes.decode('utf-8-sig')
        if 'INVOICE' not in text.upper() or '1500' not in text:
            raise RuntimeError('OCR_SEMANTIC_READBACK_MISMATCH')
        target = out / 'recognized.txt'
        with target.open('xb') as writer:
            writer.write(result.output_bytes)
            writer.flush()
            os.fsync(writer.fileno())
        if (digest(target.read_bytes()) != result.output_sha256
                or digest(source.read_bytes()) != source_sha):
            raise RuntimeError('OCR_COLD_READBACK_MISMATCH')
        return {'status': 'PASS', 'program': 'TESSERACT', 'real_cli': True,
                'transport': 'U01_CLI_CONNECTION', 'version': match.group(1),
                'program_binary_sha256': binary_sha,
                'source_sha256': source_sha, 'output_sha256': result.output_sha256,
                'text_readback': True, 'source_preserved': True,
                'new_file_readback': True, 'task_result_persisted': False,
                'receipt_authority': 'QUALIFICATION_ONLY',
                'source_file': source.name, 'output_file': target.name}
    finally:
        connection.disconnect()


def run_scanned_pdf_ocr(out: Path) -> dict:
    """Synthetic image-only PDF -> searchable PDF via real U01 OCRmyPDF."""
    from PIL import Image, ImageDraw, ImageFont
    exe = shutil.which('ocrmypdf')
    if not exe or not Path(exe).is_file():
        return {'status': 'BLOCKED_NOT_INSTALLED' if os.environ.get('IWXA_REQUIRE_INSTALLED_OCRMY_PDF') == '1' else 'SKIPPED_NOT_INSTALLED',
                'program': 'OCRMY_PDF', 'real_cli': False}
    exe = str(Path(exe).resolve())
    version = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=10)
    version_match = re.search(r'\b(\d+\.\d+\.\d+)\b', version.stdout + version.stderr)
    if version.returncode != 0 or version_match is None:
        return {'status': 'BLOCKED_VERSION_NOT_VERIFIED', 'program': 'OCRMY_PDF'}
    binary_sha = hashlib.sha256(Path(exe).read_bytes()).hexdigest()
    spec = make_document_cli_spec('OCRMY_PDF', exe, binary_sha256=binary_sha,
                                  version_contains=version_match.group(1))
    owner = CliConnection(spec, executable=exe)
    if owner.inspect().get('installation_verified') is not True:
        return {'status': 'FAIL_U01_INSPECT', 'program': 'OCRMY_PDF'}
    if owner.connect().get('status') != 'CONNECTED':
        return {'status': 'FAIL_U01_CONNECT', 'program': 'OCRMY_PDF'}
    try:
        img = Image.new('RGB', (1500, 380), 'white')
        draw = ImageDraw.Draw(img)
        font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        font = ImageFont.truetype(font_path, 63) if Path(font_path).is_file() else ImageFont.load_default()
        draw.text((55, 95), 'SCAN DOCUMENT 1500 USD', font=font, fill='black')
        source = out / 'scanned.pdf'
        img.save(source, 'PDF', resolution=200.0)
        raw_source = source.read_bytes()
        missing = pdf_text_coverage(raw_source)
        if missing.get('status') != 'OCR_OFFER' or missing.get('pages_without_text') != [1]:
            raise RuntimeError('SCAN_TEXT_LAYER_DETECTION_WRONG')
        source_sha = digest(raw_source)
        result = ocr_pdf_via_u01(owner, source_path=source,
                                 expected_source_sha256=source_sha,
                                 scratch_root=out, approved=True)
        from pypdf import PdfReader
        target = out / 'searchable.pdf'
        with target.open('xb') as f:
            f.write(result.output_bytes)
            f.flush()
            os.fsync(f.fileno())
        extracted = '\n'.join(page.extract_text() or '' for page in PdfReader(str(target)).pages).upper()
        if 'SCAN DOCUMENT' not in extracted or '1500' not in extracted:
            raise RuntimeError('OCR_PDF_SEMANTIC_READBACK_MISMATCH')
        if digest(target.read_bytes()) != result.output_sha256 or digest(source.read_bytes()) != source_sha:
            raise RuntimeError('OCR_PDF_COLD_READBACK_MISMATCH')
        return {'status': 'PASS', 'program': 'OCRMY_PDF', 'transport': 'U01_CLI_CONNECTION',
                'real_cli': True, 'version': version_match.group(1),
                'program_binary_sha256': binary_sha,
                'source_sha256': source_sha, 'output_sha256': result.output_sha256,
                'no_text_before': True, 'searchable_after': True,
                'source_preserved': True, 'new_file_readback': True,
                'receipt_authority': 'QUALIFICATION_ONLY', 'task_result_persisted': False,
                'source_file': source.name, 'output_file': target.name}
    finally:
        owner.disconnect()


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) == 2 else Path(tempfile.mkdtemp(prefix="iwxa-office-u01-"))
    try:
        report = run(destination)
        report['ocr_image'] = run_ocr(destination)
        report['ocr_pdf'] = run_scanned_pdf_ocr(destination)
        (destination / "REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
        sys.exit(0 if report['status'] in ('PASS', 'SKIPPED_NOT_INSTALLED')
                 and report['ocr_image']['status'] in ('PASS', 'SKIPPED_NOT_INSTALLED')
                 and report['ocr_pdf']['status'] in ('PASS', 'SKIPPED_NOT_INSTALLED') else 1)
    except Exception as exc:
        report = {"status": "FAIL", "reason": type(exc).__name__ + ":" + str(exc)}
        (destination / "REPORT.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
        sys.exit(1)
