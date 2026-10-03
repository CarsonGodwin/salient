#!/usr/bin/env python3
"""salient hook entry point (PreToolUse and PostToolUse on Edit|Write|MultiEdit).

Flow:
  1. Edit lands on a path (or is about to, in pre mode).
  2. Path matched against business/*.md anchors. No match: exit silently, no model call.
  3. Match: one scoped critique call with the diff, the matched conditions, and
     any declared context files.
  4. Findings: injected with a revise directive (post) or the edit is denied
     with the findings as the reason (pre). No findings: silent.

Fails open: any error is logged and the edit proceeds untouched.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from salient import business, change, config, critic, prompt, record, state  # noqa: E402

DIFF_LOG_CHARS = 4000


def _emit(obj) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def _under(path: str, directory: str) -> bool:
    try:
        p, d = os.path.realpath(path), os.path.realpath(directory)
    except (OSError, ValueError):
        return False
    return p == d or p.startswith(d.rstrip(os.sep) + os.sep)


def _read_context(entries, root: str, exclude_rel: str):
    out = {}
    for e in entries:
        for rel in e.context:
            if rel == exclude_rel or rel in out:
                continue
            try:
                with open(os.path.join(root, rel), "r", encoding="utf-8", errors="replace") as f:
                    out[rel] = f.read()
            except OSError:
                continue
    return out


def run(data: dict, cfg: config.Config) -> int:
    event = data.get("hook_event_name", "")
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    file_path = change.target_path(tool, tool_input)
    if not file_path:
        return 0

    root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    if not os.path.isabs(file_path):
        file_path = os.path.join(root, file_path)
    rel = business.to_rel(file_path, root)
    if rel is None:
        return 0
    bdir = cfg.business_dir if os.path.isabs(cfg.business_dir) else os.path.join(root, cfg.business_dir)

    session_id = data.get("session_id", "") or "default"
    prompt_id = data.get("prompt_id", "") or ""
    base = {"session": session_id, "prompt": prompt_id, "event": event, "mode": cfg.mode,
            "tool": tool, "file": rel, "cwd": root}

    # Business files are human-maintained. Given the chance, an agent rewrites
    # them into implementation docs, so edits to them are refused.
    if event == "PreToolUse" and not cfg.allow_business_writes and _under(file_path, bdir):
        record.write(cfg.data_dir, {**base, "kind": "business_write_denied"})
        _emit({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"{prompt.PROVENANCE}\n{cfg.business_dir}/ holds human-confirmed facts about how this "
                "software is used. Agents do not edit these files. If something in one looks wrong, "
                "tell the user instead."),
        }})
        return 0

    if cfg.mode == "off":
        return 0
    if event != ("PreToolUse" if cfg.mode == "pre" else "PostToolUse"):
        return 0
    if _under(file_path, bdir):
        return 0

    entries = business.load_all(bdir)
    matched = [e for e in business.match(entries, rel) if e.conditions]
    base["matched"] = [e.id for e in matched]
    if not matched:
        if cfg.log_all_edits and entries:
            record.write(cfg.data_dir, {**base, "kind": "edit"})
        return 0

    ch = change.build(event, tool, tool_input, file_path, rel)
    base["exact"] = ch.exact
    base["drift"] = {e.id: business.drift(e, rel, ch.before) for e in matched}
    if event == "PreToolUse" and tool != "Write" and not ch.exact:
        # old_string not found: the tool call is about to fail on its own.
        record.write(cfg.data_dir, {**base, "kind": "skip", "reason": "edit_will_fail"})
        return 0

    key = state.review_key(prompt_id, rel)
    with state.locked(cfg.data_dir, session_id) as st:
        rec = st["reviews"].get(key) or {"count": 0, "findings": None}
    if rec["count"] >= cfg.max_reviews:
        record.write(cfg.data_dir, {**base, "kind": "capped", "count": rec["count"]})
        return 0

    ctx = _read_context(matched, root, rel)
    text = prompt.build_critique(matched, ch, ctx, previous=rec.get("findings") or None)

    t0 = time.monotonic()
    try:
        backend, raw = critic.critique(text, cfg.backend, cfg.model, cfg.timeout)
    except critic.CriticError as e:
        record.write(cfg.data_dir, {**base, "kind": "error", "error": str(e),
                                    "latency_ms": int((time.monotonic() - t0) * 1000)})
        return 0
    latency = int((time.monotonic() - t0) * 1000)

    findings = prompt.parse_findings(raw, cfg.min_severity)
    if findings is None:
        record.write(cfg.data_dir, {**base, "kind": "error", "error": "unparseable critic output",
                                    "raw": raw[:1000], "latency_ms": latency, "backend": backend})
        return 0

    with state.locked(cfg.data_dir, session_id) as st:
        cur = st["reviews"].get(key) or {"count": 0}
        st["reviews"][key] = {"count": cur.get("count", 0) + 1, "findings": findings,
                              "ts": time.time()}
        review_index = st["reviews"][key]["count"]

    record.write(cfg.data_dir, {
        **base, "kind": "review", "review_index": review_index, "backend": backend,
        "model": cfg.model if backend != "cmd" else "cmd", "latency_ms": latency,
        "prompt_chars": len(text), "findings": findings, "injected": bool(findings),
        "conditions": len(prompt.numbered_conditions(matched)),
        "diff": ch.diff[:DIFF_LOG_CHARS],
    })

    if not findings:
        return 0

    message = prompt.build_injection(event, matched, ch, findings)
    summary = f"salient: {len(findings)} finding(s) on {rel} against " + \
              ", ".join(e.id for e in matched)
    if event == "PreToolUse":
        _emit({"systemMessage": summary, "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }})
    else:
        out = {"systemMessage": summary, "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        }}
        if os.environ.get("SALIENT_POST_STYLE", "context").lower() == "block":
            out["decision"] = "block"
            out["reason"] = message
        _emit(out)
    return 0


def main() -> int:
    if os.environ.get(critic.INNER_ENV):
        return 0  # we are inside our own critic's child claude
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    cfg = config.load()
    if random.random() < 0.05:
        state.cleanup(cfg.data_dir)
    try:
        return run(data, cfg)
    except Exception as e:  # fail open, always
        record.write(cfg.data_dir, {"kind": "crash", "error": f"{type(e).__name__}: {e}"})
        return 0


if __name__ == "__main__":
    sys.exit(main())
