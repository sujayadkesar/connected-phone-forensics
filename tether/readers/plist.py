"""Property list reading, including NSKeyedArchiver archives."""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any


def load(path: Path) -> Any:
    """Load a binary or XML plist."""
    with open(path, "rb") as f:
        return plistlib.load(f)


def loads(data: bytes) -> Any:
    return plistlib.loads(data)


def is_keyed_archive(obj: Any) -> bool:
    return isinstance(obj, dict) and obj.get("$archiver") == "NSKeyedArchiver" and "$objects" in obj


def unarchive(obj: Any) -> Any:
    """Decode an NSKeyedArchiver plist into plain Python values.

    Handles the common Foundation classes (dictionaries, arrays, sets,
    strings, data, dates, numbers, UUIDs, URLs). Unknown classes are returned
    as a dict of their decoded fields plus a ``$class`` name. Reference
    cycles are broken by returning the marker ``"<cycle>"``.
    """
    if not is_keyed_archive(obj):
        return obj
    objects = obj["$objects"]
    top = obj.get("$top", {})

    def resolve(value: Any, stack: tuple[int, ...]) -> Any:
        if isinstance(value, plistlib.UID):
            idx = value.data
            if idx in stack:
                return "<cycle>"
            return decode(idx, stack + (idx,))
        if isinstance(value, list):
            return [resolve(v, stack) for v in value]
        if isinstance(value, dict):
            return {k: resolve(v, stack) for k, v in value.items()}
        return value

    def decode(idx: int, stack: tuple[int, ...]) -> Any:
        item = objects[idx]
        if isinstance(item, str) and item == "$null":
            return None
        if not isinstance(item, dict):
            return item
        cls_ref = item.get("$class")
        classname = None
        if isinstance(cls_ref, plistlib.UID):
            cls = objects[cls_ref.data]
            if isinstance(cls, dict):
                classname = cls.get("$classname")
        if classname in ("NSDictionary", "NSMutableDictionary"):
            keys = [resolve(k, stack) for k in item.get("NS.keys", [])]
            vals = [resolve(v, stack) for v in item.get("NS.objects", [])]
            return {str(k): v for k, v in zip(keys, vals, strict=False)}
        if classname in ("NSArray", "NSMutableArray", "NSSet", "NSMutableSet", "NSOrderedSet"):
            return [resolve(v, stack) for v in item.get("NS.objects", [])]
        if classname in ("NSString", "NSMutableString"):
            return item.get("NS.string")
        if classname in ("NSData", "NSMutableData"):
            return item.get("NS.data")
        if classname == "NSDate":
            from .. import timeutil
            return timeutil.from_cocoa(item.get("NS.time"))
        if classname == "NSUUID":
            raw = item.get("NS.uuidbytes")
            if isinstance(raw, bytes) and len(raw) == 16:
                import uuid
                return str(uuid.UUID(bytes=raw)).upper()
            return raw
        if classname == "NSURL":
            base = resolve(item.get("NS.base"), stack)
            rel = resolve(item.get("NS.relative"), stack)
            return f"{base or ''}{rel or ''}"
        out = {k: resolve(v, stack) for k, v in item.items() if k != "$class"}
        if classname:
            out["$class"] = classname
        return out

    root = top.get("root", next(iter(top.values()), None)) if isinstance(top, dict) else None
    if root is None:
        return None
    return resolve(root, ())


def get_path(obj: Any, *keys: Any, default: Any = None) -> Any:
    """Safely walk nested dicts/lists."""
    cur = obj
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and isinstance(k, int) and -len(cur) <= k < len(cur):
            cur = cur[k]
        else:
            return default
        if cur is None:
            return default
    return cur
