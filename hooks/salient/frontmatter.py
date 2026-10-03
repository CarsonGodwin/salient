"""Minimal YAML-subset frontmatter parser (stdlib only).

Supports exactly what business files need:

    ---
    id: bot-pathing
    anchors:
      - Assets/Scripts/Bots/**/*.cs
      - path: Assets/Scripts/Bots/BotMovement.cs
        hash: sha256:9f2c...
    context: [Assets/Scripts/Player/PlayerMovement.cs]
    ---

Top-level `key: scalar`, `key: [a, b]`, and `key:` followed by a list of
scalars or of one-level dicts. Anything fancier raises FrontmatterError
rather than being silently misread.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


class FrontmatterError(ValueError):
    pass


def _strip_comment(s: str) -> str:
    # YAML-style trailing comment: " # ..." outside quotes.
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s


def _scalar(raw: str) -> Any:
    s = _strip_comment(raw.strip())
    if not s:
        return ""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(p) for p in inner.split(",")] if inner else []
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def _split_kv(text: str, lineno: int) -> Tuple[str, str]:
    if ":" not in text:
        raise FrontmatterError(f"line {lineno}: expected 'key: value', got {text!r}")
    key, _, val = text.partition(":")
    key = key.strip()
    if not key:
        raise FrontmatterError(f"line {lineno}: empty key")
    return key, val


def split(text: str) -> Tuple[str, str]:
    """Return (frontmatter_text, body). Frontmatter is '' if absent."""
    if not text.startswith("---"):
        return "", text
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    raise FrontmatterError("frontmatter opened with '---' but never closed")


def parse_meta(fm: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    current_key = None  # top-level key whose value is a pending list
    current_item = None  # dict list item being filled
    item_indent = -1

    for lineno, raw in enumerate(fm.splitlines(), start=2):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()

        if indent == 0:
            key, val = _split_kv(line, lineno)
            current_item = None
            if val.strip():
                meta[key] = _scalar(val)
                current_key = None
            else:
                meta[key] = []
                current_key = key
            continue

        if current_key is None:
            raise FrontmatterError(f"line {lineno}: indented line with no open key")

        if line.startswith("- ") or line == "-":
            body = line[1:].strip()
            item_indent = indent
            if body and ":" in body and not body.startswith(("[", "\"", "'")) and _looks_like_kv(body):
                k, v = _split_kv(body, lineno)
                current_item = {k: _scalar(v)}
                meta[current_key].append(current_item)
            else:
                current_item = None
                meta[current_key].append(_scalar(body))
            continue

        if current_item is not None and indent > item_indent:
            k, v = _split_kv(line, lineno)
            current_item[k] = _scalar(v)
            continue

        raise FrontmatterError(f"line {lineno}: unsupported structure: {line!r}")

    return meta


def _looks_like_kv(body: str) -> bool:
    # "path: a/b.cs" is a dict item; "a/b.cs" or "http://x" is a scalar.
    key, _, rest = body.partition(":")
    return bool(key) and " " not in key.strip() and "/" not in key and (rest == "" or rest.startswith(" "))


def parse(text: str) -> Tuple[Dict[str, Any], str]:
    fm, body = split(text)
    return (parse_meta(fm) if fm else {}), body
