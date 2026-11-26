"""Read-only public-page qualification criteria for the actual original MCP."""
from urllib.parse import urlsplit

SITES = (
    ("github", "https://github.com/ChromeDevTools/chrome-devtools-mcp",
     {"github.com"}, ("chrome-devtools-mcp",)),
    ("iwxa", "https://iwxa.ru/", {"iwxa.ru", "www.iwxa.ru"}, ("iwxa",)),
    ("learn", "https://learn.iwxa.ru/", {"learn.iwxa.ru"},
     ("learn", "iwxa", "обучен", "образован", "университет", "лад")),
    ("artbazi", "https://artbazi.ru/", {"artbazi.ru", "www.artbazi.ru"},
     ("artbazi", "artbazy", "артбаз")),
)

def page_catalog(result):
    """Use the upstream structured page records rather than titles or AX text."""
    structured = result.get("structuredContent")
    if not isinstance(structured, dict) or not isinstance(structured.get("pages"), list):
        raise RuntimeError("original_mcp_structured_pages_missing")
    pages = structured["pages"]
    if not all(isinstance(p,dict) and type(p.get("id")) is int and
               isinstance(p.get("url"),str) and isinstance(p.get("title"),str) for p in pages):
        raise RuntimeError("original_mcp_invalid_page_records")
    if len({p["id"] for p in pages}) != len(pages):
        raise RuntimeError("original_mcp_duplicate_page_id")
    return pages

def validate_public_page(result, expected_id, hosts):
    pages = page_catalog(result)
    if len(pages) != 1 or pages[0]["id"] != expected_id:
        raise RuntimeError("public_page_not_original_visible_target")
    page = pages[0]
    url = urlsplit(page["url"])
    if (url.scheme != "https" or url.hostname not in hosts or url.port not in (None,443)
        or url.username is not None or url.password is not None):
        raise RuntimeError("public_page_origin_mismatch")
    return page

def validate_public_snapshot(text, markers):
    normalized = text.casefold()
    nodes = sum(line.lstrip().startswith("uid=") for line in text.splitlines())
    if "rootwebarea" not in normalized or nodes < 5:
        raise RuntimeError("public_page_accessibility_content_missing")
    if not any(marker.casefold() in normalized for marker in markers):
        raise RuntimeError("public_page_expected_content_missing")
    return nodes
