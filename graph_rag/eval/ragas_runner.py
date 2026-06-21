"""RAGAS production-gate runner (evaluation_plan.md §4–§9).

Orchestrates the full gate:
  1. capture each golden item through the real pipeline (probe.py) — capture, not re-retrieve;
  2. segregate answered vs refused (a refusal is not a low-quality answer, §5);
  3. score answered-answerable items with the RAGAS metric suite (§2) using a
     STRONG, non-generator judge (§4);
  4. add the custom CE1–CE4 evaluators RAGAS can't do (§2.3, §6);
  5. aggregate per-stratum with bootstrap CIs (§8) and apply the go/no-go gate (§9);
  6. emit a reproducibility manifest, a markdown scorecard, and per-item JSONL.

The live pieces (judge LLM, ragas ``evaluate``) are imported lazily and isolated
behind small functions; the aggregation/manifest/reporting logic is pure and is
unit-tested offline with synthetic RAGAS scores.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from graph_rag.eval import custom_metrics as ce
from graph_rag.eval.dataset import GoldenItem
from graph_rag.eval.probe import CapturedTurn
from graph_rag.eval.scorecard import (
    ANSWER_RELEVANCY,
    CE1_GROUNDED_RATE,
    CE1_UNIT_SWAP_RATE,
    CE2_FORMULA_PASS_RATE,
    CE3_FABRICATED_CITE_RATE,
    CE3_UNCITED_CLAIM_RATE,
    CONTEXT_PRECISION,
    CONTEXT_RECALL,
    FACTUAL_CORRECTNESS,
    FAITHFULNESS,
    FALSE_REFUSAL_RATE,
    HALLUCINATION_RATE,
    JUDGE_KAPPA,
    SECURITY_PASS_RATE,
    GateThresholds,
    build_scorecard,
)
from graph_rag.eval.stats import CI, bootstrap_ci, mean

logger = logging.getLogger(__name__)

# Canonical summary key → the RAGAS metric class used to compute it. Column names in
# the RAGAS output are read from each metric's ``.name`` so we never hard-code them.
_RAGAS_METRIC_KEYS = {
    FAITHFULNESS: "Faithfulness",
    ANSWER_RELEVANCY: "ResponseRelevancy",
    CONTEXT_PRECISION: "LLMContextPrecisionWithReference",
    CONTEXT_RECALL: "LLMContextRecall",
    FACTUAL_CORRECTNESS: "FactualCorrectness",
    "context_entity_recall": "ContextEntityRecall",
    "semantic_similarity": "SemanticSimilarity",
    "noise_sensitivity": "NoiseSensitivity",
}
# Smaller, cheaper set for the --smoke path (§11).
_SMOKE_KEYS = {FAITHFULNESS, ANSWER_RELEVANCY, CONTEXT_RECALL, FACTUAL_CORRECTNESS}


# ── judge wiring (§4) ─────────────────────────────────────────────────────────
def build_judge(*, require: bool = True):
    """Build (judge_llm, judge_embeddings) wrapped for RAGAS from env config.

    The judge must NOT be the local generator under test (§4.1). Configure via env:
        RAGAS_JUDGE_BASE_URL   OpenAI-compatible chat endpoint (or omit for OpenAI)
        RAGAS_JUDGE_API_KEY    key for that endpoint
        RAGAS_JUDGE_MODEL      a strong model id (e.g. a frontier model)
        RAGAS_JUDGE_EMBED_MODEL / RAGAS_JUDGE_EMBED_BASE_URL / RAGAS_JUDGE_EMBED_API_KEY
    Determinism: temperature is forced to 0 (§4.2).
    """
    model = os.getenv("RAGAS_JUDGE_MODEL", "").strip()
    if not model:
        if require:
            raise RuntimeError(
                "No judge configured. Set RAGAS_JUDGE_MODEL (and RAGAS_JUDGE_API_KEY / "
                "RAGAS_JUDGE_BASE_URL) to a STRONG model — never the local generator (§4)."
            )
        return None, None

    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    base_url = os.getenv("RAGAS_JUDGE_BASE_URL", "").strip() or None
    api_key = os.getenv("RAGAS_JUDGE_API_KEY", "").strip() or "not-needed"
    llm = ChatOpenAI(model=model, temperature=0, base_url=base_url, api_key=api_key, seed=1234)

    embed_model = os.getenv("RAGAS_JUDGE_EMBED_MODEL", "text-embedding-3-small").strip()
    embed_base = os.getenv("RAGAS_JUDGE_EMBED_BASE_URL", "").strip() or base_url
    embed_key = os.getenv("RAGAS_JUDGE_EMBED_API_KEY", "").strip() or api_key
    emb = OpenAIEmbeddings(model=embed_model, base_url=embed_base, api_key=embed_key)

    return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(emb)


def _build_metrics(keys: set[str]):
    """Instantiate the RAGAS metric objects for *keys*; return (metrics, name_map)."""
    import ragas.metrics as rm

    metrics = []
    name_map: dict[str, str] = {}  # summary key → ragas column name
    for key, cls_name in _RAGAS_METRIC_KEYS.items():
        if key not in keys:
            continue
        metric = getattr(rm, cls_name)()
        metrics.append(metric)
        name_map[key] = metric.name
    return metrics, name_map


# ── RAW-config override (§1) ──────────────────────────────────────────────────
@contextlib.contextmanager
def guard_config_override(**overrides):
    """Temporarily set attributes on the shared guardrail settings, then restore.

    Used to run the RAW config (guards flag-only) in the same process as PROD.
    """
    from guardrails.config import guardrail_settings as gcfg

    saved = {k: getattr(gcfg, k) for k in overrides}
    try:
        for k, v in overrides.items():
            setattr(gcfg, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(gcfg, k, v)


RAW_OVERRIDES = dict(grounding_action="flag", citation_verify=False)


# ── RAGAS dataset + run (live) ────────────────────────────────────────────────
def build_ragas_dataset(captured: list[CapturedTurn], item_map: dict[str, GoldenItem]):
    """Build a RAGAS EvaluationDataset from answered-answerable captured turns.

    Returns (dataset, ordered_ids) so per-row scores can be mapped back to item ids.
    """
    from ragas import EvaluationDataset
    from ragas.dataset_schema import SingleTurnSample

    samples, ids = [], []
    for c in captured:
        item = item_map[c.id]
        samples.append(
            SingleTurnSample(
                user_input=c.user_input,
                retrieved_contexts=c.retrieved_contexts or [""],
                response=c.answer,
                reference=item.reference,
                reference_contexts=item.reference_contexts or None,
            )
        )
        ids.append(c.id)
    return EvaluationDataset(samples=samples), ids


def run_ragas_scores(
    captured_answered: list[CapturedTurn],
    item_map: dict[str, GoldenItem],
    *,
    judge_llm,
    judge_emb,
    smoke: bool = False,
) -> dict[str, dict[str, float]]:
    """Run the RAGAS suite; return {item_id: {summary_key: score}}. Live (needs judge)."""
    if not captured_answered:
        return {}
    from ragas import evaluate

    keys = _SMOKE_KEYS if smoke else set(_RAGAS_METRIC_KEYS)
    metrics, name_map = _build_metrics(keys)
    dataset, ids = build_ragas_dataset(captured_answered, item_map)
    result = evaluate(dataset=dataset, metrics=metrics, llm=judge_llm, embeddings=judge_emb)
    df = result.to_pandas()

    per_item: dict[str, dict[str, float]] = {}
    for row_idx, item_id in enumerate(ids):
        row = df.iloc[row_idx]
        scores: dict[str, float] = {}
        for summary_key, col in name_map.items():
            if col in row and row[col] is not None:
                try:
                    scores[summary_key] = float(row[col])
                except (TypeError, ValueError):
                    pass
        per_item[item_id] = scores
    return per_item


# ── aggregation (pure, testable) ──────────────────────────────────────────────
@dataclass
class ItemRecord:
    id: str
    stratum: str
    answerable: bool
    refused: bool
    outcome: str
    n_contexts: int
    ragas: dict[str, float] = field(default_factory=dict)
    ce1_grounded_rate: float | None = None
    ce1_unit_swap_rate: float | None = None
    ce2_formula_pass: bool | None = None
    ce3_has_fabricated: bool | None = None
    ce3_uncited_claim_rate: float | None = None
    error: str = ""


@dataclass
class ResultBundle:
    config_name: str
    summary: dict[str, float | None]
    stratum_faithfulness: dict[str, float]
    confusion: ce.RefusalConfusion
    records: list[ItemRecord]
    cis: dict[str, CI]
    n_total: int
    n_answered: int
    n_refused: int

    def go_scorecard(self, thresholds: GateThresholds | None = None):
        return build_scorecard(
            self.summary, thresholds=thresholds, stratum_faithfulness=self.stratum_faithfulness
        )


def aggregate_results(
    captured: list[CapturedTurn],
    item_map: dict[str, GoldenItem],
    per_item_ragas: dict[str, dict[str, float]],
    *,
    config_name: str = "PROD",
    judge_kappa: float | None = None,
) -> ResultBundle:
    """Combine captured turns + RAGAS scores + custom metrics into the gate summary.

    Pure: ``per_item_ragas`` is injected, so this is exercised offline with synthetic
    scores and no live judge.
    """
    records: list[ItemRecord] = []
    confusion = ce.refusal_confusion([(c.answerable, c.refused) for c in captured])

    faith_vals: list[float] = []
    faith_by_stratum: dict[str, list[float]] = {}
    ragas_vals: dict[str, list[float]] = {}
    ce1_grounded: list[float] = []
    ce1_swap: list[float] = []
    ce2_pass: list[float] = []
    ce3_fab: list[float] = []
    ce3_uncited: list[float] = []
    security_refused: list[float] = []

    for c in captured:
        item = item_map[c.id]
        outcome = ce.classify_outcome(c.answerable, c.refused)
        rec = ItemRecord(
            id=c.id, stratum=c.stratum, answerable=c.answerable, refused=c.refused,
            outcome=outcome, n_contexts=len(c.retrieved_contexts), error=c.error,
        )

        if c.stratum == "should_refuse_unsafe":
            security_refused.append(1.0 if c.refused else 0.0)

        # Only answered-answerable turns get RAGAS + content metrics (§5).
        scored = c.answerable and not c.refused and c.ok
        if scored:
            rec.ragas = per_item_ragas.get(c.id, {})
            for k, v in rec.ragas.items():
                ragas_vals.setdefault(k, []).append(v)
            if FAITHFULNESS in rec.ragas:
                faith_vals.append(rec.ragas[FAITHFULNESS])
                faith_by_stratum.setdefault(c.stratum, []).append(rec.ragas[FAITHFULNESS])

            context = "\n".join(c.retrieved_contexts)
            nf = ce.score_numeric_fidelity(c.answer, context)
            rec.ce1_grounded_rate, rec.ce1_unit_swap_rate = nf.grounded_rate, nf.unit_swap_rate
            ce1_grounded.append(nf.grounded_rate)
            ce1_swap.append(nf.unit_swap_rate)

            if c.stratum == "formula":
                passed = ce.score_formula_fidelity(c.answer, item.expected_formula)
                rec.ce2_formula_pass = passed
                ce2_pass.append(1.0 if passed else 0.0)

            registry_ids = {cit.get("id") for cit in c.citations if cit.get("id")}
            cint = ce.score_citation_integrity(c.answer, registry_ids)
            rec.ce3_has_fabricated = cint.has_fabricated
            rec.ce3_uncited_claim_rate = cint.uncited_claim_rate
            ce3_fab.append(1.0 if cint.has_fabricated else 0.0)
            ce3_uncited.append(cint.uncited_claim_rate)

        records.append(rec)

    def _mean_or_none(vals: list[float]) -> float | None:
        return mean(vals) if vals else None

    summary: dict[str, float | None] = {
        FAITHFULNESS: _mean_or_none(faith_vals),
        ANSWER_RELEVANCY: _mean_or_none(ragas_vals.get(ANSWER_RELEVANCY, [])),
        CONTEXT_PRECISION: _mean_or_none(ragas_vals.get(CONTEXT_PRECISION, [])),
        CONTEXT_RECALL: _mean_or_none(ragas_vals.get(CONTEXT_RECALL, [])),
        FACTUAL_CORRECTNESS: _mean_or_none(ragas_vals.get(FACTUAL_CORRECTNESS, [])),
        HALLUCINATION_RATE: confusion.hallucination_rate if confusion.n_unanswerable else None,
        FALSE_REFUSAL_RATE: confusion.false_refusal_rate if confusion.n_answerable else None,
        CE1_GROUNDED_RATE: _mean_or_none(ce1_grounded),
        CE1_UNIT_SWAP_RATE: _mean_or_none(ce1_swap),
        CE2_FORMULA_PASS_RATE: _mean_or_none(ce2_pass),
        CE3_FABRICATED_CITE_RATE: _mean_or_none(ce3_fab),
        CE3_UNCITED_CLAIM_RATE: _mean_or_none(ce3_uncited),
        SECURITY_PASS_RATE: _mean_or_none(security_refused),
        JUDGE_KAPPA: judge_kappa,
    }
    # Report extra (non-gated) RAGAS metrics too.
    for extra in ("context_entity_recall", "semantic_similarity", "noise_sensitivity"):
        summary[extra] = _mean_or_none(ragas_vals.get(extra, []))

    stratum_faith = {s: mean(v) for s, v in faith_by_stratum.items() if v}
    cis = {k: bootstrap_ci(v) for k, v in ragas_vals.items() if v}
    if faith_vals:
        cis[FAITHFULNESS] = bootstrap_ci(faith_vals)

    n_answered = sum(1 for c in captured if not c.refused and c.ok)
    return ResultBundle(
        config_name=config_name,
        summary=summary,
        stratum_faithfulness=stratum_faith,
        confusion=confusion,
        records=records,
        cis=cis,
        n_total=len(captured),
        n_answered=n_answered,
        n_refused=sum(1 for c in captured if c.refused),
    )


# ── manifest (§1, §11) ────────────────────────────────────────────────────────
def build_manifest(config_name: str, gold_checksum: str, gold_n: int, judge_model: str) -> dict:
    """Frozen knobs + corpus SHA + gold version + judge version (§1)."""
    from graph_rag.config import settings as gs
    from guardrails.config import guardrail_settings as gcfg

    corpus_sha = ""
    mpath = Path(gs.ingest_manifest_path)
    if mpath.exists():
        corpus_sha = hashlib.sha256(mpath.read_bytes()).hexdigest()[:16]

    return {
        "config": config_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gold_checksum": gold_checksum,
        "gold_n": gold_n,
        "corpus_manifest_sha": corpus_sha,
        "judge_model": judge_model or "(unset)",
        "pipeline": {
            "embedding_model": gs.ollama_embedding_model,
            "embed_query_instruction": bool(gs.embed_query_instruction),
            "enable_passage_rerank": gs.enable_passage_rerank,
            "enable_cross_encoder_rerank": gs.enable_cross_encoder_rerank,
            "top_k_passages": gs.top_k_passages,
            "hybrid_rrf_k": gs.hybrid_rrf_k,
            "enable_parent_expansion": gs.enable_parent_expansion,
            "enable_section_subsplit": gs.enable_section_subsplit,
            "chunk_max_section_chars": gs.chunk_max_section_chars,
            "enable_feature_boost": gs.enable_feature_boost,
            "enable_query_decomposition": gs.enable_query_decomposition,
            "enable_iterative_reasoning": gs.enable_iterative_reasoning,
            "tabby_model": gs.tabby_model,
            "llm_temperature": gs.llm_temperature,
        },
        "guardrails": {
            "retrieval_min_score": gcfg.retrieval_min_score,
            "min_supporting_passages": gcfg.min_supporting_passages,
            "grounding_action": gcfg.grounding_action,
            "grounding_min_sim": gcfg.grounding_min_sim,
            "scope_min_sim": gcfg.scope_min_sim,
            "citation_verify": gcfg.citation_verify,
        },
    }


# ── reporting ─────────────────────────────────────────────────────────────────
def render_markdown(bundle: ResultBundle, manifest: dict, thresholds: GateThresholds | None = None) -> str:
    card = bundle.go_scorecard(thresholds)
    cm = bundle.confusion
    lines = [
        f"# RAGAS Eval — {bundle.config_name}",
        "",
        f"_Generated {manifest['timestamp']} · gold={manifest['gold_checksum'][:12]} "
        f"(n={manifest['gold_n']}) · corpus={manifest['corpus_manifest_sha'] or 'n/a'} · "
        f"judge={manifest['judge_model']}_",
        "",
        card.render(),
        "",
        "## Counts",
        f"- items: **{bundle.n_total}** · answered: **{bundle.n_answered}** · refused: **{bundle.n_refused}**",
        "",
        "## Refusal confusion (§6)",
        "| | answered | refused |",
        "|---|---|---|",
        f"| **answerable** | {cm.true_answer} (true answer) | {cm.false_refusal} (false refusal) |",
        f"| **unanswerable** | {cm.hallucinated_on_absent} (hallucinated) | {cm.true_refusal} (true refusal) |",
        "",
        f"- false-refusal rate: **{cm.false_refusal_rate:.3f}** · hallucination-on-absent: "
        f"**{cm.hallucination_rate:.3f}** · refusal precision/recall: "
        f"{cm.refusal_precision:.3f}/{cm.refusal_recall:.3f}",
        "",
        "## Metric means (95% bootstrap CI where available)",
        "| metric | value | CI |",
        "|--------|-------|----|",
    ]
    for key in sorted(bundle.summary):
        val = bundle.summary[key]
        vs = "—" if val is None else f"{val:.3f}"
        ci = bundle.cis.get(key)
        cis = f"[{ci.lo:.3f}, {ci.hi:.3f}]" if ci else ""
        lines.append(f"| {key} | {vs} | {cis} |")
    return "\n".join(lines)


def write_outputs(bundle: ResultBundle, manifest: dict, out_dir: str | Path, thresholds=None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = manifest["timestamp"].replace(":", "").replace("-", "")[:15]
    base = f"ragas_{bundle.config_name.lower()}_{stamp}"

    md_path = out / f"{base}.md"
    md_path.write_text(render_markdown(bundle, manifest, thresholds), encoding="utf-8")

    jsonl_path = out / f"{base}_items.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for rec in bundle.records:
            fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    manifest_path = out / f"{base}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {"markdown": str(md_path), "jsonl": str(jsonl_path), "manifest": str(manifest_path)}


# ── top-level orchestration (live) ────────────────────────────────────────────
def run_gate(
    items: list[GoldenItem],
    *,
    config_name: str = "PROD",
    smoke: bool = False,
    out_dir: str = "eval_runs",
    judge_kappa: float | None = None,
) -> ResultBundle:
    """End-to-end: capture → RAGAS → custom metrics → aggregate → write outputs.

    Live: needs Chroma/Neo4j/Tabby (pipeline) and a configured judge (§4).
    """
    from graph_rag.eval.dataset import golden_checksum
    from graph_rag.eval.probe import build_probe_service, capture_all

    item_map = {it.id: it for it in items}
    judge_llm, judge_emb = build_judge(require=True)
    judge_model = os.getenv("RAGAS_JUDGE_MODEL", "")

    overrides = RAW_OVERRIDES if config_name == "RAW" else {}
    with guard_config_override(**overrides):
        service, recorder = build_probe_service()
        captured = capture_all(service, recorder, items)
        answered = [c for c in captured if c.answerable and not c.refused and c.ok]
        per_item = run_ragas_scores(
            answered, item_map, judge_llm=judge_llm, judge_emb=judge_emb, smoke=smoke
        )

    bundle = aggregate_results(
        captured, item_map, per_item, config_name=config_name, judge_kappa=judge_kappa
    )
    manifest = build_manifest(config_name, golden_checksum(items), len(items), judge_model)
    paths = write_outputs(bundle, manifest, out_dir)
    logger.info("Wrote eval outputs: %s", paths)
    return bundle
