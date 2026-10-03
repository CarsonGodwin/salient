"""Reconstruct the before/after of an edit from the hook payload.

PreToolUse: the edit has not landed. before = file on disk, after = edit applied.
PostToolUse: the edit has landed. after = file on disk, before = edit reversed.

When an exact reconstruction is impossible (old_string missing, ambiguous
reverse), we fall back to a fragment diff of just the strings in the payload
and mark the change inexact. The critic still gets something useful.
"""
from __future__ import annotations

import difflib
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

EDIT_TOOLS = ("Edit", "Write", "MultiEdit")
MAX_DIFF_LINES = 400


@dataclass
class Change:
    file_path: str
    rel_path: str
    before: Optional[str]
    after: Optional[str]
    diff: str
    exact: bool
    new_file: bool


def target_path(tool_name: str, tool_input: Dict[str, Any]) -> Optional[str]:
    if tool_name not in EDIT_TOOLS:
        return None
    p = tool_input.get("file_path") or tool_input.get("path")
    return p if isinstance(p, str) and p else None


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _edits(tool_name: str, tool_input: Dict[str, Any]) -> List[Tuple[str, str, bool]]:
    if tool_name == "Edit":
        return [(tool_input.get("old_string") or "", tool_input.get("new_string") or "",
                 bool(tool_input.get("replace_all")))]
    if tool_name == "MultiEdit":
        return [(e.get("old_string") or "", e.get("new_string") or "", bool(e.get("replace_all")))
                for e in (tool_input.get("edits") or []) if isinstance(e, dict)]
    return []


def _write_content(tool_input: Dict[str, Any]) -> str:
    c = tool_input.get("content")
    if c is None:
        c = tool_input.get("file_text")
    return c if isinstance(c, str) else ""


def _apply(text: str, edits: List[Tuple[str, str, bool]]) -> Optional[str]:
    for old, new, all_ in edits:
        if old == "":
            if text == "":
                text = new
                continue
            return None
        if old not in text:
            return None
        text = text.replace(old, new) if all_ else text.replace(old, new, 1)
    return text


def _reverse(text: str, edits: List[Tuple[str, str, bool]]) -> Optional[str]:
    for old, new, all_ in reversed(edits):
        if new == "" or text.count(new) != 1 and not all_:
            # Empty or ambiguous replacement: cannot locate it reliably.
            return None
        if new not in text:
            return None
        text = text.replace(new, old) if all_ else text.replace(new, old, 1)
    return text


def _git_head(path: str) -> Optional[str]:
    d = os.path.dirname(path) or "."
    try:
        top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        if top.returncode != 0:
            return None
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top.stdout.strip()))
        r = subprocess.run(["git", "-C", top.stdout.strip(), "show", f"HEAD:{rel}"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _udiff(before: str, after: str, rel: str) -> str:
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                      fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=3))
    if len(lines) > MAX_DIFF_LINES:
        cut = len(lines) - MAX_DIFF_LINES
        lines = lines[:MAX_DIFF_LINES] + [f"... [{cut} more diff lines truncated]"]
    return "\n".join(lines)


def _fragment_diff(edits: List[Tuple[str, str, bool]], rel: str) -> str:
    parts = [f"--- a/{rel} (fragments; exact position unknown)", f"+++ b/{rel}"]
    for old, new, _ in edits:
        parts.append("@@")
        parts += ["-" + l for l in old.splitlines()]
        parts += ["+" + l for l in new.splitlines()]
    return "\n".join(parts[: MAX_DIFF_LINES + 2])


def build(event: str, tool_name: str, tool_input: Dict[str, Any], file_path: str, rel: str) -> Change:
    current = _read(file_path)

    if tool_name == "Write":
        content = _write_content(tool_input)
        if event == "PreToolUse":
            before, after = current, content
        else:
            before, after = _git_head(file_path), (current if current is not None else content)
        new_file = before is None
        return Change(file_path, rel, before, after, _udiff(before or "", after or "", rel), True, new_file)

    edits = _edits(tool_name, tool_input)
    if event == "PreToolUse":
        before = current or ""
        after = _apply(before, edits)
    else:
        after = current or ""
        before = _reverse(after, edits)
        if before is not None and _apply(before, edits) != after:
            before = None

    if before is None or after is None:
        return Change(file_path, rel, before, after, _fragment_diff(edits, rel), False, False)
    return Change(file_path, rel, before, after, _udiff(before, after, rel), True, False)
