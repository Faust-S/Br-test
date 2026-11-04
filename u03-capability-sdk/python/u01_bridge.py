"""Read-only U01 -> U03 projection, never a grant or program connection."""
import re
from iwxa_capability_sdk import STATUS_SCHEMA

U01 = "IWXA_PROGRAM_CONNECTION/1.0.0"
_ID = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_OP = re.compile(r"[a-z][a-z0-9_]*\Z")
_PROGRAM = re.compile(r"[A-Z][A-Z0-9_]{2,79}\Z")
_TRANSPORTS = frozenset(("cli", "http", "mcp", "sdk", "ipc", "file"))


def _keys(value, required, optional=()):
    return isinstance(value, dict) and set(required) <= value.keys() and value.keys() <= (set(required) | set(optional))


def _config(config):
    if not _keys(config, ("programId", "bindings"), ("receipts",)):
        return False
    if not isinstance(config["programId"], str) or not _PROGRAM.fullmatch(config["programId"]):
        return False
    bindings = config["bindings"]
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= 128:
        return False
    if not all(_keys(x, ("operation", "id", "version")) and
               isinstance(x["operation"], str) and _OP.fullmatch(x["operation"]) and
               isinstance(x["id"], str) and len(x["id"]) <= 128 and _ID.fullmatch(x["id"]) and
               isinstance(x["version"], str) and len(x["version"]) <= 120 and _VERSION.fullmatch(x["version"])
               for x in bindings):
        return False
    if len({x["operation"] for x in bindings}) != len(bindings) or len({x["id"] for x in bindings}) != len(bindings):
        return False
    return "receipts" not in config or isinstance(config["receipts"], dict)


def _live(x, program_id):
    if not (isinstance(x, dict) and x.get("schema") == U01 and
            x.get("program_id") == program_id and x.get("transport") in _TRANSPORTS and
            x.get("status") == "DECLARED" and x.get("connected") is True):
        return False
    ops = x.get("operations")
    return (isinstance(ops, list) and len(ops) <= 128 and
            all(_keys(item, ("name", "declared", "verified")) and
                isinstance(item["name"], str) and _OP.fullmatch(item["name"]) and
                type(item["declared"]) is bool and type(item["verified"]) is bool for item in ops) and
            len({item["name"] for item in ops}) == len(ops))


def _receipt_valid(receipt, operation, program_id, transport):
    if not _keys(receipt, ("observation", "observationRef")):
        return False
    o, ref = receipt["observation"], receipt["observationRef"]
    return (isinstance(o, dict) and o.get("schema") == U01 and o.get("program_id") == program_id and
            o.get("transport") == transport and o.get("operation") == operation and
            o.get("status") == "EXECUTED" and o.get("executed") is True and
            isinstance(ref, str) and 0 < len(ref.strip()) and len(ref) <= 512)


def observe_u01_capabilities(connection, config):
    if not _config(config):
        raise ValueError("U03_U01_BINDINGS_INVALID")
    try:
        source = connection.capabilities()
    except Exception:
        source = None
    live = _live(source, config["programId"])
    operations = {x["name"]: x for x in source["operations"]} if live else {}
    receipts = config.get("receipts", {})
    results = []
    for item in config["bindings"]:
        operation = operations.get(item["operation"])
        receipt = receipts.get(item["operation"])
        reason = "source-unavailable"
        if live:
            if operation is None or not operation["declared"]:
                reason = "operation-unsupported"
            elif not operation["verified"]:
                reason = "operation-unverified"
            elif not _receipt_valid(receipt, item["operation"], config["programId"], source["transport"]):
                reason = "execution-receipt-missing"
            else:
                reason = None
        entry = {"schema": STATUS_SCHEMA, "id": item["id"], "available": reason is None}
        if reason is not None:
            entry["reason"] = reason
        else:
            entry["version"] = item["version"]
            entry["observationRef"] = receipt["observationRef"]
        results.append(entry)
    return results
