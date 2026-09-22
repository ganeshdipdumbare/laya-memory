"""Unit tests for the pure logic in scripts/store.py — everything that doesn't
require a live laya server. Run with: python3 -m unittest discover -s tests
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import store  # noqa: E402


class TestPureHelpers(unittest.TestCase):
    def test_slugify_basic(self):
        self.assertEqual(
            store.slugify("Go test cache hides changed testdata!"),
            "go-test-cache-hides-changed-testdata",
        )

    def test_slugify_empty_falls_back_to_untitled(self):
        self.assertEqual(store.slugify(""), "untitled")
        self.assertEqual(store.slugify(None), "untitled")

    def test_slugify_respects_limit(self):
        slug = store.slugify("a " * 100, limit=48)
        self.assertLessEqual(len(slug), 48)

    def test_tokenize_drops_stopwords_and_short_tokens(self):
        tokens = store.tokenize("The build is failing because of an old cache")
        self.assertIn("build", tokens)
        self.assertIn("cache", tokens)
        self.assertNotIn("the", tokens)  # stopword
        self.assertNotIn("is", tokens)  # stopword
        self.assertNotIn("of", tokens)  # stopword
        self.assertNotIn("an", tokens)  # len <= 2
        # "failing" is deliberately a stopword too (store.py's STOPWORDS list) —
        # every debugging entry says something "failed", so it carries no
        # discriminating signal for the keyword prefilter.
        self.assertNotIn("failing", tokens)

    def test_entry_id_is_deterministic_and_content_addressed(self):
        id1 = store.entry_id("gotcha", "Title", "body text")
        id2 = store.entry_id("gotcha", "Title", "body text")
        id3 = store.entry_id("gotcha", "Title", "different body")
        self.assertEqual(id1, id2)
        self.assertEqual(len(id1), 10)
        self.assertNotEqual(id1, id3)

    def test_render_and_parse_entry_roundtrip(self):
        meta = {
            "id": "abc123def0", "kind": "gotcha", "category": "build",
            "title": "Test entry", "summary": "a summary",
            "triggers": ["trigger one", "trigger two"],
            "parents": [], "supersedes": None, "verifies": None, "refutes": None,
            "repo": "myrepo", "created": "2026-01-01T00:00:00Z",
        }
        body = "## Problem\n\nsomething broke\n\n## Resolution\n\nfixed it"
        rendered = store.render_entry(meta, body)

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(rendered)
            path = f.name
        try:
            parsed_meta, parsed_body = store.parse_entry(path)
            self.assertEqual(parsed_meta["id"], "abc123def0")
            self.assertEqual(parsed_meta["triggers"], ["trigger one", "trigger two"])
            self.assertIsNone(parsed_meta["supersedes"])
            self.assertIn("something broke", parsed_body)
        finally:
            os.unlink(path)


class TestLibraryOperations(unittest.TestCase):
    """Exercises store.add/prefilter/reindex against a throwaway library, never
    the user's real ~/.claude/laya-memory."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = (store.ROOT, store.ENTRIES, store.INDEX)
        store.ROOT = Path(self.tmpdir)
        store.ENTRIES = store.ROOT / "entries"
        store.INDEX = store.ROOT / "index.json"

    def tearDown(self):
        store.ROOT, store.ENTRIES, store.INDEX = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_is_idempotent_for_identical_content(self):
        record1, created1 = store.add("gotcha", "build", "Title", "summary", "body")
        record2, created2 = store.add("gotcha", "build", "Title", "summary", "body")
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(record1["id"], record2["id"])

    def test_prefilter_finds_by_keyword_overlap(self):
        store.add(
            "gotcha", "build", "Go test cache issue",
            "run go test -count=1", "testdata caching problem",
            triggers=["stale go test results"],
        )
        store.add(
            "gotcha", "infra", "Unrelated k8s thing",
            "summary", "body about kubernetes pods",
        )
        hits = store.prefilter("go test cache is stale")
        self.assertIn("Go test cache issue", [h["title"] for h in hits])
        self.assertNotIn("Unrelated k8s thing", [h["title"] for h in hits])

    def test_prefilter_excludes_superseded_entries(self):
        old, _ = store.add(
            "gotcha", "build", "Old approach", "old summary", "old body",
            triggers=["shared trigger phrase"],
        )
        store.add(
            "gotcha", "build", "Better approach", "new summary", "new body",
            triggers=["shared trigger phrase"], supersedes=old["id"],
        )
        titles = [h["title"] for h in store.prefilter("shared trigger phrase")]
        self.assertNotIn("Old approach", titles)
        self.assertIn("Better approach", titles)

    def test_refuted_more_than_verified_marks_disputed(self):
        claim, _ = store.add("gotcha", "build", "Claim", "summary", "body")
        store.add("gotcha", "build", "Wrong", "summary", "refutation body", refutes=claim["id"])
        index = store.reindex()
        entry = next(e for e in index["entries"] if e["id"] == claim["id"])
        self.assertEqual(entry["status"], "disputed")

    def test_verifying_raises_verified_count(self):
        claim, _ = store.add("gotcha", "build", "Claim", "summary", "body")
        store.add("gotcha", "build", "Confirmed", "summary", "confirmation body", verifies=claim["id"])
        index = store.reindex()
        entry = next(e for e in index["entries"] if e["id"] == claim["id"])
        self.assertEqual(entry["verified"], 1)
        self.assertEqual(entry["status"], "active")

    def test_reindex_rebuilds_index_from_disk(self):
        store.add("fact", "other", "Some fact", "summary", "body")
        (store.ROOT / "index.json").unlink()
        index = store.reindex()
        self.assertEqual(len(index["entries"]), 1)
        self.assertEqual(index["entries"][0]["title"], "Some fact")


if __name__ == "__main__":
    unittest.main()
