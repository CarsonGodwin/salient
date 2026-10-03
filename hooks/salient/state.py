"""Per-session review state: how many times a file was reviewed for the
current user prompt, and what the last findings were. This is the loop cap:
the revise edit hits the same anchored file and would otherwise fire forever.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
from typing import Any, Dict, Iterator

try:
    import fcntl  # POSIX only; on Windows we run unlocked
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")
STALE_SECONDS = 7 * 24 * 3600


def _path(data_dir: str, session_id: str) -> str:
    d = os.path.join(data_dir, "state")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, _SAFE.sub("_", session_id or "default")[:128] + ".json")


@contextlib.contextmanager
def locked(data_dir: str, session_id: str) -> Iterator[Dict[str, Any]]:
    path = _path(data_dir, session_id)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            raw = f.read()
            try:
                state = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                state = {}
            state.setdefault("reviews", {})
            yield state
            f.seek(0)
            f.truncate()
            f.write(json.dumps(state))
        finally:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)


def review_key(prompt_id: str, rel_path: str) -> str:
    return f"{prompt_id or '-'}::{rel_path}"


def cleanup(data_dir: str) -> None:
    d = os.path.join(data_dir, "state")
    cutoff = time.time() - STALE_SECONDS
    try:
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
    except OSError:
        pass
