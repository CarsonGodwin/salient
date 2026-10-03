"""Business entries: load, parse facts, match anchors.

A business entry is `business/<id>.md`:

    ---
    id: large-uploads
    anchors:
      - src/upload/**/*.ts
      - path: src/upload/chunker.ts
        hash: sha256:<hex>
    context:
      - src/net/retry.ts
    ---
    ## Usage
    - [verified] p50 upload is 800MB, p95 is 6GB (telemetry, 2026-08)
    ## Failure consequences
    - [claimed] a failed upload costs the customer a return site visit
    ## Deliberately not supported
    - [observed] uploads from desktop browsers older than 2 years

Every bullet in the body is a condition. The tag in brackets is its
confidence. Conditions are facts, never prescriptions.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import frontmatter

CONFIDENCE_TAGS = ("verified", "observed", "claimed")
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_TAG = re.compile(r"^\[(\w+)(?::\s*([^\]]*))?\]\s*(.*)$")


@dataclass
class Anchor:
    pattern: str
    hash: Optional[str] = None

    @property
    def is_glob(self) -> bool:
        return any(c in self.pattern for c in "*?[")

    def matches(self, rel_path: str) -> bool:
        if not self.is_glob:
            return _norm(self.pattern) == rel_path
        return _glob_regex(self.pattern).match(rel_path) is not None


@dataclass
class Condition:
    id: str
    section: str
    confidence: str  # one of CONFIDENCE_TAGS, or "untagged"
    source: str      # optional detail after the tag, e.g. "telemetry"
    text: str


@dataclass
class Entry:
    id: str
    path: str
    anchors: List[Anchor]
    context: List[str]
    conditions: List[Condition]
    body: str
    problems: List[str] = field(default_factory=list)

    def matches(self, rel_path: str) -> bool:
        return any(a.matches(rel_path) for a in self.anchors)


def _norm(p: str) -> str:
    p = p.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


_glob_cache: Dict[str, "re.Pattern[str]"] = {}


def _glob_regex(pattern: str) -> "re.Pattern[str]":
    pat = _norm(pattern)
    if pat in _glob_cache:
        return _glob_cache[pat]
    out = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    rx = re.compile("^" + "".join(out) + "$")
    _glob_cache[pat] = rx
    return rx


def parse_conditions(body: str) -> List[Condition]:
    conds: List[Condition] = []
    section = ""
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            section = s.lstrip("#").strip()
            continue
        m = _BULLET.match(line)
        if not m:
            continue
        text = m.group(1)
        conf, source = "untagged", ""
        t = _TAG.match(text)
        if t and t.group(1).lower() in CONFIDENCE_TAGS:
            conf = t.group(1).lower()
            source = (t.group(2) or "").strip()
            text = t.group(3).strip()
        conds.append(Condition(f"C{len(conds) + 1}", section, conf, source, text))
    return conds


def load_entry(path: str) -> Entry:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    problems: List[str] = []
    try:
        meta, body = frontmatter.parse(text)
    except frontmatter.FrontmatterError as e:
        return Entry(_stem(path), path, [], [], [], text, [f"frontmatter: {e}"])

    anchors: List[Anchor] = []
    raw_anchors = meta.get("anchors") or []
    if isinstance(raw_anchors, str):
        raw_anchors = [raw_anchors]
    for a in raw_anchors:
        if isinstance(a, str) and a:
            anchors.append(Anchor(_norm(a)))
        elif isinstance(a, dict) and a.get("path"):
            anchors.append(Anchor(_norm(str(a["path"])), a.get("hash") or None))
        else:
            problems.append(f"bad anchor: {a!r}")
    if not anchors:
        problems.append("no anchors: this entry can never fire")

    context = meta.get("context") or []
    if isinstance(context, str):
        context = [context]
    context = [_norm(str(c)) for c in context if c]

    conditions = parse_conditions(body)
    if not conditions:
        problems.append("no conditions (bullet lines) in body")
    untagged = [c.id for c in conditions if c.confidence == "untagged"]
    if untagged:
        problems.append(f"untagged conditions: {', '.join(untagged)} (use [verified]/[observed]/[claimed])")

    return Entry(str(meta.get("id") or _stem(path)), path, anchors, context, conditions, body, problems)


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def load_all(business_dir: str) -> List[Entry]:
    if not os.path.isdir(business_dir):
        return []
    entries = []
    for name in sorted(os.listdir(business_dir)):
        if name.endswith(".md") and not name.startswith((".", "_")) and name.lower() != "readme.md":
            entries.append(load_entry(os.path.join(business_dir, name)))
    return entries


def match(entries: List[Entry], rel_path: str) -> List[Entry]:
    rel = _norm(rel_path)
    return [e for e in entries if e.matches(rel)]


def file_hash(data: str) -> str:
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def drift(entry: Entry, rel_path: str, before_text: Optional[str]) -> Optional[bool]:
    """True if a hashed anchor for this exact path no longer matches the
    pre-edit content; None if there is nothing to compare."""
    if before_text is None:
        return None
    for a in entry.anchors:
        if a.hash and not a.is_glob and a.matches(_norm(rel_path)):
            return file_hash(before_text) != a.hash
    return None


def to_rel(path: str, root: str) -> Optional[str]:
    """Project-relative POSIX path, or None if outside the project."""
    try:
        ap = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
        ar = os.path.realpath(root)
    except (OSError, ValueError):
        return None
    if ap != ar and not ap.startswith(ar.rstrip(os.sep) + os.sep):
        return None
    return _norm(os.path.relpath(ap, ar))
