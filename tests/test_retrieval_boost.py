"""Hybrid-retrieval precision boosters: relevance propagation, feature boost,
exact-formula fast path, and pluggable rerank."""
import pytest

from graph_rag.retrieval.bm25_retriever import BM25Retriever
from graph_rag.retrieval.hybrid_retriever import HybridRetriever
from graph_rag.retrieval.rerankers import BiEncoderReranker
from graph_rag.retrieval.vector_retriever import VectorHit


def _hit(cid, text="t", source="s", score=0.0, relevance=0.0, meta=None):
    return VectorHit(text=text, source=source, score=score, chunk_id=cid,
                     relevance=relevance, metadata=meta or {})


# ── VectorHit shape ─────────────────────────────────────────────────────────

def test_vectorhit_defaults_backward_compatible():
    h = VectorHit(text="x", source="s", score=0.5, chunk_id="c")
    assert h.relevance == 0.0 and h.metadata == {}


# ── RRF keeps a real relevance, not just the tiny RRF score ──────────────────

def test_rrf_propagates_max_relevance_and_metadata():
    vec = [_hit("a", relevance=0.6, meta={"has_formula": True})]
    bm25 = [_hit("a", relevance=0.9), _hit("b", relevance=0.3)]
    fused = HybridRetriever._rrf_fuse(vec, bm25, rrf_k=60)
    by_id = {h.chunk_id: h for h in fused}
    # 'a' appears in both lists → RRF score larger, relevance is the per-channel max.
    assert by_id["a"].relevance == 0.9
    assert by_id["a"].metadata.get("has_formula") is True
    assert by_id["a"].score > by_id["b"].score  # fused on top


# ── Feature boost lifts formula/numeric chunks for quantitative queries ──────

def test_feature_boost_promotes_formula_chunk(monkeypatch):
    from graph_rag.config import settings
    monkeypatch.setattr(settings, "enable_feature_boost", True)
    monkeypatch.setattr(settings, "feature_boost_weight", 1.0)
    plain = _hit("p", score=0.10, meta={"has_formula": False, "numeric_density": 0.0})
    formula = _hit("f", score=0.09, meta={"has_formula": True, "numeric_density": 0.5})
    out = HybridRetriever._apply_feature_boost("what is the value of sigma^0 at 5 GHz", [plain, formula])
    assert out[0].chunk_id == "f"  # formula chunk boosted above the plain one


def test_feature_boost_noop_for_non_numeric_query(monkeypatch):
    from graph_rag.config import settings
    monkeypatch.setattr(settings, "enable_feature_boost", True)
    a = _hit("a", score=0.2, meta={"has_formula": True})
    b = _hit("b", score=0.1, meta={"has_formula": False})
    out = HybridRetriever._apply_feature_boost("describe the mission", [a, b])
    assert [h.chunk_id for h in out] == ["a", "b"]  # order unchanged


# ── Exact-formula fast path is merged at the front, de-duplicated ────────────

def test_merge_exact_first_dedupes():
    exact = [_hit("x", relevance=1.0)]
    fused = [_hit("x", relevance=0.3), _hit("y", relevance=0.4)]
    merged = HybridRetriever._merge_exact_first(exact, fused)
    assert merged[0].chunk_id == "x" and merged[0].relevance == 1.0
    assert [h.chunk_id for h in merged] == ["x", "y"]  # x not duplicated


def test_bm25_exact_match_finds_verbatim_formula():
    bm = BM25Retriever(store=None)
    # Bypass the DB-backed index build by pre-populating the in-memory corpus.
    bm._bm25 = object()
    bm._docs = ["intro text", r"backscatter $$\sigma^0 = -10 dB$$ over ocean", "other"]
    bm._ids = ["c0", "c1", "c2"]
    bm._sources = ["a", "b", "c"]
    bm._metas = [{}, {}, {}]
    hits = bm.exact_match([r"\sigma^0"], limit=5)
    assert len(hits) == 1 and hits[0].chunk_id == "c1" and hits[0].relevance == 1.0


# ── Bi-encoder reranker writes a normalized relevance and reorders ──────────

class _FakeEmbedder:
    """Deterministic embeddings: vector aligns with the query when text matches."""
    def embed_query(self, q):
        return [1.0, 0.0]
    def embed_documents(self, texts):
        # 'match' → parallel to query (cos 1); else orthogonal (cos 0).
        return [[1.0, 0.0] if "match" in t else [0.0, 1.0] for t in texts]


def test_bi_encoder_reranker_orders_and_sets_relevance():
    hits = [_hit("a", text="noise"), _hit("b", text="a match here")]
    out = BiEncoderReranker(_FakeEmbedder()).rerank("q", hits, top_k=2)
    assert out[0].chunk_id == "b"
    assert out[0].relevance == pytest.approx(1.0, abs=1e-6)
    assert out[1].relevance == pytest.approx(0.0, abs=1e-6)
