"""U01 document capability adapter. No installer, Store, authority or GUI engine.

The Agent host owns selected files, Human Review, approval, output publication,
readback receipts and Result. The adapter only produces independently checkable
bytes and selects declared executors; external CLIs use U01 CliConnection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import base64
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Callable, Mapping
import xml.etree.ElementTree as ET
from xml.parsers import expat
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZipFile

SCHEMA = "IWXA_U01_DOCUMENT_OPERATION/1"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_DOCX_BYTES = 32 * 1024 * 1024
MAX_PART_BYTES = 32 * 1024 * 1024
MAX_PARTS = 512
MAX_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_TEXT_CHARS = 16_000


class DocumentOperationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


@dataclass(frozen=True)
class DocumentCandidate:
    operation: str
    executor: str
    source_sha256: str
    output_sha256: str
    output_format: str
    output_bytes: bytes = field(repr=False)
    summary: str = ""

    @property
    def plan_digest(self) -> str:
        fields = (SCHEMA, self.operation, self.executor, self.source_sha256,
                  self.output_sha256, self.output_format)
        return digest("\n".join(fields).encode("utf-8"))



# Keep the complete reviewed payload within the existing ToolRuntime argument
# bound. Larger files must use a future host-approved existing Files transfer,
# never silently route around Task and Human Review.
MAX_INLINE_DOCUMENT_BYTES = 700_000


def files_binary_write_candidate(candidate: DocumentCandidate, *,
                                 root_id: str, source_path: str,
                                 destination_path: str) -> dict:
    """Adapt reviewed U01 bytes to the *existing* Agent Task action shape.

    This is pure serialization, NOT an execution/approval/receipt. Agent's
    ToolRuntime must bind the returned arguments to its own accepted Task,
    review and scoped Files provider before performing a no-clobber create.
    """
    if candidate.output_format not in ("docx", "pdf") or not isinstance(candidate.output_bytes, bytes):
        raise DocumentOperationError("BINARY_ACTION_FORMAT_UNSUPPORTED")
    if not candidate.output_bytes or len(candidate.output_bytes) > MAX_INLINE_DOCUMENT_BYTES:
        raise DocumentOperationError("BINARY_ACTION_TOO_LARGE")
    if digest(candidate.output_bytes) != candidate.output_sha256:
        raise DocumentOperationError("BINARY_ACTION_PAYLOAD_MISMATCH")
    if not re.fullmatch(r"[a-f0-9]{64}", candidate.source_sha256):
        raise DocumentOperationError("BINARY_ACTION_SOURCE_INVALID")
    for path in (source_path, destination_path):
        if (not isinstance(path, str) or not path or len(path) > 160
                or path.startswith(("/", "\\")) or "\\" in path
                or any(piece in ("", ".", "..") or piece.startswith(".")
                       for piece in path.split("/"))):
            raise DocumentOperationError("BINARY_ACTION_PATH_INVALID")
    if not isinstance(root_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", root_id):
        raise DocumentOperationError("BINARY_ACTION_ROOT_INVALID")
    if source_path.casefold() == destination_path.casefold():
        raise DocumentOperationError("BINARY_ACTION_SOURCE_OVERWRITE_FORBIDDEN")
    if not destination_path.lower().endswith("." + candidate.output_format):
        raise DocumentOperationError("BINARY_ACTION_FORMAT_MISMATCH")
    args = {
        "root_id": root_id,
        "path": destination_path,
        "source_path": source_path,
        "expected_source_sha256": candidate.source_sha256,
        "content_base64": base64.b64encode(candidate.output_bytes).decode("ascii"),
        "expected_content_sha256": candidate.output_sha256,
        "mode": "create",
    }
    return {"operation": "files.write_binary", "target": destination_path,
            "arguments": args, "risk": "medium", "reversible": True,
            "description": candidate.summary}


def _docx_entries(raw: bytes) -> tuple[list, dict[str, bytes]]:
    if not isinstance(raw, bytes) or len(raw) > MAX_DOCX_BYTES:
        raise DocumentOperationError("DOCX_SIZE_UNSUPPORTED")
    try:
        with ZipFile(BytesIO(raw)) as package:
            items = package.infolist()
            names = [item.filename for item in items]
            if (len(items) > MAX_PARTS or len(names) != len(set(names)) or
                    sum(item.file_size for item in items) > MAX_TOTAL_UNCOMPRESSED_BYTES or
                    any(item.flag_bits & 1 or item.file_size > MAX_PART_BYTES
                        or item.filename.startswith("/") or "\\" in item.filename or
                        ".." in item.filename.split("/") for item in items)):
                raise DocumentOperationError("DOCX_PACKAGE_UNSUPPORTED")
            if not {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(names):
                raise DocumentOperationError("DOCX_PACKAGE_UNSUPPORTED")
            if any("_xmlsignatures" in name.lower() or "vbaproject" in name.lower()
                   for name in names):
                raise DocumentOperationError("DOCX_SIGNED_OR_MACRO_UNSUPPORTED")
            return items, {item.filename: package.read(item) for item in items}
    except DocumentOperationError:
        raise
    except (BadZipFile, OSError, RuntimeError, ValueError, EOFError, NotImplementedError):
        raise DocumentOperationError("DOCX_PACKAGE_UNREADABLE") from None


def append_docx_paragraph(source: bytes, text: str, *, expected_source_sha256: str) -> DocumentCandidate:
    """Append one plain paragraph to a NEW DOCX copy; refuse complex/signed files.

    No in-place rewrite. Preserves all package parts except word/document.xml.
    This conservative native path is not a general DOCX editing engine.
    """
    if not isinstance(source, bytes):
        raise DocumentOperationError("DOCX_SOURCE_BYTES_REQUIRED")
    if digest(source) != expected_source_sha256:
        raise DocumentOperationError("SOURCE_REVISION_CHANGED")
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS or
            any((ord(ch) < 32 and ch not in "\t\n\r") for ch in text)):
        raise DocumentOperationError("PARAGRAPH_INVALID")
    items, parts = _docx_entries(source)
    xml_before = parts["word/document.xml"]
    if b"<!DOCTYPE" in xml_before.upper():
        raise DocumentOperationError("DOCX_DTD_UNSUPPORTED")
    if any(c in text for c in "\r\n\t"):
        raise DocumentOperationError("PARAGRAPH_SINGLE_LINE_REQUIRED")
    # Insert bytes at the exact direct child position without reserializing the
    # existing XML. Word's mc:Ignorable references unused namespace prefixes;
    # ElementTree/lxml serialization can invalidate these modern documents.
    parser = expat.ParserCreate(namespace_separator="}")
    depth = 0
    body_depth = None
    body_prefix = None
    section_position = None
    body_end_position = None
    ns = W[1:]  # {URI} -> URI} (Expat's expanded element name)

    def on_start(name, _attrs):
        nonlocal depth, body_depth, body_prefix, section_position
        depth += 1
        if name == ns + "body":
            if body_depth is not None:
                raise DocumentOperationError("DOCX_BODY_UNSUPPORTED")
            body_depth = depth
            match = re.match(rb"<([A-Za-z_][A-Za-z0-9_.-]*):body(?=[\s>/])",
                             xml_before[parser.CurrentByteIndex:])
            if match is None:
                raise DocumentOperationError("DOCX_BODY_UNSUPPORTED")
            body_prefix = match.group(1).decode("ascii")
        elif name == ns + "sectPr" and body_depth is not None and depth == body_depth + 1:
            section_position = parser.CurrentByteIndex

    def on_end(name):
        nonlocal depth, body_end_position
        if name == ns + "body":
            body_end_position = parser.CurrentByteIndex
        depth -= 1

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    try:
        parser.Parse(xml_before, True)
    except DocumentOperationError:
        raise
    except expat.ExpatError:
        raise DocumentOperationError("DOCX_XML_UNREADABLE") from None
    if body_prefix is None or body_end_position is None:
        raise DocumentOperationError("DOCX_BODY_UNSUPPORTED")
    inserted = escape(text)
    space = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
    paragraph = (f"<{body_prefix}:p><{body_prefix}:r><{body_prefix}:t{space}>"
                 f"{inserted}</{body_prefix}:t></{body_prefix}:r></{body_prefix}:p>").encode("utf-8")
    offset = section_position if section_position is not None else body_end_position
    xml = xml_before[:offset] + paragraph + xml_before[offset:]
    try:
        verified_tree = ET.fromstring(xml)
        if verified_tree.tag != W + "document" or verified_tree.find(W + "body") is None:
            raise DocumentOperationError("DOCX_BODY_UNSUPPORTED")
    except ET.ParseError:
        raise DocumentOperationError("DOCX_OUTPUT_READBACK_FAILED") from None
    output = BytesIO()
    with ZipFile(output, "w") as result:
        for item in items:
            result.writestr(item, xml if item.filename == "word/document.xml" else parts[item.filename])
    edited = output.getvalue()
    _, checked = _docx_entries(edited)
    try:
        new_root = ET.fromstring(checked["word/document.xml"])
        new_body = new_root.find(W + "body")
        if new_body is None or not any(node.find("./" + W + "r/" + W + "t") is not None and
                                        node.find("./" + W + "r/" + W + "t").text == text
                                        for node in new_body.findall(W + "p")):
            raise DocumentOperationError("DOCX_OUTPUT_READBACK_FAILED")
    except ET.ParseError:
        raise DocumentOperationError("DOCX_OUTPUT_READBACK_FAILED") from None
    return DocumentCandidate("docx.append_paragraph", "IWXA_NATIVE_DOCX_V1",
                             expected_source_sha256, digest(edited), "docx", edited,
                             "Added one plain paragraph to a new DOCX copy")


def publish_approved(candidate: DocumentCandidate, *, approved_plan_digest: str,
                     read_source: Callable[[], bytes],
                     write_new: Callable[[bytes], None],
                     read_output: Callable[[], bytes]) -> dict:
    """Host callback owns authorized, no-clobber write and durable receipt.

    Recheck both exact source and exact published bytes. No retries after an
    ambiguous write. Constructing a candidate is NOT proof of user consent.
    """
    if approved_plan_digest != candidate.plan_digest:
        raise DocumentOperationError("HUMAN_APPROVAL_REQUIRED")
    if digest(read_source()) != candidate.source_sha256:
        raise DocumentOperationError("SOURCE_REVISION_CHANGED")
    write_new(candidate.output_bytes)
    if digest(read_output()) != candidate.output_sha256:
        raise DocumentOperationError("OUTPUT_READBACK_MISMATCH")
    if digest(read_source()) != candidate.source_sha256:
        raise DocumentOperationError("SOURCE_CHANGED_AFTER_WRITE")
    return {"schema": SCHEMA, "status": "READBACK_VERIFIED",
            "operation": candidate.operation, "executor": candidate.executor,
            "source_sha256": candidate.source_sha256,
            "output_sha256": candidate.output_sha256, "plan_digest": candidate.plan_digest,
            "source_preserved": True, "receipt_authority": "HOST_TOOLRUNTIME_REQUIRED",
            "result_persisted": False}


def verify_cold_readback(candidate: DocumentCandidate, *, read_source: Callable[[], bytes],
                         read_output: Callable[[], bytes]) -> dict:
    """Re-open real files after host restart without trusting prior observations."""
    source_ok = digest(read_source()) == candidate.source_sha256
    output_ok = digest(read_output()) == candidate.output_sha256
    return {"schema": SCHEMA, "status": "PASS" if source_ok and output_ok else "FAIL",
            "source_preserved": source_ok, "output_verified": output_ok,
            "source_sha256": candidate.source_sha256,
            "output_sha256": candidate.output_sha256}


# Program discovery is observational only. Detection != supported automation.
PROGRAMS = (
    ("MICROSOFT_WORD", "WINWORD.EXE", "MICROSOFT OFFICE", "GUI_OR_HOST_APPROVED_COM"),
    ("MICROSOFT_EXCEL", "EXCEL.EXE", "MICROSOFT OFFICE", "GUI_OR_HOST_APPROVED_COM"),
    ("MICROSOFT_POWERPOINT", "POWERPNT.EXE", "MICROSOFT OFFICE", "GUI_OR_HOST_APPROVED_COM"),
    ("LIBREOFFICE", "soffice.com", "LIBREOFFICE", "U01_CLI_CANDIDATE"),
    ("ONLYOFFICE", "DesktopEditors.exe", "ONLYOFFICE", "GUI_ONLY"),
    ("OCRMY_PDF", "ocrmypdf", "OCRMY_PDF", "U01_CLI_CANDIDATE"),
    ("TESSERACT", "tesseract", "TESSERACT", "U01_CLI_CANDIDATE"),
)


def _windows_default_association(extension: str) -> str | None:
    """Read current user's ProgID; this is NOT proof of a callable interface."""
    if os.name != "nt":
        return None
    import winreg
    key_name = ("Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\FileExts\\"
                + extension + "\\UserChoice")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
            value = winreg.QueryValueEx(key, "ProgId")[0]
            return value if isinstance(value, str) else None
    except OSError:
        return None


def discover_document_programs(*, environ: Mapping[str, str] | None = None,
                               which_fn: Callable[[str], str | None] = shutil.which,
                               platform_name: str | None = None,
                               association_probe: Callable[[str], str | None] | None = None) -> dict:
    env = os.environ if environ is None else environ
    if association_probe is None and os.name == "nt" and environ is None:
        association_probe = _windows_default_association
    windows = (platform_name or os.name) in ("nt", "win32")
    detected = []
    for program_id, binary, _, mode in PROGRAMS:
        paths = []
        if windows:
            for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                base = env.get(key)
                if not base:
                    continue
                root = Path(base)
                if program_id.startswith("MICROSOFT_"):
                    paths += [root / "Microsoft Office" / "root" / "Office16" / binary,
                              root / "Microsoft Office" / "Office16" / binary]
                elif program_id == "LIBREOFFICE":
                    paths += [root / "LibreOffice" / "program" / "soffice.com",
                              root / "LibreOffice" / "program" / "soffice.exe"]
                elif program_id == "ONLYOFFICE":
                    paths += [root / "ONLYOFFICE" / "DesktopEditors" / binary]
        names = [binary]
        if program_id == "LIBREOFFICE" and not windows:
            names = ["libreoffice", "soffice"]
        if program_id == "OCRMY_PDF" and windows:
            names = ["ocrmypdf.exe", "ocrmypdf"]
        if program_id == "TESSERACT" and windows:
            names = ["tesseract.exe", "tesseract"]
        for name in names:
            try:
                path = which_fn(name)
            except Exception:
                path = None
            if path:
                paths.append(Path(path))
        found = None
        for path in paths:
            try:
                if path.is_file():
                    found = str(path.resolve())
                    break
            except OSError:
                continue
        detected.append({"program_id": program_id, "installed_detected": found is not None,
                         "path": found, "mode": mode,
                         "cli_usable_verified": False, "api_usable_verified": False,
                         "interface_confirmation": "U01_CONNECT_REQUIRED" if mode == "U01_CLI_CANDIDATE"
                                                   else "HOST_APPROVAL_OR_GUI_REQUIRED"})
    defaults = {}
    if association_probe:
        for ext in (".docx", ".xlsx", ".pptx", ".pdf"):
            try:
                value = association_probe(ext)
            except Exception:
                value = None
            defaults[ext] = value if isinstance(value, str) and 0 < len(value) < 256 else None
    return {"schema": SCHEMA, "programs": detected,
            "default_associations": defaults, "read_only": True,
            "installation_mutated": False, "program_launched": False}


def select_document_executor(operation: str, inventory: dict,
                             *, u01_connected_program_ids: tuple[str, ...] = ()) -> dict:
    if operation == "docx.append_paragraph":
        return {"executor": "IWXA_NATIVE_DOCX_V1", "status": "READY_NATIVE"}
    required = {"pdf.convert": "LIBREOFFICE", "pdf.ocr": "OCRMY_PDF",
                "image.ocr": "TESSERACT"}.get(operation)
    if required is None:
        return {"executor": None, "status": "OPERATION_UNSUPPORTED"}
    row = next((x for x in inventory.get("programs", []) if x.get("program_id") == required), None)
    if not row or row.get("installed_detected") is not True:
        return {"executor": None, "status": "PROGRAM_NOT_INSTALLED"}
    if required not in u01_connected_program_ids:
        return {"executor": required, "status": "U01_CONNECT_REQUIRED"}
    return {"executor": required, "status": "READY_U01_CLI"}


def make_document_cli_spec(program_id: str, executable: str, *, binary_sha256: str,
                           version_contains: str) -> dict:
    """Reviewed argv templates for existing U01 CliConnection, never a new runner."""
    name = Path(executable).name
    ops = {
        "LIBREOFFICE": {
            "pdf_docx": ["{profile}", "--headless", "--convert-to", "pdf:writer_pdf_Export",
                         "--outdir", "{outdir}", "{source}"],
            "pdf_xlsx": ["{profile}", "--headless", "--convert-to", "pdf:calc_pdf_Export",
                         "--outdir", "{outdir}", "{source}"],
            "pdf_pptx": ["{profile}", "--headless", "--convert-to", "pdf:impress_pdf_Export",
                         "--outdir", "{outdir}", "{source}"],
        },
        "OCRMY_PDF": {"ocr_pdf": ["--skip-text", "--output-type", "pdf", "--",
                                    "{source}", "{dest}"]},
        "TESSERACT": {"ocr_image": ["{source}", "{output_base}", "-l", "{lang}", "txt"]},
    }
    if program_id not in ops or not re.fullmatch(r"[0-9a-f]{64}", binary_sha256):
        raise DocumentOperationError("U01_SPEC_UNSUPPORTED")
    return {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "DOCUMENT_" + program_id,
            "executable_name": name, "executable_sha256": binary_sha256,
            "version_args": ["--version"], "version_contains": version_contains,
            "operations": ops[program_id]}


def _hash_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for data in iter(lambda: f.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def convert_to_pdf_via_u01(connection, *, source_path: str | Path,
                           expected_source_sha256: str, scratch_root: str | Path,
                           approved: bool) -> DocumentCandidate:
    """Real isolated conversion with an already connected, pinned U01 CLI.

    Converts into host-owned disposable staging; returns bytes for the host's
    *existing* file-write/approval path. No durable user output is written here.
    """
    source = Path(source_path)
    if not approved:
        raise DocumentOperationError("HUMAN_APPROVAL_REQUIRED")
    if source.suffix.lower() not in (".docx", ".xlsx", ".pptx"):
        raise DocumentOperationError("PDF_SOURCE_FORMAT_UNSUPPORTED")
    if not source.is_file() or _hash_file(source) != expected_source_sha256:
        raise DocumentOperationError("SOURCE_REVISION_CHANGED")
    state = connection.capabilities()
    if (connection.spec.get("program_id") != "DOCUMENT_LIBREOFFICE" or
            state.get("status") != "DECLARED" or
            state.get("installation_verified") is not True):
        raise DocumentOperationError("U01_CONNECTION_REQUIRED")
    staging = Path(scratch_root)
    if not staging.is_dir():
        raise DocumentOperationError("STAGING_ROOT_UNAVAILABLE")
    with tempfile.TemporaryDirectory(prefix="iwxa-office-", dir=staging) as temp:
        folder = Path(temp)
        profile = (folder / "profile").as_uri()
        observation = connection.invoke("pdf_" + source.suffix.lower()[1:],
                                        {"profile": "-env:UserInstallation=" + profile,
                                         "outdir": str(folder), "source": str(source)},
                                        timeout_seconds=20)
        if observation.get("status") != "EXECUTED" or observation.get("executionOutcome") != "ACKNOWLEDGED":
            raise DocumentOperationError("U01_CONVERSION_NOT_ACKNOWLEDGED")
        output = folder / (source.stem + ".pdf")
        if not output.is_file() or output.stat().st_size > MAX_DOCX_BYTES:
            raise DocumentOperationError("PDF_OUTPUT_MISSING_OR_LARGE")
        raw = output.read_bytes()
        if not raw.startswith(b"%PDF-") or b"%%EOF" not in raw[-2048:]:
            raise DocumentOperationError("PDF_OUTPUT_INVALID")
        try:
            from pypdf import PdfReader
            if not PdfReader(BytesIO(raw)).pages:
                raise DocumentOperationError("PDF_OUTPUT_INVALID")
        except ImportError:
            pass  # Header/readback proven; full PDF parse remains host-dependent.
        except DocumentOperationError:
            raise
        except Exception:
            raise DocumentOperationError("PDF_OUTPUT_INVALID") from None
        if _hash_file(source) != expected_source_sha256:
            raise DocumentOperationError("SOURCE_CHANGED_DURING_CONVERSION")
        return DocumentCandidate("pdf.convert", "DOCUMENT_LIBREOFFICE",
                                 expected_source_sha256, digest(raw), "pdf", raw,
                                 "Converted to PDF in isolated staging through pinned U01 CLI")


def pdf_text_coverage(pdf_bytes: bytes) -> dict:
    """Do not declare 'scanned' merely because OCR dependencies are missing."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"status": "UNDETERMINED", "reason": "PDF_READER_UNAVAILABLE"}
    try:
        source = PdfReader(BytesIO(pdf_bytes))
        if source.is_encrypted:
            return {"status": "UNDETERMINED", "reason": "PDF_ENCRYPTED"}
        missing = [i + 1 for i, p in enumerate(source.pages) if not (p.extract_text() or "").strip()]
        return {"status": "OCR_OFFER" if missing else "TEXT_PRESENT",
                "pages_total": len(source.pages), "pages_without_text": missing,
                "ocr_executor": "OCRMY_PDF" if missing else None}
    except Exception:
        return {"status": "UNDETERMINED", "reason": "PDF_PARSE_FAILED"}


def ocr_pdf_via_u01(connection, *, source_path: str | Path,
                    expected_source_sha256: str, scratch_root: str | Path,
                    approved: bool) -> DocumentCandidate:
    """Run installed OCRmyPDF through pinned U01, never install it automatically."""
    if not approved:
        raise DocumentOperationError("HUMAN_APPROVAL_REQUIRED")
    source = Path(source_path)
    if source.suffix.lower() != ".pdf" or not source.is_file():
        raise DocumentOperationError("OCR_SOURCE_UNSUPPORTED")
    raw = source.read_bytes()
    if digest(raw) != expected_source_sha256:
        raise DocumentOperationError("SOURCE_REVISION_CHANGED")
    coverage = pdf_text_coverage(raw)
    if coverage.get("status") != "OCR_OFFER":
        raise DocumentOperationError("OCR_NOT_INDICATED_OR_UNVERIFIED")
    state = connection.capabilities()
    if (connection.spec.get("program_id") != "DOCUMENT_OCRMY_PDF" or
            state.get("status") != "DECLARED" or state.get("installation_verified") is not True):
        raise DocumentOperationError("U01_CONNECTION_REQUIRED")
    scratch = Path(scratch_root)
    if not scratch.is_dir():
        raise DocumentOperationError("STAGING_ROOT_UNAVAILABLE")
    with tempfile.TemporaryDirectory(prefix="iwxa-ocr-", dir=scratch) as temp:
        output = Path(temp) / "searchable.pdf"
        observation = connection.invoke("ocr_pdf", {"source": str(source), "dest": str(output)},
                                        timeout_seconds=20)
        if (observation.get("status") != "EXECUTED" or
                observation.get("executionOutcome") != "ACKNOWLEDGED"):
            raise DocumentOperationError("U01_OCR_NOT_ACKNOWLEDGED")
        if not output.is_file() or output.stat().st_size > MAX_DOCX_BYTES:
            raise DocumentOperationError("OCR_OUTPUT_MISSING_OR_LARGE")
        converted = output.read_bytes()
        after = pdf_text_coverage(converted)
        if after.get("status") != "TEXT_PRESENT":
            raise DocumentOperationError("OCR_TEXT_READBACK_FAILED")
        if _hash_file(source) != expected_source_sha256:
            raise DocumentOperationError("SOURCE_CHANGED_DURING_OCR")
        return DocumentCandidate("pdf.ocr", "DOCUMENT_OCRMY_PDF", expected_source_sha256,
                                 digest(converted), "pdf", converted,
                                 "OCRmyPDF created a searchable PDF copy")


def ocr_image_via_u01(connection, *, source_path: str | Path,
                      expected_source_sha256: str, scratch_root: str | Path,
                      approved: bool, language: str = "eng") -> DocumentCandidate:
    """Tesseract image -> plain text. Does not claim direct scanned-PDF OCR."""
    if not approved:
        raise DocumentOperationError("HUMAN_APPROVAL_REQUIRED")
    source = Path(source_path)
    if source.suffix.lower() not in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        raise DocumentOperationError("OCR_SOURCE_UNSUPPORTED")
    if not source.is_file() or _hash_file(source) != expected_source_sha256:
        raise DocumentOperationError("SOURCE_REVISION_CHANGED")
    if not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3}){0,2}", language):
        raise DocumentOperationError("OCR_LANGUAGE_INVALID")
    state = connection.capabilities()
    if (connection.spec.get("program_id") != "DOCUMENT_TESSERACT" or
            state.get("status") != "DECLARED" or state.get("installation_verified") is not True):
        raise DocumentOperationError("U01_CONNECTION_REQUIRED")
    scratch = Path(scratch_root)
    if not scratch.is_dir():
        raise DocumentOperationError("STAGING_ROOT_UNAVAILABLE")
    with tempfile.TemporaryDirectory(prefix="iwxa-image-ocr-", dir=scratch) as temp:
        base = Path(temp) / "recognized"
        observation = connection.invoke("ocr_image", {"source": str(source),
                                                       "output_base": str(base), "lang": language},
                                        timeout_seconds=20)
        if (observation.get("status") != "EXECUTED" or
                observation.get("executionOutcome") != "ACKNOWLEDGED"):
            raise DocumentOperationError("U01_OCR_NOT_ACKNOWLEDGED")
        output = base.with_suffix(".txt")
        if not output.is_file() or output.stat().st_size > MAX_DOCX_BYTES:
            raise DocumentOperationError("OCR_OUTPUT_MISSING_OR_LARGE")
        raw = output.read_bytes()
        try:
            extracted = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            raise DocumentOperationError("OCR_TEXT_ENCODING_UNSUPPORTED") from None
        if not extracted.strip():
            raise DocumentOperationError("OCR_TEXT_READBACK_FAILED")
        if _hash_file(source) != expected_source_sha256:
            raise DocumentOperationError("SOURCE_CHANGED_DURING_OCR")
        return DocumentCandidate("image.ocr", "DOCUMENT_TESSERACT", expected_source_sha256,
                                 digest(raw), "txt", raw,
                                 "Tesseract extracted text to a new UTF-8 file")
