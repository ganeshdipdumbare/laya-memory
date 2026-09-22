---
name: laya-memory
description: Durable memory for this machine — skills, gotchas, decisions and facts learned while debugging, stored as an append-only git DAG and retrieved through a local laya classifier. Use this skill at the start of any non-trivial task, the moment you hit an error, a failing build, a confusing stack trace, an unfamiliar tool, or any "why is this happening" moment — run recall first, before you start guessing. Use it again after you solve something non-obvious, to capture what worked. Also use it whenever the user says remember this, we hit this before, what did we decide about X, or asks why the project does something a certain way.
---

# laya-memory

Two commands. `recall.py` before you guess, `capture.py` after you learn.

The library lives at `~/.claude/laya-memory/` (override with `LAYA_MEMORY_HOME`). It is
**not** under `~/.claude/skills/`, and that is the whole point: entries there would sit
in context permanently, whether or not they matter. Here nothing enters context until
laya judges it relevant to the problem in front of you.

## Before you start guessing

```bash
python3 ~/.claude/skills/laya-memory/scripts/recall.py "the error or task, in your own words"
```

Run this when you hit a failure, inherit an unfamiliar area of the codebase, or start a
task that smells like one done before. Describe the *situation*, not keywords — laya
reads meaning, and a pasted error line plus one sentence of context beats a search query.

It prints nothing when nothing applies, which is the common and correct case. When it
prints hits, read the ones whose summary looks right — the path is in the output — and
say out loud which stored entry you are following, so the user can correct you early.

Flags: `--limit N` candidates (default 5), `--threshold 0.0-1.0` (default 0.70),
`--kind skill|gotcha|decision|fact` to narrow, `--all` to skip the is-this-a-snag
triage, `--json` for programmatic use.

## After you learn something

```bash
python3 ~/.claude/skills/laya-memory/scripts/capture.py \
  --title "short, searchable, states the trap" \
  --problem "what went wrong, including the exact error text" \
  --solution "what actually fixed it" \
  --why "the mechanism, if you worked it out" \
  --triggers "symptom that should surface this again; another symptom"
```

Laya decides whether it is worth keeping and what kind of note it is. It answers three
questions: was the fix non-obvious, would it recur elsewhere, is it stable rather than a
quirk of today. Fail any one and nothing is stored — **exit code 3 is a normal outcome,
not an error.** Do not rerun with `--force` to defeat it; the gate exists so the library
stays worth reading. Use `--force` only when the user explicitly asks you to remember
something.

Capture at the end of the work, not mid-stream, and only for things you had to *work
out*. A typo you fixed in ten seconds is not memory.

## Correcting and confirming — never editing

Entries are immutable. Every correction is a new entry pointing at the old one:

```bash
capture.py --verifies   <id> --title "Confirmed: ..." --problem "hit it again in X" --solution "..."
capture.py --supersedes <id> --title "Better approach: ..." --problem "..." --solution "..."
capture.py --refutes    <id> --title "Wrong: ..." --problem "..." --solution "..."
capture.py --parents <id>,<id> --title "..."    # this note builds on those
```

Superseded entries stop being retrieved but stay in git. Verifications raise an entry's
trust and its ranking; refutations mark it disputed and push it down. **If a recalled
entry turns out to be wrong or stale, say so and write the `--refutes` or `--supersedes`
entry before moving on.** A library nobody corrects rots.

Never open an entry file and edit it. Never delete one.

## Housekeeping

```bash
python3 ~/.claude/skills/laya-memory/scripts/reindex.py   # after git pull, or a hand edit
```

`index.json` is a derived cache over `entries/`. Deleting it costs nothing.

## Setup

1. Start laya: `docker compose up -d --build` in the laya repo, then wait for
   `/healthz` to return 200 (first run downloads a ~1 GB checkpoint).
2. Put this folder at `~/.claude/skills/laya-memory/`.
3. `export LAYA_URL=http://127.0.0.1:8080` if you moved the port.

If laya is unreachable, both commands degrade rather than fail: recall falls back to
keyword matching and says so, capture stores the entry unclassified. Say plainly when
you are in the degraded path — its recall is much worse.

### Optional: automatic recall

A `UserPromptSubmit` hook makes recall unconditional instead of relying on this skill
triggering. In `~/.claude/settings.json`:

```json
{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
  "command": "python3 ~/.claude/skills/laya-memory/scripts/recall.py --limit 4"}]}]}}
```

The script reads the prompt on stdin and prints nothing when nothing applies, so the
cost on an ordinary turn is one laya forward pass.

## How retrieval works, so you can reason about misses

1. One laya pass over your problem: is this a real snag, and which of six areas is it.
2. A cheap keyword-and-category shortlist over active entries — laya cannot rank the
   library itself, because every question in a request shares one 512-token state.
3. One laya pass per shortlisted entry: does it bear on this, and how directly.

So a miss is usually stage 2, not stage 1: an entry with no vocabulary in common with
how you phrased the problem never reaches laya. That is what `triggers` is for — write
the symptom in the words someone would actually hit it with. If you know something is in
there and it did not surface, rerun with `--all --threshold 0.5` and, if it turns up,
capture a `--supersedes` entry with better triggers.

See `references/design.md` for the storage format, the laya constraints that shape these
prompts, and what to change as the library grows.
