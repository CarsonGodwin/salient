"""All configuration is environment variables (set them in settings.json "env")."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def data_dir() -> str:
    # Fixed default (not CLAUDE_PLUGIN_DATA) so `bin/salient report`, run by
    # hand outside Claude Code, finds the same log the hook writes.
    return os.environ.get("SALIENT_DATA_DIR") or os.path.expanduser("~/.claude/salient")


@dataclass
class Config:
    mode: str               # pre | post | off
    business_dir: str       # relative to project root unless absolute
    backend: str            # auto | api | cli | cmd
    model: str
    timeout: float
    max_reviews: int        # per file per user prompt
    min_severity: str       # high | medium | low
    allow_business_writes: bool
    log_all_edits: bool
    data_dir: str


def load() -> Config:
    mode = os.environ.get("SALIENT_MODE", "post").strip().lower()
    if mode not in ("pre", "post", "off"):
        mode = "post"
    return Config(
        mode=mode,
        business_dir=os.environ.get("SALIENT_BUSINESS_DIR", "business"),
        backend=os.environ.get("SALIENT_CRITIC", "auto").strip().lower(),
        model=os.environ.get("SALIENT_MODEL", "claude-sonnet-5-5"),
        timeout=float(_int("SALIENT_TIMEOUT", 120)),
        max_reviews=max(1, _int("SALIENT_MAX_REVIEWS", 2)),
        min_severity=os.environ.get("SALIENT_MIN_SEVERITY", "medium").strip().lower(),
        allow_business_writes=_flag("SALIENT_ALLOW_BUSINESS_WRITES", False),
        log_all_edits=_flag("SALIENT_LOG_ALL_EDITS", True),
        data_dir=data_dir(),
    )
