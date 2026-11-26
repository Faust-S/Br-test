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

def attach_tab(binding, label, bootstrap_url, pages, ids):
    """Commit one native creation only after the original catalog proves it.

    Existing pages may have changed URL or MCP pageId. Their original stable
    identities must still be present; exactly one new identity is allowed.
    The supplied bootstrap belongs to the trusted native host, not the model.
    """
    _catalog_ids(pages, ids)
    old = set(binding.values())
    if label in binding or len(old) != len(binding):
        raise RuntimeError("native_tab_label_already_bound")
    new = set(ids.values()) - old
    if not old.issubset(ids.values()) or len(new) != 1:
        raise RuntimeError("native_tab_generation_changed")
    identity = next(iter(new))
    page = next(p for p in pages if ids[p["id"]] == identity)
    if page["url"] != bootstrap_url or sum(p["url"] == bootstrap_url for p in pages) != 1:
        raise RuntimeError("native_tab_bootstrap_not_found")
    return {**binding, label: identity}

def detach_tab(binding, label, pages, ids):
    """Commit removal only when exactly the specified stable identity is gone."""
    if label not in binding or len(binding) <= 1:
        raise RuntimeError("native_tab_close_invalid")
    remaining = {key: value for key, value in binding.items() if key != label}
    rebind_pages(remaining, pages, ids)
    return remaining
