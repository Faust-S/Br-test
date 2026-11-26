"""In-memory identity mapping between native host tabs and original MCP pages.

The native host creates each tab at a unique bootstrap URL; only then is the
original get_tab_id used. URL/title are not identity after initial binding.
"""
from public_sites import page_catalog

def stable_tab_id(result):
    structured = result.get("structuredContent")
    value = structured.get("tabId") if isinstance(structured,dict) else None
    if not isinstance(value,str) or not value or len(value)>256:
        raise RuntimeError("original_mcp_tab_id_missing")
    return value

def _catalog_ids(pages, ids):
    page_ids = {p["id"] for p in pages}
    if set(ids) != page_ids or len(page_ids)!=len(pages):
        raise RuntimeError("native_tab_catalog_mismatch")
    if not all(isinstance(v,str) and v for v in ids.values()) or len(set(ids.values()))!=len(ids):
        raise RuntimeError("native_tab_identity_not_unique")

def bind_bootstrap(native_urls, pages, ids):
    _catalog_ids(pages,ids)
    if len(native_urls)!=len(pages) or len(set(native_urls.values()))!=len(native_urls):
        raise RuntimeError("native_tab_bootstrap_not_unique")
    output={}
    for label,url in native_urls.items():
        matches=[p for p in pages if p["url"]==url]
        if len(matches)!=1:
            raise RuntimeError("native_tab_bootstrap_not_found")
        output[label]=ids[matches[0]["id"]]
    return output

def rebind_pages(binding, pages, ids):
    _catalog_ids(pages,ids)
    if set(binding.values())!=set(ids.values()) or len(binding)!=len(ids):
        raise RuntimeError("native_tab_generation_changed")
    lookup={v:k for k,v in ids.items()}
    return {label:lookup[identity] for label,identity in binding.items()}
