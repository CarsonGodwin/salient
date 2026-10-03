"""Append-only JSONL log. This is the measurement instrument: fire rate,
anchor precision, latency and findings all come from here (see `bin/salient report`).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

MAX_BYTES = 10 * 1024 * 1024


def log_path(data_dir: str) -> str:
    return os.environ.get("SALIENT_LOG") or os.path.join(data_dir, "log.jsonl")


def write(data_dir: str, event: Dict[str, Any]) -> None:
    try:
        path = log_path(data_dir)
        os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
        try:
            if os.path.getsize(path) > MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        event = {"ts": round(time.time(), 3), **event}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass  # logging must never break the hook
