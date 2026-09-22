#!/usr/bin/env python3
"""Ask laya whether this moment is worth storing, then store it.

    capture.py --title "..." --problem "..." --solution "..." [--why "..."]
               [--triggers "a;b"] [--parents id,id]
               [--supersedes ID | --verifies ID | --refutes ID]
               [--force] [--dry-run] [--json]

Exit 0 = stored. Exit 3 = laya said not worth storing (not an error).
"""

import argparse
import json
import sys

import laya
import store

# Thresholds. Gate on the probability of the answer, never on `confidence`.
NOVEL = float(__import__("os").environ.get("LAYA_MEM_NOVEL", "0.60"))
REUSABLE = float(__import__("os").environ.get("LAYA_MEM_REUSABLE", "0.60"))
DURABLE = float(__import__("os").environ.get("LAYA_MEM_DURABLE", "0.40"))

# Instructions + all criteria must fit 192 tokens PER QUESTION. Overrun is a hard 422.
QUESTIONS = {
    "kind": {
        "type": "choice",
        "instructions": "What kind of durable note does `finding` describe?",
        "criteria": {
            "skill": "a repeatable procedure for doing something",
            "gotcha": "a trap and the way around it",
            "decision": "a choice made, and why",
            "fact": "a stable truth about this system",
        },
    },
    "category": {
        "type": "choice",
        "instructions": "Which area does `finding` belong to?",
        "criteria": {
            "build": "compiling, dependencies, packaging, toolchain",
            "runtime": "crashes or wrong behaviour while running",
            "infra": "containers, CI, cloud, networking, deploys",
            "data": "schemas, migrations, queries, serialization",
            "workflow": "repo conventions, tooling, git, process",
            "other": "none of the above fits",
        },
    },
    "novel": {
        "type": "noul",
        "instructions": "Was the resolution in `finding` non-obvious, rather than "
                        "readable straight off the error message?",
    },
    "reusable": {
        "type": "noul",
        "instructions": "Would the resolution in `finding` apply again in other "
                        "projects, not only this one file?",
    },
    "durable": {
        "type": "noul",
        "instructions": "Is `finding` a stable technique, rather than a one-off "
                        "quirk of today's local state?",
    },
}


def build_body(args):
    parts = []
    if args.problem:
        parts.append("## Problem\n\n" + args.problem.strip())
    if args.solution:
        parts.append("## Resolution\n\n" + args.solution.strip())
    if args.why:
        parts.append("## Why it works\n\n" + args.why.strip())
    return "\n\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--problem", default="")
    parser.add_argument("--solution", default="")
    parser.add_argument("--why", default="")
    parser.add_argument("--triggers", default="", help="semicolon-separated")
    parser.add_argument("--parents", default="", help="comma-separated entry ids")
    parser.add_argument("--supersedes", default=None)
    parser.add_argument("--verifies", default=None)
    parser.add_argument("--refutes", default=None)
    parser.add_argument("--kind", default=None, choices=store.KINDS)
    parser.add_argument("--category", default=None, choices=store.CATEGORIES)
    parser.add_argument("--force", action="store_true", help="skip the worth-storing gate")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    body = build_body(args)
    triggers = [t.strip() for t in args.triggers.split(";") if t.strip()]
    parents = [p.strip() for p in args.parents.split(",") if p.strip()]

    kind, category = args.kind, args.category
    verdict = {}
    reason = None

    # A verification or a correction is always worth writing: it is an edge in the
    # DAG, and the gate already ran on the entry it points at.
    linked = bool(args.verifies or args.refutes or args.supersedes)

    if not (args.force or linked) or not (kind and category):
        state = {
            "finding": laya.clip(
                "%s. %s %s" % (args.title, args.problem, args.solution), 460
            )
        }
        try:
            result = laya.predict(state, QUESTIONS)
            answers = result["answers"]
            if not kind:
                kind = laya.choice(answers, "kind")[0]
            if not category:
                category = laya.choice(answers, "category")[0]
            verdict = {
                "novel": laya.noul(answers, "novel"),
                "reusable": laya.noul(answers, "reusable"),
                "durable": laya.noul(answers, "durable"),
                "latency_ms": result.get("latency_ms"),
            }
            if not (args.force or linked):
                if verdict["novel"] < NOVEL:
                    reason = "obvious from the error itself"
                elif verdict["reusable"] < REUSABLE:
                    reason = "local to this one file"
                elif verdict["durable"] < DURABLE:
                    reason = "a transient quirk"
        except laya.LayaUnavailable as exc:
            # Degrade to storing it. A missing classifier is no reason to lose
            # the finding; reindex.py can reclassify later.
            verdict = {"error": str(exc)}
            kind = kind or "gotcha"
            category = category or "other"

    if reason and not args.force:
        out = {"stored": False, "reason": reason, "verdict": verdict}
        print(json.dumps(out) if args.json else "not stored: %s (%s)" % (reason, verdict))
        return 3

    if args.dry_run:
        print(json.dumps({"kind": kind, "category": category, "verdict": verdict}, indent=2))
        return 0

    summary = (args.solution or args.problem or args.title).strip()
    summary = " ".join(summary.split())[:240]

    record, created = store.add(
        kind=kind or "gotcha",
        category=category or "other",
        title=args.title,
        summary=summary,
        body=body,
        triggers=triggers,
        parents=parents,
        supersedes=args.supersedes,
        verifies=args.verifies,
        refutes=args.refutes,
    )
    out = {
        "stored": True,
        "new": created,
        "id": record["id"],
        "kind": record["kind"],
        "category": record["category"],
        "path": record["path"],
        "verdict": verdict,
    }
    if args.json:
        print(json.dumps(out))
    else:
        print("%s %s [%s/%s] %s" % (
            "stored" if created else "already stored",
            record["id"], record["kind"], record["category"], record["path"],
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
