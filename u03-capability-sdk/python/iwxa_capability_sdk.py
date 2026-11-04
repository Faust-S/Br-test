"""U03 compatibility observation. No permissions, storage, install or runtime."""
from __future__ import annotations

from copy import deepcopy
import re

MODULE_SCHEMA = "IWXA_MODULE_DESCRIPTOR/1.0.0"
STATUS_SCHEMA = "IWXA_CAPABILITY_STATUS/1.0.0"
_ID = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_CLAUSE = re.compile(r"(>=|<=|=|>|<)?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def _fields(value, required, permitted):
    return isinstance(value, dict) and set(required) <= value.keys() and value.keys() <= set(permitted)


def _id(value):
    return isinstance(value, str) and len(value) <= 128 and _ID.fullmatch(value) is not None


def _version(value):
    return isinstance(value, str) and len(value) <= 120 and _VERSION.fullmatch(value) is not None


def _range(value):
    return (isinstance(value, str) and 0 < len(value) <= 240 and
            1 <= len(value.split(" ")) <= 4 and
            all(_CLAUSE.fullmatch(part) for part in value.split(" ")))


def _distinct(records, field):
    return len({record[field] for record in records}) == len(records)


def validate_descriptor(value):
    keys = ("schema", "id", "version", "provides", "requires", "hostCompatibility")
    if not _fields(value, keys, keys) or value["schema"] != MODULE_SCHEMA or not _id(value["id"]) or not _version(value["version"]):
        return False
    for field, attribute, validator, limit in (
        ("provides", "version", _version, 128),
        ("requires", "range", _range, 128),
        ("hostCompatibility", "range", _range, 32),
    ):
        records = value[field]
        key = "hostId" if field == "hostCompatibility" else "id"
        if not isinstance(records, list) or len(records) > limit or (field == "hostCompatibility" and not records):
            return False
        if not all(_fields(record, (key, attribute), (key, attribute)) and
                   _id(record[key]) and validator(record[attribute]) for record in records):
            return False
        if not _distinct(records, key):
            return False
    return True


def validate_status(value):
    keys = ("schema", "id", "available", "version", "reason", "observationRef")
    return (_fields(value, keys[:3], keys) and value["schema"] == STATUS_SCHEMA and
            _id(value["id"]) and type(value["available"]) is bool and
            ("version" not in value or _version(value["version"])) and
            ("reason" not in value or (isinstance(value["reason"], str) and len(value["reason"]) <= 256)) and
            ("observationRef" not in value or (isinstance(value["observationRef"], str) and
             0 < len(value["observationRef"].strip()) and len(value["observationRef"]) <= 512)))


def satisfies(actual, requirement):
    if not _version(actual) or not _range(requirement):
        return False
    actual_tuple = tuple(map(int, actual.split(".")))
    for part in requirement.split(" "):
        match = _CLAUSE.fullmatch(part)
        operator = match[1] or "="
        target_tuple = tuple(map(int, (match[2], match[3], match[4])))
        difference = (actual_tuple > target_tuple) - (actual_tuple < target_tuple)
        if not {"=": difference == 0, ">": difference > 0, ">=": difference >= 0,
                "<": difference < 0, "<=": difference <= 0}[operator]:
            return False
    return True


def assess(descriptor, host, observations):
    reasons = []
    if not isinstance(descriptor, dict) or descriptor.get("schema") != MODULE_SCHEMA:
        reasons.append("U03_SCHEMA_UNSUPPORTED")
    elif not validate_descriptor(descriptor):
        reasons.append("U03_DESCRIPTOR_INVALID")
    if reasons:
        return {"compatible": False, "reasons": reasons}
    if not _fields(host, ("hostId", "version"), ("hostId", "version")) or not _id(host["hostId"]) or not _version(host["version"]):
        return {"compatible": False, "reasons": ["U03_HOST_INVALID"]}
    rule = next((item for item in descriptor["hostCompatibility"] if item["hostId"] == host["hostId"]), None)
    if rule is None:
        reasons.append("U03_HOST_UNSUPPORTED")
    elif not satisfies(host["version"], rule["range"]):
        reasons.append("U03_HOST_VERSION_INCOMPATIBLE")
    if not isinstance(observations, list) or not all(validate_status(item) for item in observations):
        reasons.append("U03_OBSERVATION_INVALID")
    elif not _distinct(observations, "id"):
        reasons.append("U03_OBSERVATION_AMBIGUOUS")
    else:
        observed = {item["id"]: item for item in observations}
        for required in descriptor["requires"]:
            item = observed.get(required["id"])
            suffix = ":" + required["id"]
            if item is None:
                reasons.append("U03_CAPABILITY_MISSING" + suffix)
            elif not item["available"]:
                reasons.append("U03_CAPABILITY_UNAVAILABLE" + suffix)
            elif not item.get("observationRef"):
                reasons.append("U03_CAPABILITY_UNVERIFIED" + suffix)
            elif not item.get("version") or not satisfies(item["version"], required["range"]):
                reasons.append("U03_CAPABILITY_VERSION_INCOMPATIBLE" + suffix)
    return {"compatible": not reasons, "reasons": reasons}


class ModuleAdapterPort:
    """Host-owned attach/detach callbacks. This class issues no capability grants."""

    def __init__(self, descriptor, attach, detach):
        if not validate_descriptor(descriptor):
            raise ValueError("U03_DESCRIPTOR_INVALID")
        if not callable(attach) or not callable(detach):
            raise TypeError("U03_HOST_HOOKS_REQUIRED")
        self._descriptor = deepcopy(descriptor)
        self._attach = attach
        self._detach = detach

    def describe(self):
        return deepcopy(self._descriptor)

    def attach(self, host, observations):
        assessment = assess(self._descriptor, host, observations)
        if not assessment["compatible"]:
            return {"attached": False, "assessment": assessment, "hostReceipt": None}
        receipt = self._attach(deepcopy(self._descriptor), deepcopy(host), deepcopy(observations))
        return {"attached": isinstance(receipt, dict) and receipt.get("attached") is True,
                "assessment": assessment, "hostReceipt": receipt}

    def detach(self, binding):
        return self._detach(binding)
