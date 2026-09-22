#!/usr/bin/env python3
"""Pull only the entries that bear on what is happening right now.

    recall.py "the thing we are stuck on"       (or pipe it on stdin)
    recall.py --limit 4 --threshold 0.7 --json

Two stages, because laya cannot rank a library in one pass:
  1. one pass over the problem  -> category + is-this-a-real-snag
  2. one pass per shortlisted entry -> does it apply, and how directly
"""

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import laya
import store

APPLIES = float(os.environ.get("LAYA_MEM_APPLIES", "0.70"))

TRIAGE = {
    "snag": {
        "type": "noul",
        "instructions": "Is the author of `problem` blocked by a concrete technical "
                        "failure, rather than asking a general question?",
    },
    "category": {
        "type": "choice",
        "instructions": "Which area does `problem` belong to?",
        "criteria": {
            "build": "compiling, dependencies, packaging, toolchain",
            "runtime": "crashes or wrong behaviour while running",
            "infra": "containers, CI, cloud, networking, deploys",
            "data": "schemas, migrations, queries, serialization",
            "workflow": "repo conventions, tooling, git, process",
            "other": "none of the above fits",
        },
    },
}

RELEVANCE = {
    "applies": {
        "type": "noul",
        "instructions": "Does the note in `note` bear on the situation in `problem`?",
    },
    "directness": {
        "type": "score",
        "instructions": "How closely does `note` match `problem`?",
        "criteria": ["unrelated", "same area only", "same class of failure", "near identical"],
    },
}


def gate(problem, entry):
    state = {
        "problem": laya.clip(problem, 260),  # decisive material first: state is cut from the end
        "note": laya.clip(store.blurb(entry), 180),
    }
    try:
        answers = laya.predict(state, RELEVANCE)["answers"]
        return entry, laya.noul(answers, "applies"), laya.score(answers, "directness")
    except laya.LayaUnavailable:
        return entry, None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("problem", nargs="*")
    parser.add_argument("--limit", type=int, default=5, help="candidates sent to laya")
    parser.add_argument("--threshold", type=float, default=APPLIES)
    parser.add_argument("--kind", action="append", choices=store.KINDS)
    parser.add_argument("--all", action="store_true", help="skip the snag triage")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    problem = " ".join(args.problem).strip() or sys.stdin.read().strip()
    if not problem:
        parser.error("give me the problem, as an argument or on stdin")

    category, degraded = None, None
    if not args.all:
        try:
            answers = laya.predict({"problem": laya.clip(problem, 440)}, TRIAGE)["answers"]
            if laya.noul(answers, "snag") < 0.45:
                print(json.dumps({"hits": [], "skipped": "not a snag"})
                      if args.json else "no recall: not a stuck moment")
                return 0
            label, prob = laya.choice(answers, "category")
            category = label if prob >= 0.5 else None
        except laya.LayaUnavailable as exc:
            degraded = str(exc)

    candidates = store.prefilter(problem, category=category, kinds=args.kind, limit=args.limit)
    if not candidates:
        print(json.dumps({"hits": []}) if args.json else "no recall: nothing in the library matches")
        return 0

    if degraded:
        hits = [(entry, None, None) for entry in candidates[:3]]
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            hits = list(pool.map(lambda e: gate(problem, e), candidates))
        hits = [h for h in hits if h[1] is None or h[1] >= args.threshold]
        hits.sort(key=lambda h: (-(h[1] or 0), -(h[2] or 0)))

    store.record_use([entry["id"] for entry, _, _ in hits])

    if args.json:
        print(json.dumps({
            "degraded": degraded,
            "category": category,
            "hits": [
                {"id": e["id"], "kind": e["kind"], "title": e["title"],
                 "summary": e["summary"], "path": e["path"],
                 "status": e.get("status"), "verified": e.get("verified", 0),
                 "applies": p, "directness": d}
                for e, p, d in hits
            ],
        }))
        return 0

    if degraded:
        print("laya unavailable, falling back to keyword match: %s\n" % degraded)
    if not hits:
        print("no recall: candidates existed but none cleared %.2f" % args.threshold)
        return 0
    for entry, prob, direct in hits:
        trust = "verified x%d" % entry.get("verified", 0) if entry.get("verified") else "unverified"
        if entry.get("status") == "disputed":
            trust = "DISPUTED"
        head = "[%s] %s (%s, %s)" % (entry["kind"], entry["title"], entry["id"], trust)
        if prob is not None:
            head += " p=%.2f" % prob
        print(head)
        print("    %s" % entry["summary"])
        print("    %s\n" % entry["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
