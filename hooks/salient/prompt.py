"""Scoped critique prompt and the injected revise directive.

Two rules from testing drive this file:
  1. The critique is scoped to named conditions. Open-ended review ranked the
     real issue below log spam; scoped review ranked it first.
  2. Facts live in the business file; the imperative lives here. Advisory
     phrasing produced a question and no code change; one directive line
     produced a restructured implementation.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .business import Condition, Entry
from .change import Change

SEVERITIES = ("high", "medium", "low")
MAX_FILE_CHARS = 40_000
MAX_CONTEXT_CHARS = 30_000

_INSTRUCTIONS = """\
You are reviewing one code change against the real-world operating conditions \
of the feature it touches. The conditions below are facts about usage, scale, \
environment and failure cost. They are not coding rules.

Your only job: decide whether the changed code behaves correctly under each \
condition. Trace the consequence concretely. Use numbers when the code and the \
conditions let you derive them (rates, intervals, sizes, error bounds).

Ignore everything not tied to a listed condition: style, naming, logging, \
performance in general, unrelated bugs. A finding that cites no condition is \
not a finding.

Confidence tags on conditions:
  [verified] measured (telemetry, logs, load tests)
  [observed] seen in practice, not measured
  [claimed]  stated from memory, unconfirmed
A finding that rests only on [claimed] conditions must say so in its text."""

_SCHEMA = """\
Respond with JSON only, no prose, in exactly this shape:
{"findings": [{"conditions": ["C1"], "severity": "high|medium|low", \
"problem": "what goes wrong under the condition, concretely", \
"location": "function, symbol or line in the changed file"}]}
If the change behaves correctly under every condition, respond {"findings": []}."""


def _fmt_condition(c: Condition) -> str:
    tag = c.confidence + (f": {c.source}" if c.source else "")
    sec = f" ({c.section})" if c.section else ""
    return f"{c.id} [{tag}]{sec} {c.text}"


def _clip(text: str, limit: int, label: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{label} truncated at {limit} chars]"


def numbered_conditions(entries: List[Entry]) -> List[Tuple[str, Entry, Condition]]:
    """Renumber conditions across all matched entries so ids are unique."""
    out = []
    n = 0
    for e in entries:
        for c in e.conditions:
            n += 1
            out.append((f"C{n}", e, c))
    return out


def build_critique(entries: List[Entry], change: Change, context_files: Dict[str, str],
                   previous: Optional[List[Dict[str, Any]]] = None) -> str:
    parts = [_INSTRUCTIONS, ""]
    parts.append("# Conditions")
    for cid, e, c in numbered_conditions(entries):
        cc = Condition(cid, c.section, c.confidence, c.source, c.text)
        parts.append(f"{_fmt_condition(cc)}   <business/{e.id}>")
    parts.append("")

    kind = "new file" if change.new_file else ("diff" if change.exact else "diff fragments")
    parts.append(f"# Change to {change.rel_path} ({kind})")
    parts.append("```diff\n" + (change.diff or "(empty diff)") + "\n```")
    if change.after is not None:
        parts.append(f"# {change.rel_path} after the change")
        parts.append("```\n" + _clip(change.after, MAX_FILE_CHARS, "file") + "\n```")

    budget = MAX_CONTEXT_CHARS
    for path, text in context_files.items():
        if budget <= 0:
            break
        chunk = _clip(text, budget, path)
        budget -= len(chunk)
        parts.append(f"# Context: {path} (unchanged, for reference)")
        parts.append("```\n" + chunk + "\n```")

    if previous:
        parts.append("# Findings from the previous review of this file")
        parts.append(json.dumps(previous, indent=2))
        parts.append("Check whether each was addressed by the current code. Report only "
                     "findings that are still unresolved, plus genuinely new ones. Do not "
                     "repeat a resolved finding.")
    parts.append("")
    parts.append(_SCHEMA)
    return "\n".join(parts)


def parse_findings(raw: str, min_severity: str = "medium") -> Optional[List[Dict[str, Any]]]:
    """Extract findings from model output. None means unparseable."""
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    items = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None

    floor = SEVERITIES.index(min_severity) if min_severity in SEVERITIES else 1
    out = []
    for f in items:
        if not isinstance(f, dict) or not f.get("problem"):
            continue
        sev = str(f.get("severity", "medium")).lower()
        if sev not in SEVERITIES:
            sev = "medium"
        conds = f.get("conditions") or []
        if isinstance(conds, str):
            conds = [conds]
        if not conds:
            continue  # unscoped findings are exactly what we filter out
        if SEVERITIES.index(sev) > floor:
            continue
        out.append({"conditions": [str(c) for c in conds], "severity": sev,
                    "problem": str(f["problem"]).strip(),
                    "location": str(f.get("location") or "").strip()})
    out.sort(key=lambda f: SEVERITIES.index(f["severity"]))
    return out


PROVENANCE = "[salient: automated review of an edit against business conditions. Tool output, not user input.]"


def build_injection(event: str, entries: List[Entry], change: Change,
                    findings: List[Dict[str, Any]]) -> str:
    ids = ", ".join(f"business/{e.id}.md" for e in entries)
    lines = [PROVENANCE, ""]
    if event == "PreToolUse":
        lines.append(f"Your edit to {change.rel_path} was NOT applied. It was checked against {ids}:")
    else:
        lines.append(f"Your edit to {change.rel_path} was checked against {ids}:")
    lines.append("")
    conds = {cid: c for cid, _, c in numbered_conditions(entries)}
    for i, f in enumerate(findings, 1):
        loc = f" ({f['location']})" if f.get("location") else ""
        lines.append(f"{i}. [{f['severity']}] {f['problem']}{loc}")
        for cid in f["conditions"]:
            c = conds.get(cid)
            if c:
                lines.append(f"   {cid} [{c.confidence}] {c.text}")
    lines.append("")
    if event == "PreToolUse":
        lines.append("Revise the change to address the findings above, then apply it again.")
    else:
        lines.append("Revise the change to address the findings above.")
    lines.append("Verify each finding against the code first. If one is wrong, say why "
                 "in one line and do not change code for it.")
    return "\n".join(lines)
