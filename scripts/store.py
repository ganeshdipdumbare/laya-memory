"""Append-only, git-backed memory store. Agora's data model, one-developer scale.

    ~/.claude/laya-memory/              a git repo
      entries/<kind>/<id>-<slug>.md     immutable; one git commit each
      index.json                        DERIVED — rebuildable, never authoritative

Rules that make this work:
  * Entries are never edited. To correct one, write a new entry that `supersedes`
    it. To confirm one, write one that `verifies` it. History stays in git.
  * `parents` records what an entry builds on. That is the DAG.
  * index.json is a cache over the working tree. Delete it and run reindex.py.
"""

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(os.environ.get("LAYA_MEMORY_HOME", Path.home() / ".claude" / "laya-memory"))
ENTRIES = ROOT / "entries"
INDEX = ROOT / "index.json"

# Laya's options share one softmax, so look-alike labels split probability between
# themselves. Both lists are deliberately short and mutually unlike.
KINDS = ["skill", "gotcha", "decision", "fact"]
CATEGORIES = ["build", "runtime", "infra", "data", "workflow", "other"]

STOPWORDS = set(
    """a an the and or but if then than that this these those is are was were be been being
    it its of to in on at for from with without into over under by as not no do does did
    i we you he she they them our your my me can could should would will shall may might
    have has had how what when where which who why use used using get got make made
    error failed fail failing issue problem trying tried""".split()
)


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slugify(text, limit=48):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:limit].rstrip("-") or "untitled"


def tokenize(text):
    return {
        w
        for w in re.findall(r"[a-z0-9_.\-/]+", (text or "").lower())
        if w not in STOPWORDS and len(w) > 2
    }


# --------------------------------------------------------------------------- git


def git(*args, check=True):
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            check=check,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return exc


def ensure_repo():
    ENTRIES.mkdir(parents=True, exist_ok=True)
    if (ROOT / ".git").exists():
        return True
    result = git("init", "-q", check=False)
    if isinstance(result, Exception) or getattr(result, "returncode", 1) != 0:
        return False  # no git available — files still work, just without history
    (ROOT / ".gitignore").write_text("index.json\nindex.json.tmp\n")
    git("add", "-A", check=False)
    git("commit", "-qm", "laya-memory: init", check=False)
    return True


def commit(path, message):
    if not (ROOT / ".git").exists():
        return
    git("add", str(path), check=False)
    git("commit", "-q", "-m", message, check=False)


# ------------------------------------------------------------------ frontmatter

_SCALAR = re.compile(r"^([a-z_]+):\s*(.*)$")


def parse_entry(path):
    """Tiny frontmatter reader. Scalars and flat [a, b] lists only — no yaml dep."""
    text = Path(path).read_text()
    if not text.startswith("---\n"):
        return None, text
    head, _, body = text[4:].partition("\n---\n")
    meta = {}
    for line in head.splitlines():
        match = _SCALAR.match(line.strip())
        if not match:
            continue
        key, raw = match.group(1), match.group(2).strip()
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            meta[key] = [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
        elif raw in ("", "null", "~"):
            meta[key] = None
        else:
            meta[key] = raw.strip("'\"")
    return meta, body.lstrip("\n")


def _fm(value):
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def render_entry(meta, body):
    keys = [
        "id", "kind", "category", "title", "summary", "triggers",
        "parents", "supersedes", "verifies", "refutes", "repo", "created",
    ]
    head = "\n".join("%s: %s" % (k, _fm(meta.get(k))) for k in keys)
    return "---\n%s\n---\n\n%s\n" % (head, body.strip())


# ------------------------------------------------------------------------ write


def entry_id(kind, title, body):
    """Content-addressed. Identical content captured twice collides — that is the
    dedup, and it is free."""
    digest = hashlib.sha1(("%s|%s|%s" % (kind, title.strip(), body.strip())).encode())
    return digest.hexdigest()[:10]


def repo_label():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(out.stdout.strip()).name
    except Exception:
        return Path.cwd().name


def add(kind, category, title, summary, body, triggers=None,
        parents=None, supersedes=None, verifies=None, refutes=None):
    """Write one immutable entry. Returns (record, created: bool)."""
    ensure_repo()
    kind = kind if kind in KINDS else "gotcha"
    category = category if category in CATEGORIES else "other"
    eid = entry_id(kind, title, body)

    path = ENTRIES / kind / ("%s-%s.md" % (eid, slugify(title)))
    if path.exists():
        return index_record(path), False  # exact duplicate; nothing to do

    meta = {
        "id": eid,
        "kind": kind,
        "category": category,
        "title": title,
        "summary": summary,
        "triggers": triggers or [],
        "parents": parents or [],
        "supersedes": supersedes,
        "verifies": verifies,
        "refutes": refutes,
        "repo": repo_label(),
        "created": now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_entry(meta, body))
    commit(path, "%s(%s): %s" % (kind, category, title[:60]))

    index = load_index()
    index["entries"] = [e for e in index["entries"] if e["id"] != eid]
    index["entries"].append(index_record(path))
    _apply_edges(index)
    save_index(index)
    return index_record(path), True


# ------------------------------------------------------------------------ index


def index_record(path):
    meta, body = parse_entry(path)
    meta = meta or {}
    keywords = tokenize(
        " ".join(
            [meta.get("title") or "", meta.get("summary") or "", body[:1200]]
            + (meta.get("triggers") or [])
        )
    )
    return {
        "id": meta.get("id"),
        "kind": meta.get("kind"),
        "category": meta.get("category"),
        "title": meta.get("title"),
        "summary": meta.get("summary"),
        "triggers": meta.get("triggers") or [],
        "supersedes": meta.get("supersedes"),
        "verifies": meta.get("verifies"),
        "refutes": meta.get("refutes"),
        "repo": meta.get("repo"),
        "created": meta.get("created"),
        "path": str(path),
        "status": "active",
        "verified": 0,
        "refuted": 0,
        "hits": 0,
        "keywords": sorted(keywords)[:60],
    }


def _apply_edges(index):
    """Derive status and trust from the DAG. Pure function of the entries."""
    by_id = {e["id"]: e for e in index["entries"] if e.get("id")}
    for entry in by_id.values():
        entry["status"] = "active"
        entry["verified"] = 0
        entry["refuted"] = 0
    for entry in by_id.values():
        target = by_id.get(entry.get("supersedes") or "")
        if target:
            target["status"] = "superseded"
        target = by_id.get(entry.get("verifies") or "")
        if target:
            target["verified"] += 1
        target = by_id.get(entry.get("refutes") or "")
        if target:
            target["refuted"] += 1
            if target["refuted"] > target["verified"]:
                target["status"] = "disputed"


def load_index():
    if not INDEX.exists():
        return {"version": 2, "entries": []}
    try:
        return json.loads(INDEX.read_text())
    except json.JSONDecodeError:
        return {"version": 2, "entries": []}


def save_index(index):
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, separators=(",", ":")))
    tmp.replace(INDEX)


def reindex():
    """Rebuild index.json from the working tree. The tree is the source of truth."""
    entries = [index_record(p) for p in sorted(ENTRIES.rglob("*.md"))]
    index = {"version": 2, "entries": [e for e in entries if e.get("id")]}
    _apply_edges(index)
    save_index(index)
    return index


def record_use(ids):
    if not ids:
        return
    index = load_index()
    for entry in index["entries"]:
        if entry["id"] in ids:
            entry["hits"] = entry.get("hits", 0) + 1
            entry["last_used"] = now()
    save_index(index)


# -------------------------------------------------------------------- retrieval


def prefilter(problem, category=None, kinds=None, limit=6):
    """Cheap lexical shortlist over active entries.

    This stage exists because laya cannot rank a library in one pass: every
    question in a request shares one 512-token state. Laya gates what survives.
    """
    index = load_index()
    if not index["entries"] and ENTRIES.exists():
        index = reindex()
    query = tokenize(problem)
    scored = []
    for entry in index["entries"]:
        if entry.get("status") == "superseded":
            continue
        if kinds and entry.get("kind") not in kinds:
            continue
        overlap = len(query & set(entry.get("keywords", [])))
        same_area = bool(category) and entry.get("category") == category
        if not overlap and not same_area:
            continue  # bonuses alone must never float an unrelated entry up
        rank = overlap / (len(query) ** 0.5 + 1) if query else 0.0
        if same_area:
            rank += 0.3
        if entry.get("repo") == repo_label():
            rank += 0.2
        rank += min(entry.get("verified", 0), 3) * 0.05
        if entry.get("status") == "disputed":
            rank -= 0.4
        if rank <= 0.05:
            continue
        scored.append((rank, entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["title"] or ""))
    return [entry for _, entry in scored[:limit]]


def blurb(entry, chars=520):
    """One-paragraph rendering for laya's state. Decisive material first."""
    text = "%s. %s" % (entry.get("title") or "", entry.get("summary") or "")
    if entry.get("triggers"):
        text += " Applies when: " + "; ".join(entry["triggers"][:4]) + "."
    return text[:chars]
