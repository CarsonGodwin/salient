"""Model backends for the critique call.

Order in auto mode:
  cmd  SALIENT_CRITIC_CMD is set: run it, prompt on stdin, response on stdout.
       Used by tests and for custom routing.
  api  ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN present: one HTTPS call to
       the Messages API. Fastest path, no child process, no hook recursion.
  cli  `claude -p`: reuses whatever auth Claude Code already has (subscription,
       Bedrock, Vertex). Spawned with SALIENT_INNER=1 so our own hook exits
       immediately inside the child.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Dict, Optional, Tuple

INNER_ENV = "SALIENT_INNER"


class CriticError(RuntimeError):
    pass


_CLAUDE_CANDIDATES = (
    "~/.claude/local/claude",
    "~/.local/bin/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
)


def find_claude():
    """`claude` is often a shell alias, which subprocesses can't see."""
    explicit = os.environ.get("SALIENT_CLAUDE_BIN")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if found:
        return found
    for c in _CLAUDE_CANDIDATES:
        p = os.path.expanduser(c)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def choose_backend(pref: str) -> str:
    if pref in ("cmd", "api", "cli"):
        return pref
    if os.environ.get("SALIENT_CRITIC_CMD"):
        return "cmd"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "api"
    if find_claude():
        return "cli"
    raise CriticError("no critic backend: claude CLI not found (set SALIENT_CLAUDE_BIN to its path), "
                      "or set ANTHROPIC_API_KEY or SALIENT_CRITIC_CMD")


def _child_env() -> Dict[str, str]:
    env = dict(os.environ)
    env[INNER_ENV] = "1"
    # Same neutralization the security-guidance plugin applies to its child
    # claude: stale fd-passing vars make the inner CLI misbehave.
    for k in ("CLAUDE_CODE_WEBSOCKET_AUTH_FILE_DESCRIPTOR",
              "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
              "CLAUDE_CODE_INCLUDE_PARTIAL_MESSAGES"):
        env[k] = ""
    return env


def _run_cmd(prompt: str, timeout: float) -> str:
    cmd = os.environ.get("SALIENT_CRITIC_CMD", "")
    if not cmd:
        raise CriticError("SALIENT_CRITIC_CMD is empty")
    r = subprocess.run(cmd, shell=True, input=prompt, capture_output=True, text=True,
                       timeout=timeout, env=_child_env())
    if r.returncode != 0:
        raise CriticError(f"critic cmd exited {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout


def _run_cli(prompt: str, model: str, timeout: float) -> str:
    exe = find_claude()
    if not exe:
        raise CriticError("claude CLI not found; set SALIENT_CLAUDE_BIN")
    args = [exe, "-p", "--output-format", "text"]
    if model:
        args += ["--model", model]
    r = subprocess.run(args, input=prompt, capture_output=True, text=True,
                       timeout=timeout, env=_child_env())
    if r.returncode != 0:
        raise CriticError(f"claude -p exited {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout


def _run_api(prompt: str, model: str, timeout: float) -> str:
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if key:
        headers["x-api-key"] = key
    elif token:
        headers["authorization"] = f"Bearer {token}"
        headers["anthropic-beta"] = "oauth-2025-04-20"
    else:
        raise CriticError("no API credentials")
    body = json.dumps({
        "model": model,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(base + "/v1/messages", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise CriticError(f"API HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise CriticError(f"API unreachable: {e}") from e
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def critique(prompt: str, backend_pref: str, model: str, timeout: float) -> Tuple[str, str]:
    """Return (backend_used, raw_text). Raises CriticError on failure."""
    backend = choose_backend(backend_pref)
    try:
        if backend == "cmd":
            return backend, _run_cmd(prompt, timeout)
        if backend == "api":
            return backend, _run_api(prompt, model, timeout)
        return backend, _run_cli(prompt, model, timeout)
    except subprocess.TimeoutExpired as e:
        raise CriticError(f"{backend} timed out after {timeout}s") from e
