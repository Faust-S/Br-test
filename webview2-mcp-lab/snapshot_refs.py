"""Select a unique semantic target in an original Google MCP text snapshot.

Qualification helper only; no browser executor and no modification of Google's tools.
"""
import json
import re

def uid(snapshot: str, role: str, name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", role):
        raise ValueError("invalid_accessible_role")
    quoted = json.dumps(name, ensure_ascii=False)
    pattern = re.compile(
        r"^\s*uid=([A-Za-z0-9_-]+)\s+" + re.escape(role)
        + r"\s+" + re.escape(quoted) + r"(?:\s|$)"
    )
    matches = [match.group(1) for line in snapshot.splitlines()
               if (match := pattern.match(line))]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {role} {name!r}; found {len(matches)}: {matches}")
    return matches[0]
