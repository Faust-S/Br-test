"""Local HTTP website used only for G2 native browser qualification.

Actual HTTP session cookie, form submission and file bytes; not an imitation of MCP.
"""
import hashlib
import json
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent
COOKIE_NAME = "iwxa_lab_session"
COOKIE_VALUE = "test-operator-167"
UPLOAD_BYTES = b"IWXA supporting document G2\nReference: 167\n"
DOWNLOAD_BYTES = b"IWXA native browser result G2\nReceipt: 167\n"
MAX_BODY = 65536

def signed_in(handler):
    jar = SimpleCookie()
    try:
        jar.load(handler.headers.get("Cookie", ""))
    except Exception:
        return False
    return COOKIE_NAME in jar and jar[COOKIE_NAME].value == COOKIE_VALUE

def response(handler, status, data=b"", content_type="text/plain; charset=utf-8", **headers):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    for name, value in headers.items():
        handler.send_header(name.replace("_", "-"), value)
    handler.end_headers()
    if data:
        handler.wfile.write(data)

def handle_get(handler, record):
    if handler.path == "/advanced":
        authenticated = signed_in(handler)
        body = (ROOT / "advanced.html").read_text(encoding="utf-8")
        body = body.replace("{{SESSION_STATUS}}",
                            "Session: test operator" if authenticated else "Session: signed out")
        record({"kind": "advanced_load", "authenticated": authenticated})
        response(handler, 200, body.encode("utf-8"), "text/html; charset=utf-8")
        return True
    if handler.path == "/download":
        if not signed_in(handler):
            response(handler, 401, b"Sign in first")
            return True
        record({"kind": "advanced_download", "sha256": hashlib.sha256(DOWNLOAD_BYTES).hexdigest()})
        response(handler, 200, DOWNLOAD_BYTES, "application/octet-stream",
                 Content_Disposition='attachment; filename="iwxa-result.txt"')
        return True
    return False

def handle_post(handler, record):
    if handler.path not in {"/signin", "/advanced-order", "/upload"}:
        return False
    try:
        size = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        response(handler, 400, b"Invalid length")
        return True
    if size < 0 or size > MAX_BODY:
        response(handler, 413, b"Invalid length")
        return True
    body = handler.rfile.read(size)
    if handler.path == "/signin":
        record({"kind": "advanced_signin"})
        response(handler, 303, Location="/advanced",
                 Set_Cookie=f"{COOKIE_NAME}={COOKIE_VALUE}; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax")
        return True
    if not signed_in(handler):
        response(handler, 401, b"Sign in first")
        return True
    if handler.path == "/advanced-order":
        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        expected = {"company": ["IWXA Lab"], "delivery": ["Express"], "receipt": ["yes"]}
        if fields != expected:
            record({"kind": "advanced_order_rejected", "fields": fields})
            response(handler, 422, b"Incorrect form fields")
            return True
        record({"kind": "advanced_order", "fields": fields})
        response(handler, 200, b"Order accepted 167")
        return True
    digest = hashlib.sha256(body).hexdigest()
    record({"kind": "advanced_upload", "sha256": digest, "size": len(body)})
    response(handler, 200, json.dumps({"sha256": digest, "size": len(body)}).encode("utf-8"),
             "application/json")
    return True
