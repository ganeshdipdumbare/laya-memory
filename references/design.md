# Design notes

## Storage format

```
~/.claude/laya-memory/              git repo
  entries/<kind>/<id>-<slug>.md     immutable, one commit each
  index.json                        derived cache, gitignored
```

An entry:

```markdown
---
id: 29320bb2b9
kind: skill
category: build
title: Go test cache hides changed testdata
summary: run go test -count=1, or move the fixture where the package tracks it
triggers: [stale go test results, testdata edits not picked up]
parents: []
supersedes: null
verifies: null
refutes: null
repo: dispatch
created: 2026-09-22T10:14:02Z
---

## Problem
...
## Resolution
...
## Why it works
...
```

`id` is `sha1(kind|title|body)[:10]`. Capturing identical content twice is a no-op —
content addressing gives deduplication for nothing.

`kind` and `category` are short and mutually unlike on purpose: laya's options share one
softmax, so look-alike labels split probability between themselves.

## What was taken from Agora, and what was not

Agora (Zhang et al., NVIDIA) records autonomous research as an append-only DAG in git:
every claim is an immutable commit whose parent edges say what it builds on, with a
derived index exposing the frontier and each claim's verification status.

Taken:

- **Append-only.** Corrections are new entries with `supersedes`, not edits. You keep
  the history of what you believed and when, which is most of the value in a debugging
  log — the wrong turn is often the thing you need next time.
- **Edges as first-class.** `parents`, `supersedes`, `verifies`, `refutes`. Trust is
  derived from the graph rather than asserted.
- **Git as the substrate.** No database. Content-addressed blobs, delta packing, `git
  log` as the audit trail, and syncing across machines or sharing with a team is a push.
- **A derived index.** `index.json` is a cache rebuildable from the tree in one pass. It
  never holds anything the entries do not. Corrupt or delete it freely.

Not taken:

- The diversity-aware selection rule that stops many parallel workers collapsing onto
  one leader. That solves a multi-agent search problem you do not have.
- Frontier and neglected-branch tracking. Meaningful for a research community, noise for
  a personal library.

Worth keeping in mind: Agora's reported result is about many workers sharing state on a
research task, and the authors say the controlled comparison showing shared state
improves discovery per unit of compute is still to be done. The data model is well
argued; treat the throughput numbers as belonging to their setting, not yours.

## Laya constraints that shaped the prompts

From the server's own design notes, and each one bites:

1. **192 tokens for instructions plus all criteria, per question.** Overrun is a hard
   422. Every criterion here is one short phrase. If you add a category, shorten another.
2. **State is truncated from the end** — 512 tokens on the English checkpoint. Both
   scripts put the decisive material first and clip explicitly rather than trusting the
   server to cut somewhere sensible.
3. **Options share one softmax**, and a `choice` always needs an `other`, or leftover
   probability lands on the most plausible real label.
4. **Gate on `probabilities[choice]` or on `noul` itself, never on `confidence`** —
   confidence describes how peaked the distribution is, a different question. The code
   follows this; keep it that way if you add questions.
5. **Questions in one request cannot see each other's answers.** The capture gate sends
   five questions in one pass because they are genuinely independent. Relevance cannot
   be batched across entries, because state is shared — hence one pass per candidate.
6. **Inference is serialized per checkpoint.** The four recall threads queue on the
   server. At CPU speeds (200–500 ms/pass) a five-candidate recall is 1–3 s. Lower
   `--limit`, or rebuild the image with CUDA torch for ~33 ms passes.

The typed output guarantees the interface, not the truth. Before you trust any threshold
here, run twenty real problems through `recall.py --json` and look at where `applies`
actually lands; the defaults (0.70 relevance, 0.60/0.60/0.55 capture) are starting
points, not measurements.

## Efficiency, concretely

- **Context** is the binding constraint, not disk. The library stays out of
  `~/.claude/skills/`, so nothing is permanently resident; a turn costs one laya pass
  plus the entries that cleared the gate.
- **Disk** is trivial: entries are ~600 bytes, git packs deltas, identical captures
  collapse. Ten thousand entries is single-digit MB.
- **Query cost** is the index, never git. `index.json` is minified, holds ~250 bytes per
  entry, and is read whole. That stays comfortable to roughly 5–10k entries.

Past that, the thing to change is stage 2, not the storage: replace the keyword
prefilter with SQLite FTS5 over the same entry files, keeping laya as the gate. Nothing
about the entries or the git layout has to move.

## Tuning

| Variable | Default | Effect |
| --- | --- | --- |
| `LAYA_URL` | `http://127.0.0.1:8080` | server address |
| `LAYA_MEMORY_HOME` | `~/.claude/laya-memory` | library location |
| `LAYA_SKILLS_MODEL` | unset | set to `auto` if you loaded `english,multilingual` |
| `LAYA_MEM_APPLIES` | 0.70 | recall relevance threshold |
| `LAYA_MEM_NOVEL` / `_REUSABLE` / `_DURABLE` | 0.60 / 0.60 / 0.40 | capture gate |

Raise the capture thresholds if the library fills with obvious notes; lower
`LAYA_MEM_APPLIES` if you keep missing things you know are stored.

`_DURABLE` was lowered from its original 0.55 after a 35-case benchmark showed it was the
single tightest of the three gates — most of the real gotchas the gate wrongly rejected
were blocked on durability alone, with strong novel/reusable scores, and 0.40 recovered
several of them with no new false positives on the benchmark's trivial-item set. See the
[README's benchmark section](../README.md#benchmark-vs-mem0) for the numbers.
