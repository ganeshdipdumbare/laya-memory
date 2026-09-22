#!/usr/bin/env python3
"""Rebuild index.json from entries/. The tree is the source of truth; the index
is a cache. Safe to run any time, and required after `git pull` or a hand edit."""

import json
import sys
from collections import Counter

import store


def main():
    index = store.reindex()
    entries = index["entries"]
    by_status = Counter(e.get("status") for e in entries)
    by_kind = Counter(e.get("kind") for e in entries)
    if "--json" in sys.argv:
        print(json.dumps({"count": len(entries), "status": by_status, "kind": by_kind}))
        return 0
    print("%d entries at %s" % (len(entries), store.ROOT))
    print("  kind:   " + ", ".join("%s=%d" % kv for kv in sorted(by_kind.items())))
    print("  status: " + ", ".join("%s=%d" % kv for kv in sorted(by_status.items())))
    size = sum(f.stat().st_size for f in store.ENTRIES.rglob("*.md")) if store.ENTRIES.exists() else 0
    print("  on disk: %.1f KB of entries, %.1f KB index" % (
        size / 1024, store.INDEX.stat().st_size / 1024 if store.INDEX.exists() else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
