from __future__ import annotations

import hashlib
import re
from typing import Optional

import numpy as np


_TRACKING_PARAM = re.compile(
    r"(?:^|&)(utm_\w+|ref|fbclid|gclid)=[^&]*", re.IGNORECASE
)


def _normalize_url(url: str) -> str:
    url = re.sub(r"^(?:https?://)?(?:www\.)?", "", url)
    url = re.sub(r"/$", "", url)
    url = url.lower()
    if "?" in url:
        base, qs = url.split("?", 1)
        qs = _TRACKING_PARAM.sub("", qs).lstrip("&")
        url = f"{base}?{qs}" if qs else base
    return url


def _url_md5(url: str) -> str:
    return hashlib.md5(_normalize_url(url).encode()).hexdigest()


def _text_to_shingles(text: str, k: int = 5) -> set[bytes]:
    clean = text.lower()
    length = len(clean)
    if length < k:
        return {clean.encode("utf-8")}
    return {clean[i : i + k].encode("utf-8") for i in range(length - k + 1)}


class _MinHash:
    """Lightweight MinHash with numpy-backed permutations (no datasketch)."""

    _MERSENNE_PRIME = (1 << 61) - 1

    def __init__(self, num_perm: int = 128, seed: int = 42) -> None:
        self.num_perm = num_perm
        rng = np.random.RandomState(seed)
        self._a = rng.randint(1, 2**31 - 1, size=num_perm, dtype=np.uint64)
        self._b = rng.randint(0, 2**31 - 1, size=num_perm, dtype=np.uint64)
        self.hashvalues = np.full(num_perm, np.inf, dtype=np.float64)

    def update(self, shingle: bytes) -> None:
        hv = int(hashlib.sha1(shingle).hexdigest(), 16) & 0xFFFFFFFFFFFFFFFF
        phv = ((self._a.astype(np.uint64) * np.uint64(hv) + self._b) % np.uint64(self._MERSENNE_PRIME)).astype(np.float64)
        self.hashvalues = np.minimum(self.hashvalues, phv)

    def update_batch(self, shingles: set[bytes]) -> None:
        for s in shingles:
            self.update(s)

    def jaccard(self, other: _MinHash) -> float:
        if self.num_perm != other.num_perm:
            raise ValueError(f"num_perm mismatch: {self.num_perm} vs {other.num_perm}")
        return float(np.mean(np.abs(self.hashvalues - other.hashvalues) < 1e-12))


class SemanticDedup:

    def __init__(
        self,
        embedding_model_name: str = "BAAI/bge-m3",
        device: str = "cpu",
        minhash_perm: int = 128,
    ) -> None:
        self._url_fingerprints: set[str] = set()
        self._minhashes: list[_MinHash] = []
        self._embeddings: list[tuple[str, np.ndarray]] = []
        self._embedding_model: object | None = None
        self._embedding_model_name = embedding_model_name
        self._device = device
        self._minhash_perm = minhash_perm

    def url_dedup(self, urls: list[str]) -> list[str]:
        unique: list[str] = []
        for url in urls:
            fp = _url_md5(url)
            if fp not in self._url_fingerprints:
                self._url_fingerprints.add(fp)
                unique.append(url)
        return unique

    def url_dedup_readonly(self, urls: list[str]) -> list[str]:
        return [u for u in urls if _url_md5(u) not in self._url_fingerprints]

    def _make_minhash(self, text: str) -> _MinHash:
        mh = _MinHash(num_perm=self._minhash_perm)
        mh.update_batch(_text_to_shingles(text, k=5))
        return mh

    def minhash_dedup(self, texts: list[str], threshold: float = 0.70) -> list[int]:
        unique_indices: list[int] = []
        for i, text in enumerate(texts):
            mh = self._make_minhash(text)
            if any(mh.jaccard(existing) >= threshold for existing in self._minhashes):
                continue
            unique_indices.append(i)
        return unique_indices

    def _load_embedding_model(self) -> object:
        if self._embedding_model is not None:
            return self._embedding_model
        try:
            from sentence_transformers import SentenceTransformer

            self._embedding_model = SentenceTransformer(
                self._embedding_model_name, device=self._device, trust_remote_code=True
            )
        except ImportError:
            raise ImportError(
                "sentence-transformers is required for embedding-based dedup. "
                "Install with: pip install sentence-transformers"
            )
        except OSError:
            import warnings

            fallback = "all-MiniLM-L6-v2"
            warnings.warn(
                f"Could not load {self._embedding_model_name}, falling back to {fallback}"
            )
            from sentence_transformers import SentenceTransformer

            self._embedding_model = SentenceTransformer(fallback, device=self._device)
            self._embedding_model_name = fallback
        return self._embedding_model

    def _encode(self, texts: list[str]) -> np.ndarray:
        model = self._load_embedding_model()
        truncated = [t[:32000] for t in texts]
        embeddings = model.encode(
            truncated,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return np.asarray(embeddings, dtype=np.float32)

    def semantic_dedup(
        self,
        texts: list[str],
        existing_embeddings: Optional[np.ndarray] = None,
        strong_threshold: float = 0.92,
        weak_threshold: float = 0.85,
    ) -> tuple[list[int], list[int]]:
        if not texts:
            return [], []
        new_embs = self._encode(texts)
        all_existing: list[np.ndarray] = [e for _, e in self._embeddings]
        if existing_embeddings is not None and len(existing_embeddings) > 0:
            all_existing.extend(
                existing_embeddings[i] for i in range(existing_embeddings.shape[0])
            )
        if not all_existing:
            return list(range(len(texts))), []
        existing_matrix = np.stack(all_existing)
        sim_matrix = np.dot(new_embs, existing_matrix.T)
        max_sims = np.max(sim_matrix, axis=1)
        unique_indices: list[int] = []
        weak_indices: list[int] = []
        for i, sim in enumerate(max_sims):
            if sim >= strong_threshold:
                continue
            elif sim >= weak_threshold:
                weak_indices.append(i)
            else:
                unique_indices.append(i)
        return unique_indices, weak_indices

    def add(self, content_hash: str, text: str, embedding: np.ndarray) -> None:
        self._embeddings.append((content_hash, np.asarray(embedding, dtype=np.float32)))
        mh = self._make_minhash(text)
        self._minhashes.append(mh)

    def add_url(self, url: str) -> None:
        self._url_fingerprints.add(_url_md5(url))

    def remove(self, content_hash: str) -> bool:
        before = len(self._embeddings)
        self._embeddings = [(h, e) for h, e in self._embeddings if h != content_hash]
        return len(self._embeddings) < before

    def bulk_load_urls(self, urls: list[str]) -> None:
        for url in urls:
            self._url_fingerprints.add(_url_md5(url))

    def bulk_load_embeddings(
        self, items: list[tuple[str, str, np.ndarray]]
    ) -> None:
        for content_hash, text, emb in items:
            self._embeddings.append((content_hash, np.asarray(emb, dtype=np.float32)))
            self._minhashes.append(self._make_minhash(text))

    def contains_url(self, url: str) -> bool:
        return _url_md5(url) in self._url_fingerprints

    def find_similar(
        self, text: str, top_k: int = 5, threshold: float = 0.80
    ) -> list[tuple[str, float]]:
        if not self._embeddings:
            return []
        query_emb = self._encode([text])[0]
        existing_matrix = np.stack([e for _, e in self._embeddings])
        sims = np.dot(query_emb, existing_matrix.T)
        top_indices = np.argsort(sims)[::-1][:top_k]
        results: list[tuple[str, float]] = []
        for idx in top_indices:
            sim = float(sims[idx])
            if sim >= threshold:
                results.append((self._embeddings[idx][0], sim))
        return results

    def pairwise_similarity_matrix(self, texts: list[str]) -> np.ndarray:
        embs = self._encode(texts)
        return np.dot(embs, embs.T)

    def reset(self) -> None:
        self._url_fingerprints.clear()
        self._minhashes.clear()
        self._embeddings.clear()

    @property
    def url_count(self) -> int:
        return len(self._url_fingerprints)

    @property
    def minhash_count(self) -> int:
        return len(self._minhashes)

    @property
    def embedding_count(self) -> int:
        return len(self._embeddings)
