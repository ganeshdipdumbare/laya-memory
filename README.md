# laya-memory

Durable, cross-session memory for coding agents — Claude Code, Cursor, Codex CLI, or
anything else that speaks the [Agent Skills](https://agentskills.io) standard.

It is **not** a RAG index and **not** a context file the agent reads every turn. It's two
small scripts plus an append-only git log, gated by [laya](https://github.com/ganeshdipdumbare/laya),
a local classifier that decides — for every recall and every capture — whether something
actually belongs in the agent's context right now.

```
recall.py   before you start guessing
capture.py  after you learn something non-obvious
```

## Why this exists

Two failure modes on the way here:

- **Nothing persists.** Every session re-discovers the same gotcha, re-litigates the same
  decision, re-learns the same trap in the same build tool.
- **Everything persists, all the time.** A `CLAUDE.md` or rules file that accumulates every
  lesson ever learned becomes a context tax the agent pays on every single turn, whether
  or not any of it is relevant to the task at hand.

laya-memory takes a third path: entries live outside the context window entirely, in a
plain git repo, and a local classifier decides per-turn what — if anything — is worth
pulling in. Nothing enters context until something judges it relevant to the specific
problem in front of you. The judging model runs locally (no API key, no data leaves the
machine, ~200–500ms/pass on CPU).

This selectivity is deliberate on **both** ends, not just one. Most memory systems (Mem0,
for instance) optimize for recall: `add()` almost always extracts something from a
conversation, and search always returns its top-k nearest neighbors, relevant or not.
laya-memory's capture gate can refuse to store something entirely (novel? reusable?
durable? — fail any one and nothing is written), and recall can come back with nothing at
all when nothing clears the relevance bar. It stores less and answers "nothing here" more
often, on purpose — see [Benchmark](#benchmark-vs-mem0) below for what that trades away
and what it buys.

## How it works

```
you hit a snag ──▶ recall.py ──▶ laya: "is this a real snag, which area?"
                                    │
                          keyword/category shortlist over entries/
                                    │
                          laya: "does this entry bear on it, how directly?"
                                    │
                              ranked hits, or nothing

you solve it   ──▶ capture.py ──▶ laya: "novel? reusable? durable?"
                                    │
                        fails any question → nothing stored (exit 3, not an error)
                        passes all three   → entries/<kind>/<id>-<slug>.md, git commit
```

Retrieval is two laya passes, not one, because laya can't rank a whole library in a
single forward pass — every question in a request shares one 512-token state. So a cheap
keyword-and-category prefilter narrows the field first, then laya scores each shortlisted
candidate individually for relevance.

Storage is an append-only DAG (the data model is adapted from NVIDIA's
[Agora](https://arxiv.org/abs/2510.24101)): entries are immutable content-addressed
files, corrections are new entries with `supersedes`/`verifies`/`refutes` edges pointing
at the old one, and `index.json` is a derived cache you can delete and rebuild any time
from the git tree, which stays the source of truth.

Full design rationale — the storage format, the laya token-budget constraints that shape
every prompt in these scripts, and what to change as a library grows past ~10k entries —
is in [`references/design.md`](references/design.md).

## Benchmark: vs Mem0

Tested against [Mem0](https://github.com/mem0ai/mem0) (Claude Haiku 4.5 for fact
extraction, local embeddings, local vector store) on 35 coding-agent scenarios: 20 real
gotchas that should be remembered, 10 trivial one-offs that shouldn't, 5 unrelated queries
that should surface nothing.

| | laya-memory | Mem0 |
| --- | --- | --- |
| Stored the real gotchas | 12/20 (60%) | 20/20 (100%) |
| Correctly rejected trivial junk | 10/10 (100%) | 7/10 (70%) |
| Recall@5 of what it stored | 10/12 (83%) | 20/20 (100%) |
| False positives on 5 unrelated queries | 0/5 | 5/5 at Mem0's default settings* |
| External API calls | 0 | 30 (paid) |

\* Mem0's `search()` always returns its top-`k` nearest neighbors regardless of how
irrelevant they are, unless the caller tunes `threshold` itself. laya-memory's recall has
that judgment built in — a default relevance threshold of 0.70 — and can return nothing.

The numbers say plainly what the section above claims in words: **laya-memory stores and
retrieves less, on purpose.** Mem0's `add()` almost always extracts something from a
conversation and its `search()` always ranks and returns its top-k; that's the right
default for "remember everything about this conversation." laya-memory's gate can refuse
a capture outright and its recall can come back empty — the tradeoff for a library that
stays worth reading, and zero ongoing API cost (Mem0's run here cost 30 real LLM calls;
laya-memory's cost none).

Worth being honest about the other direction too: of the 8 real gotchas laya-memory's gate
turned away, most were blocked on a single axis — `durable`, the tightest of the three
capture thresholds. Lowering it from 0.55 to 0.40 recovered several of them with no new
false positives on the trivial set, so that's now the shipped default (see the tuning
table in [`references/design.md`](references/design.md) if you want to move it further).
This is a one-run, 35-case benchmark — directional, not a large-scale study.

## Quick start

**1. Run laya.**

```bash
git clone https://github.com/ganeshdipdumbare/laya
cd laya && docker compose up -d --build   # first run pulls a ~1GB checkpoint
curl localhost:8080/healthz               # wait for 200
```

**2. Install this skill.** All three tools now read the same
[`SKILL.md`](SKILL.md) format; put one real copy down and symlink it for the others so
they never drift:

```bash
git clone https://github.com/ganeshdipdumbare/laya-memory ~/.claude/skills/laya-memory
mkdir -p ~/.agents/skills ~/.cursor/skills
ln -s ~/.claude/skills/laya-memory ~/.agents/skills/laya-memory   # Codex CLI
ln -s ~/.claude/skills/laya-memory ~/.cursor/skills/laya-memory   # Cursor
```

| Tool | Reads skills from |
| --- | --- |
| Claude Code | `~/.claude/skills/` |
| Codex CLI | `~/.agents/skills/` (user-level), `.agents/skills/` (repo-level) |
| Cursor | `~/.cursor/skills/`, `~/.agents/skills/`, and `~/.claude/skills/` as a legacy fallback |

The scripts default to `~/.claude/laya-memory/` for the library itself regardless of
which tool invokes them (override with `LAYA_MEMORY_HOME`) — one shared memory across
every tool and every project on the machine, not one per tool.

**3. Use it.** The agent should reach for this on its own once the skill is installed —
that's what the `description` in `SKILL.md`'s frontmatter is for. To drive it by hand:

```bash
python3 scripts/recall.py "the error or task, in your own words"

python3 scripts/capture.py \
  --title "short, searchable, states the trap" \
  --problem "what went wrong, including the exact error text" \
  --solution "what actually fixed it" \
  --why "the mechanism, if you worked it out" \
  --triggers "symptom that should surface this again; another symptom"
```

See [`SKILL.md`](SKILL.md) for the full usage contract the agent follows — flags,
thresholds, the correction/verification workflow, and the optional `UserPromptSubmit`
hook for unconditional recall.

## What it looks like in practice

```
$ recall.py "pip install fails with SSL verify failed behind our proxy"
[gotcha] Corporate proxy breaks pip's cert chain (a91f3c2b1e, unverified) p=0.86
    set PIP_CERT to the corporate CA bundle, not REQUESTS_CA_BUNDLE
    ~/.claude/laya-memory/entries/gotcha/a91f3c2b1e-corporate-proxy-pip-cert.md
```

```
$ capture.py --title "Forgot a semicolon" --problem "..." --solution "added it"
not stored: obvious from the error itself ({'novel': 0.41, 'reusable': 0.55, 'durable': 0.36})
```

The gate rejecting the second one is the feature, not a bug — a library that stores
everything is just a slower, worse `grep`.

## Repo layout

```
SKILL.md              the contract the agent reads: when to use this, how
agents/
  openai.yaml           Codex-specific: display metadata, implicit-invocation policy
scripts/
  recall.py            entry point: query the library
  capture.py           entry point: write to the library
  store.py             entries/index read-write, git plumbing, lexical prefilter
  laya.py               minimal stdlib-only laya HTTP client
  reindex.py            rebuild index.json from entries/ on disk
references/
  design.md            storage format, what was and wasn't taken from Agora, tuning
tests/
  test_store.py         unit tests for the pure logic in store.py
```

`entries/` and `index.json` are **not** in this repo — they live in `~/.claude/laya-memory/`
(or `$LAYA_MEMORY_HOME`), a separate git repo that holds *your* captured memory. This repo
is only the skill definition: the code and the contract, not the accumulated notes.

## Requirements

- Python 3, stdlib only — nothing to `pip install`.
- `git`, for the memory library's history (recall/capture still work without it, just
  without commits).
- [laya](https://github.com/ganeshdipdumbare/laya) running locally. Both scripts degrade
  gracefully if it's unreachable — recall falls back to keyword matching, capture stores
  unclassified — and say so plainly when they do.

## Running the tests

`tests/test_store.py` covers `store.py`'s pure logic — slugify/tokenize, the content-
addressed dedup, the DAG edge derivation (supersede/verify/refute), and the lexical
prefilter — against a throwaway library, never your real one. No laya server needed:

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT
