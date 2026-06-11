from __future__ import annotations

import numpy as np
import pytest

from app.services.semantic_dedup import SemanticDedup, _normalize_url, _text_to_shingles, _url_md5


class TestNormalizeUrl:
    def test_strip_www(self):
        assert "example.com" in _normalize_url("https://www.example.com/page")

    def test_strip_trailing_slash(self):
        result = _normalize_url("https://example.com/page/")
        assert not result.endswith("/")

    def test_strip_utm(self):
        result = _normalize_url("https://example.com/page?utm_source=test&key=val")
        assert "utm_source" not in result
        assert "key=val" in result

    def test_lowercase(self):
        result = _normalize_url("https://EXAMPLE.COM/Page")
        assert result == result.lower()


class TestUrlMd5:
    def test_deterministic(self):
        assert _url_md5("https://example.com/a") == _url_md5("https://example.com/a")

    def test_www_equivalent(self):
        assert _url_md5("https://www.example.com/a") == _url_md5("https://example.com/a")


class TestTextToShingles:
    def test_short_text(self):
        shingles = _text_to_shingles("hi", k=5)
        assert len(shingles) == 1

    def test_longer_text(self):
        shingles = _text_to_shingles("hello world test string", k=5)
        assert len(shingles) > 1

    def test_same_text_same_shingles(self):
        s1 = _text_to_shingles("polymer recycling breakthrough")
        s2 = _text_to_shingles("polymer recycling breakthrough")
        assert s1 == s2


class TestSemanticDedupUrlDedup:
    def test_url_dedup(self):
        dedup = SemanticDedup()
        urls = ["https://example.com/a", "https://example.com/b", "https://example.com/a"]
        unique = dedup.url_dedup(urls)
        assert unique == [0, 1]

    def test_url_dedup_empty(self):
        dedup = SemanticDedup()
        assert dedup.url_dedup([]) == []

    def test_url_dedup_all_unique(self):
        dedup = SemanticDedup()
        urls = ["https://a.com", "https://b.com", "https://c.com"]
        unique = dedup.url_dedup(urls)
        assert unique == [0, 1, 2]

    def test_contains_url(self):
        dedup = SemanticDedup()
        dedup.url_dedup(["https://example.com/a"])
        assert dedup.contains_url("https://example.com/a") is True
        assert dedup.contains_url("https://example.com/b") is False


class TestSemanticDedupMinHash:
    def test_minhash_similarity_identical(self):
        dedup = SemanticDedup()
        text = "polymer recycling is important for sustainability"
        indices = dedup.minhash_dedup([text, text], threshold=0.5)
        assert len(indices) == 1

    def test_minhash_similarity_different(self):
        dedup = SemanticDedup()
        texts = [
            "polymer recycling breakthrough in 2026",
            "completely unrelated topic about cooking recipes",
        ]
        indices = dedup.minhash_dedup(texts, threshold=0.7)
        assert len(indices) == 2

    def test_minhash_count(self):
        dedup = SemanticDedup()
        dedup.minhash_dedup(["text one", "text two", "text three"])
        assert dedup.minhash_count >= 1


class TestSemanticDedupContentHash:
    def test_add_and_count(self):
        dedup = SemanticDedup()
        dedup.add("hash1", "some text", np.random.rand(128).astype(np.float32))
        assert dedup.embedding_count == 1

    def test_remove(self):
        dedup = SemanticDedup()
        dedup.add("hash1", "text", np.random.rand(128).astype(np.float32))
        assert dedup.remove("hash1") is True
        assert dedup.embedding_count == 0

    def test_remove_nonexistent(self):
        dedup = SemanticDedup()
        assert dedup.remove("no-such-hash") is False


class TestSemanticDedupBulkLoad:
    def test_bulk_load_urls(self):
        dedup = SemanticDedup()
        dedup.bulk_load_urls(["https://a.com", "https://b.com"])
        assert dedup.url_count == 2

    def test_bulk_load_embeddings(self):
        dedup = SemanticDedup()
        items = [
            ("h1", "text one", np.random.rand(128).astype(np.float32)),
            ("h2", "text two", np.random.rand(128).astype(np.float32)),
        ]
        dedup.bulk_load_embeddings(items)
        assert dedup.embedding_count == 2


class TestSemanticDedupReset:
    def test_reset_clears_all(self):
        dedup = SemanticDedup()
        dedup.url_dedup(["https://a.com"])
        dedup.minhash_dedup(["some text"])
        dedup.reset()
        assert dedup.url_count == 0
        assert dedup.minhash_count == 0
        assert dedup.embedding_count == 0
