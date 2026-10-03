"""Stdlib-only tests. Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

from salient import business, change, frontmatter, prompt  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "project")
STUB = os.path.join(ROOT, "tests", "fixtures", "stub_critic.py")
HOOK = os.path.join(ROOT, "hooks", "salient_hook.py")

FINDING = json.dumps({"findings": [
    {"conditions": ["C1", "C2"], "severity": "high",
     "problem": "Single POST of a 6GB body restarts from zero on every LTE dropout.",
     "location": "upload()"},
    {"conditions": ["C3"], "severity": "low", "problem": "minor", "location": ""},
    {"conditions": [], "severity": "high", "problem": "unscoped style nit", "location": ""},
]})


class FrontmatterTest(unittest.TestCase):
    def test_lists_dicts_and_inline(self):
        meta, body = frontmatter.parse(
            "---\nid: x\nanchors:\n  - a/**/*.cs\n  - path: a/b.cs\n    hash: sha256:ab\n"
            "context: [c.cs, d.cs]\n---\nbody\n")
        self.assertEqual(meta["id"], "x")
        self.assertEqual(meta["anchors"][0], "a/**/*.cs")
        self.assertEqual(meta["anchors"][1], {"path": "a/b.cs", "hash": "sha256:ab"})
        self.assertEqual(meta["context"], ["c.cs", "d.cs"])
        self.assertEqual(body, "body\n")

    def test_inline_comments(self):
        meta, _ = frontmatter.parse("---\nanchors:\n  - a/**/*.cs   # todo\n  - path: b.cs # x\n"
                                    "id: 'a # b'\n---\n")
        self.assertEqual(meta["anchors"], ["a/**/*.cs", {"path": "b.cs"}])
        self.assertEqual(meta["id"], "a # b")

    def test_no_frontmatter(self):
        self.assertEqual(frontmatter.parse("hello"), ({}, "hello"))

    def test_unclosed_raises(self):
        with self.assertRaises(frontmatter.FrontmatterError):
            frontmatter.parse("---\nid: x\n")


class BusinessTest(unittest.TestCase):
    def setUp(self):
        self.entries = business.load_all(os.path.join(FIXTURE, "business"))
        self.e = self.entries[0]

    def test_conditions_and_tags(self):
        self.assertEqual(len(self.e.conditions), 4)
        c1 = self.e.conditions[0]
        self.assertEqual((c1.id, c1.confidence, c1.source), ("C1", "verified", "telemetry 2026-08"))
        self.assertEqual(c1.section, "Usage")
        self.assertEqual(self.e.conditions[2].confidence, "claimed")
        self.assertEqual(self.e.problems, [])

    def test_glob_matching(self):
        m = lambda p: [e.id for e in business.match(self.entries, p)]
        self.assertEqual(m("src/upload/chunker.ts"), ["large-uploads"])
        self.assertEqual(m("src/upload/deep/nested/x.ts"), ["large-uploads"])
        self.assertEqual(m("src/upload/chunker.js"), [])
        self.assertEqual(m("src/other.ts"), [])

    def test_star_does_not_cross_dirs(self):
        a = business.Anchor("src/*.ts")
        self.assertTrue(a.matches("src/a.ts"))
        self.assertFalse(a.matches("src/x/a.ts"))

    def test_untagged_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.md")
            with open(p, "w") as f:
                f.write("---\nanchors: [a.cs]\n---\n- plain fact\n")
            self.assertTrue(any("untagged" in s for s in business.load_entry(p).problems))

    def test_to_rel_rejects_outside(self):
        self.assertIsNone(business.to_rel("/etc/passwd", FIXTURE))
        self.assertEqual(business.to_rel(os.path.join(FIXTURE, "src/other.ts"), FIXTURE), "src/other.ts")


class ChangeTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "f.txt")
        with open(self.p, "w") as f:
            f.write("a\nb\nc\n")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_pre_edit_applies(self):
        ch = change.build("PreToolUse", "Edit", {"file_path": self.p, "old_string": "b", "new_string": "B"},
                          self.p, "f.txt")
        self.assertTrue(ch.exact)
        self.assertEqual(ch.after, "a\nB\nc\n")
        self.assertIn("-b", ch.diff)
        self.assertIn("+B", ch.diff)

    def test_post_edit_reverses(self):
        with open(self.p, "w") as f:
            f.write("a\nB\nc\n")
        ch = change.build("PostToolUse", "Edit", {"file_path": self.p, "old_string": "b", "new_string": "B"},
                          self.p, "f.txt")
        self.assertTrue(ch.exact)
        self.assertEqual(ch.before, "a\nb\nc\n")

    def test_missing_old_string_is_inexact(self):
        ch = change.build("PreToolUse", "Edit", {"file_path": self.p, "old_string": "zzz", "new_string": "y"},
                          self.p, "f.txt")
        self.assertFalse(ch.exact)
        self.assertIn("fragments", ch.diff)

    def test_multiedit(self):
        ti = {"file_path": self.p, "edits": [{"old_string": "a", "new_string": "A"},
                                              {"old_string": "c", "new_string": "C"}]}
        ch = change.build("PreToolUse", "MultiEdit", ti, self.p, "f.txt")
        self.assertEqual(ch.after, "A\nb\nC\n")

    def test_write_new_file(self):
        p = os.path.join(self.d, "new.txt")
        ch = change.build("PreToolUse", "Write", {"file_path": p, "content": "x\n"}, p, "new.txt")
        self.assertTrue(ch.new_file)
        self.assertIn("+x", ch.diff)


class PromptTest(unittest.TestCase):
    def test_parse_filters_unscoped_and_low(self):
        f = prompt.parse_findings(FINDING, "medium")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["conditions"], ["C1", "C2"])

    def test_parse_fenced_and_garbage(self):
        self.assertEqual(prompt.parse_findings('```json\n{"findings": []}\n```'), [])
        self.assertIsNone(prompt.parse_findings("I think it's fine"))

    def test_critique_names_conditions(self):
        entries = business.load_all(os.path.join(FIXTURE, "business"))
        p = os.path.join(FIXTURE, "src/upload/chunker.ts")
        ch = change.build("PreToolUse", "Write", {"file_path": p, "content": "x"}, p, "src/upload/chunker.ts")
        text = prompt.build_critique(entries, ch, {"src/net/retry.ts": "export const MAX_RETRIES = 3;"})
        self.assertIn("C1 [verified: telemetry 2026-08] (Usage) p50 upload size", text)
        self.assertIn("C3 [claimed]", text)
        self.assertIn("# Context: src/net/retry.ts", text)
        self.assertIn('"findings"', text)


class HookTest(unittest.TestCase):
    """Run the real hook as a subprocess with the stub critic."""

    def setUp(self):
        self.proj = tempfile.mkdtemp()
        shutil.copytree(FIXTURE, self.proj, dirs_exist_ok=True)
        self.data = tempfile.mkdtemp()
        self.prompt_out = os.path.join(self.data, "prompts.txt")

    def tearDown(self):
        shutil.rmtree(self.proj)
        shutil.rmtree(self.data)

    def run_hook(self, event, tool, tool_input, response=FINDING, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("SALIENT_", "ANTHROPIC_"))}
        e.update({"SALIENT_DATA_DIR": self.data, "SALIENT_CRITIC_CMD": f"{sys.executable} {STUB}",
                  "STUB_RESPONSE": response, "STUB_PROMPT_OUT": self.prompt_out,
                  "CLAUDE_PROJECT_DIR": self.proj})
        e.update(env)
        payload = {"session_id": "s1", "prompt_id": "p1", "hook_event_name": event, "cwd": self.proj,
                   "tool_name": tool, "tool_input": tool_input}
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                           text=True, env=e, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def prompts(self):
        with open(self.prompt_out) as f:
            return f.read()

    def log(self):
        with open(os.path.join(self.data, "log.jsonl")) as f:
            return [json.loads(l) for l in f]

    def edit(self, rel="src/upload/chunker.ts"):
        return {"file_path": os.path.join(self.proj, rel),
                "old_string": 'method: "POST"', "new_string": 'method: "PUT"'}

    def apply_edit(self, rel="src/upload/chunker.ts"):
        p = os.path.join(self.proj, rel)
        with open(p) as f:
            t = f.read()
        with open(p, "w") as f:
            f.write(t.replace('method: "POST"', 'method: "PUT"'))

    def test_unanchored_is_silent_and_skips_model(self):
        out = self.run_hook("PostToolUse", "Edit", {"file_path": os.path.join(self.proj, "src/other.ts"),
                                                     "old_string": "1", "new_string": "2"})
        self.assertIsNone(out)
        self.assertFalse(os.path.exists(self.prompt_out))
        self.assertEqual(self.log()[-1]["kind"], "edit")

    def test_post_injects_with_directive(self):
        self.apply_edit()
        out = self.run_hook("PostToolUse", "Edit", self.edit())
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn("Single POST of a 6GB body", ctx)
        self.assertIn("C1 [verified]", ctx)
        self.assertNotIn("unscoped style nit", ctx)
        self.assertTrue(ctx.strip().splitlines()[-2].startswith("Revise the change"))
        self.assertIn("MAX_RETRIES", self.prompts())  # context file sent
        row = self.log()[-1]
        self.assertEqual((row["kind"], row["injected"], row["exact"]), ("review", True, True))
        self.assertTrue(row["drift"]["large-uploads"])  # fixture hash is deliberately stale

    def test_post_ignored_in_pre_mode_and_vice_versa(self):
        self.assertIsNone(self.run_hook("PreToolUse", "Edit", self.edit()))
        self.assertIsNone(self.run_hook("PostToolUse", "Edit", self.edit(), SALIENT_MODE="pre"))

    def test_pre_denies(self):
        out = self.run_hook("PreToolUse", "Edit", self.edit(), SALIENT_MODE="pre")
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("was NOT applied", hso["permissionDecisionReason"])

    def test_no_findings_is_silent(self):
        self.apply_edit()
        self.assertIsNone(self.run_hook("PostToolUse", "Edit", self.edit(), response='{"findings": []}'))
        self.assertEqual(self.log()[-1]["injected"], False)

    def test_cap_and_previous_findings(self):
        self.apply_edit()
        self.assertIsNotNone(self.run_hook("PostToolUse", "Edit", self.edit()))
        self.assertIsNotNone(self.run_hook("PostToolUse", "Edit", self.edit()))
        self.assertIn("Findings from the previous review", self.prompts().split("=====")[1])
        self.assertIsNone(self.run_hook("PostToolUse", "Edit", self.edit()))
        self.assertEqual(self.log()[-1]["kind"], "capped")

    def test_new_prompt_resets_cap(self):
        self.apply_edit()
        for _ in range(2):
            self.run_hook("PostToolUse", "Edit", self.edit())
        e = {k: v for k, v in os.environ.items() if not k.startswith(("SALIENT_", "ANTHROPIC_"))}
        e.update({"SALIENT_DATA_DIR": self.data, "SALIENT_CRITIC_CMD": f"{sys.executable} {STUB}",
                  "STUB_RESPONSE": FINDING, "CLAUDE_PROJECT_DIR": self.proj})
        payload = {"session_id": "s1", "prompt_id": "p2", "hook_event_name": "PostToolUse",
                   "cwd": self.proj, "tool_name": "Edit", "tool_input": self.edit()}
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                           text=True, env=e)
        self.assertTrue(r.stdout.strip())

    def test_business_writes_denied_in_any_mode(self):
        p = os.path.join(self.proj, "business", "large-uploads.md")
        out = self.run_hook("PreToolUse", "Write", {"file_path": p, "content": "x"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNone(self.run_hook("PreToolUse", "Write", {"file_path": p, "content": "x"},
                                        SALIENT_ALLOW_BUSINESS_WRITES="1"))

    def test_recursion_guard(self):
        self.apply_edit()
        self.assertIsNone(self.run_hook("PostToolUse", "Edit", self.edit(), SALIENT_INNER="1"))
        self.assertFalse(os.path.exists(self.prompt_out))

    def test_critic_failure_fails_open(self):
        self.apply_edit()
        self.assertIsNone(self.run_hook("PostToolUse", "Edit", self.edit(), SALIENT_CRITIC_CMD="false"))
        self.assertEqual(self.log()[-1]["kind"], "error")

    def test_unparseable_fails_open(self):
        self.apply_edit()
        self.assertIsNone(self.run_hook("PostToolUse", "Edit", self.edit(), response="looks good to me"))
        self.assertIn("unparseable", self.log()[-1]["error"])

    def test_outside_project_ignored(self):
        self.assertIsNone(self.run_hook("PostToolUse", "Write", {"file_path": "/tmp/x.ts", "content": ""}))

    def test_post_style_block(self):
        self.apply_edit()
        out = self.run_hook("PostToolUse", "Edit", self.edit(), SALIENT_POST_STYLE="block")
        self.assertEqual(out["decision"], "block")


class CliTest(unittest.TestCase):
    def cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "bin", "salient"), "--root", FIXTURE, *args],
                              capture_output=True, text=True)

    def test_match(self):
        r = self.cli("match", "src/upload/chunker.ts", "src/other.ts")
        self.assertIn("src/upload/chunker.ts: large-uploads", r.stdout)
        self.assertIn("src/other.ts: (none)", r.stdout)

    def test_validate_reports_drift(self):
        r = self.cli("validate")
        self.assertIn("drift: src/upload/chunker.ts", r.stdout)

    def test_report(self):
        d = tempfile.mkdtemp()
        log = os.path.join(d, "log.jsonl")
        with open(log, "w") as f:
            f.write(json.dumps({"kind": "edit"}) + "\n")
            f.write(json.dumps({"kind": "review", "latency_ms": 900, "injected": True,
                                "findings": [{"severity": "high"}], "matched": ["a"]}) + "\n")
        r = self.cli("report", "--log", log)
        self.assertIn("fire rate: 50%", r.stdout)
        shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
