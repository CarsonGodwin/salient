# salient

A Claude Code plugin that checks each edit against the real-world conditions of the feature it touches, and makes the agent revise.

Coding agents rarely fail because business context is missing. They fail because it isn't salient when they write code. In testing, a context file in the repo changed nothing about generated code. An open-ended review found the real issue but ranked it below log spam. A review scoped to the specific conditions put it first, with numbers attached. salient makes the scoped review happen automatically at edit time, and only for files where it applies.

## How it works

1. The agent edits a file.
2. The path is matched against anchors in `business/*.md`. **No match: exit in ~80ms, no model call, no output.**
3. Match: one model call reviews the diff against the named conditions in the matching business entries, plus any context files they declare.
4. Findings: injected back to the agent with an explicit instruction to revise (post mode), or the edit is refused with the findings as the reason (pre mode). No findings: silent.

Business files hold **facts, never rules**. The hook holds the imperative. Prescriptions in context files get obeyed even when they're wrong, so the file says what's true and the hook says "revise."

## Install

```
/plugin marketplace add /path/to/salient
/plugin install salient@salient
```

Needs Python 3.8+ and one of: `ANTHROPIC_API_KEY`, the `claude` CLI (reuses your Claude Code login), or `SALIENT_CRITIC_CMD`.

## Business files

One file per feature in `business/` at the project root. Nothing loads them passively, and nothing should reference them from CLAUDE.md.

```markdown
---
id: large-uploads
anchors:
  - src/upload/**/*.ts
  - path: src/upload/chunker.ts
    hash: sha256:...          # optional; `salient hash` prints it, drift is logged
context:
  - src/net/retry.ts          # unchanged files the reviewer needs to see
---
## Usage
- [verified: telemetry 2026-08] p50 upload is 800MB, p95 is 6GB
- [observed] uploads happen from job sites over LTE with frequent dropouts

## Failure consequences
- [claimed] a failed upload costs the customer a return site visit

## Deliberately not supported
- [verified: product decision] browser uploads; mobile app only
```

Every bullet is a condition. Tag each one `[verified]` (measured), `[observed]` (seen, not measured) or `[claimed]` (from memory). The reviewer weighs them accordingly.

Write the conditions that drove a decision, not the decision itself. "p95 is 6GB over LTE with dropouts" lets the reviewer reason. "Uploads must be chunked" gets obeyed blindly, even when it's wrong.

Agents cannot edit `business/`. The hook denies those writes, because an agent given the chance turns these files into implementation docs.

## Configuration

Environment variables, typically in `.claude/settings.json` under `"env"`:

| Variable | Default | |
|---|---|---|
| `SALIENT_MODE` | `post` | `pre` blocks the edit before it lands, `post` reviews after it lands, `off` |
| `SALIENT_MODEL` | `claude-sonnet-5-5` | reviewer model (api and cli backends) |
| `SALIENT_CRITIC` | `auto` | `api`, `cli`, `cmd`, or `auto` (cmd > api > cli) |
| `SALIENT_CRITIC_CMD` | | shell command: prompt on stdin, JSON on stdout |
| `SALIENT_MAX_REVIEWS` | `2` | reviews per file per user prompt (the loop cap) |
| `SALIENT_MIN_SEVERITY` | `medium` | drop findings below this |
| `SALIENT_POST_STYLE` | `context` | `block` also sets `decision: block` in post mode |
| `SALIENT_TIMEOUT` | `120` | reviewer timeout, seconds |
| `SALIENT_BUSINESS_DIR` | `business` | |
| `SALIENT_ALLOW_BUSINESS_WRITES` | off | let the agent edit business files |
| `SALIENT_DATA_DIR` | `~/.claude/salient` | state and log location |

Any failure (no backend, timeout, unparseable output) is logged, and the edit proceeds untouched.

## Try it

`examples/large-uploads` is a tiny project with one business entry. Copy it somewhere, `git init`, open Claude Code there with the plugin installed, and ask it to change how `src/upload/chunker.ts` sends the file.

## Measuring it

Every anchored edit, review, skip and error is appended to `log.jsonl`. When a project has a `business/` directory, unanchored edits are logged too, so fire rate has a denominator.

```
bin/salient validate          # parse entries, flag untagged facts, dead anchors, overlap, drift
bin/salient match <path>...   # which entries a path resolves to
bin/salient replay -100       # anchor resolution over the last 100 real commits (0 / 1 / 2+)
bin/salient report            # fire rate, interrupt rate, latency, re-review clear rate
```

`report` prints "re-reviews after a revision ... cleared N%". That's the proxy for the main question: whether the injected critique changed the code. A re-review comes back clean only if the revision addressed the findings.

## Development

```
python3 -m unittest discover -s tests
```

Stdlib only. The tests run the real hook as a subprocess against a stub critic.
