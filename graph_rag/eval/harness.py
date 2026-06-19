"""Evaluation harness (Phase 0): measure retrieval, correctness, faithfulness.

Runs a curated set of single-fact / multi-hop / comparison questions through the
chatbot and scores three things so any later change can be compared to a baseline:

  * retrieval hit-rate — did retrieval surface the expected entities/keywords?
  * answer correctness — LLM-as-judge against a short reference (optional).
  * faithfulness        — is every number in the answer present in the context?

Metric helpers (`retrieval_hit_rate`, `faithfulness_score`, `extract_numbers`)
are pure functions and unit-tested without any live service. The full run needs
Neo4j + Tabby and is invoked via `python main.py eval`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
DEFAULT_QUESTION_SET = "tests/eval/multihop_questions.yaml"


# ── data ────────────────────────────────────────────────────────────────────
@dataclass
class EvalQuestion:
    id: str
    type: str  # single | multihop | comparison | followup
    question: str
    expected_entities: list[str] = field(default_factory=list)
    expected_keywords: list[str] = field(default_factory=list)
    reference: str = ""
    # Prior user turns to replay before `question` so a follow-up (e.g. "what's
    # its resolution?") can resolve its references. Empty for single-turn cases.
    setup: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    id: str
    type: str
    question: str
    answer: str
    retrieval_hit: float
    faithful: float
    correctness: float | None = None


# ── pure metric helpers ─────────────────────────────────────────────────────
def extract_numbers(text: str) -> set[str]:
    """Numeric tokens in text (used for faithfulness checking)."""
    return set(_NUMBER_RE.findall(text or ""))


def faithfulness_score(answer: str, context: str) -> float:
    """Fraction of the answer's numbers that appear in the context (1.0 if none)."""
    nums = extract_numbers(answer)
    if not nums:
        return 1.0
    ctx_nums = extract_numbers(context)
    grounded = sum(1 for n in nums if n in ctx_nums)
    return grounded / len(nums)


def retrieval_hit_rate(context: str, expected_entities: list[str], expected_keywords: list[str]) -> float:
    """Fraction of expected entities/keywords present (case-insensitive) in context."""
    targets = [t for t in (list(expected_entities) + list(expected_keywords)) if t]
    if not targets:
        return 1.0
    low = (context or "").lower()
    hits = sum(1 for t in targets if t.lower() in low)
    return hits / len(targets)


# ── harness ─────────────────────────────────────────────────────────────────
class EvalHarness:
    def __init__(self, retriever=None, bot=None, judge_llm=None, use_judge: bool = True):
        self._retriever = retriever
        self._bot = bot
        self._judge = judge_llm
        self._use_judge = use_judge

    @staticmethod
    def load(path: str | Path = DEFAULT_QUESTION_SET) -> list[EvalQuestion]:
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
        out: list[EvalQuestion] = []
        for row in raw:
            out.append(
                EvalQuestion(
                    id=str(row.get("id", "")),
                    type=str(row.get("type", "single")),
                    question=str(row.get("question", "")),
                    expected_entities=list(row.get("expected_entities", []) or []),
                    expected_keywords=list(row.get("expected_keywords", []) or []),
                    reference=str(row.get("reference", "")),
                    setup=[str(s) for s in (row.get("setup", []) or [])],
                )
            )
        return out

    def _get_retriever(self):
        if self._retriever is None:
            from graph_rag.retrieval.hybrid_retriever import HybridRetriever

            self._retriever = HybridRetriever()
        return self._retriever

    def _get_bot(self):
        if self._bot is None:
            from graph_rag.chat.chatbot import GraphRagChatbot

            self._bot = GraphRagChatbot(retriever=self._get_retriever())
        return self._bot

    def _get_judge(self):
        if self._judge is None and self._use_judge:
            from graph_rag.llm.tabby_client import get_llm

            self._judge = get_llm()
        return self._judge

    def judge_correctness(self, question: str, answer: str, reference: str) -> float | None:
        judge = self._get_judge()
        if judge is None or not reference:
            return None
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            sys = (
                "You are a strict grader. Rate from 0.0 to 1.0 how well the ANSWER matches the "
                "REFERENCE for the QUESTION (1.0 = fully correct, 0.0 = wrong/irrelevant). "
                "Reply with ONLY the number."
            )
            resp = judge.invoke(
                [
                    SystemMessage(content=sys),
                    HumanMessage(
                        content=f"QUESTION: {question}\nREFERENCE: {reference}\nANSWER: {answer}\nScore:"
                    ),
                ]
            )
            raw = getattr(resp, "content", str(resp))
            m = _NUMBER_RE.search(raw)
            return max(0.0, min(1.0, float(m.group(0)))) if m else None
        except Exception as exc:
            logger.debug("Judge failed: %s", exc)
            return None

    def run(self, questions: list[EvalQuestion], limit: int | None = None) -> list[EvalResult]:
        retriever = self._get_retriever()
        bot = self._get_bot()
        from graph_rag.retrieval.query_contextualizer import QueryContextualizer

        contextualizer = QueryContextualizer()
        results: list[EvalResult] = []
        for q in questions[: limit or len(questions)]:
            bot.reset()
            # Replay any prior turns so a follow-up can resolve its references.
            for prior in q.setup:
                bot.chat(prior)
            history_text = bot._format_history() if q.setup else ""
            # Score retrieval on the SAME query the pipeline searches on — a
            # follow-up is first contextualized into a standalone query.
            search_query = contextualizer.contextualize(q.question, history_text).search_query
            try:
                ctx = retriever.retrieve(search_query)
                context = f"{ctx.get('graph_context','')}\n{ctx.get('vector_context','')}"
            except Exception as exc:
                logger.warning("Retrieval failed for %s: %s", q.id, exc)
                context = ""
            answer = bot.chat(q.question)
            results.append(
                EvalResult(
                    id=q.id,
                    type=q.type,
                    question=q.question,
                    answer=answer,
                    retrieval_hit=retrieval_hit_rate(context, q.expected_entities, q.expected_keywords),
                    faithful=faithfulness_score(answer, context),
                    correctness=self.judge_correctness(q.question, answer, q.reference),
                )
            )
        return results

    # ── reporting ───────────────────────────────────────────────────────────
    @staticmethod
    def _avg(values: list[float]) -> float:
        vals = [v for v in values if v is not None]
        return sum(vals) / len(vals) if vals else 0.0

    def scorecard(self, results: list[EvalResult]) -> str:
        if not results:
            return "(no results)"
        types = sorted({r.type for r in results})
        lines = [
            "Scorecard (averages)",
            f"{'type':<12}{'n':>4}{'retrieval':>12}{'faithful':>11}{'correct':>10}",
            "-" * 49,
        ]
        for t in types:
            rs = [r for r in results if r.type == t]
            lines.append(
                f"{t:<12}{len(rs):>4}{self._avg([r.retrieval_hit for r in rs]):>12.2f}"
                f"{self._avg([r.faithful for r in rs]):>11.2f}"
                f"{self._avg([r.correctness for r in rs]):>10.2f}"
            )
        lines.append("-" * 49)
        lines.append(
            f"{'OVERALL':<12}{len(results):>4}{self._avg([r.retrieval_hit for r in results]):>12.2f}"
            f"{self._avg([r.faithful for r in results]):>11.2f}"
            f"{self._avg([r.correctness for r in results]):>10.2f}"
        )
        return "\n".join(lines)

    def save_markdown(self, results: list[EvalResult], path: str | Path) -> None:
        rows = [
            "# Eval Results",
            "",
            self.scorecard(results),
            "",
            "| id | type | retrieval | faithful | correct | question |",
            "|----|------|-----------|----------|---------|----------|",
        ]
        for r in results:
            corr = f"{r.correctness:.2f}" if r.correctness is not None else "—"
            rows.append(
                f"| {r.id} | {r.type} | {r.retrieval_hit:.2f} | {r.faithful:.2f} | {corr} | {r.question} |"
            )
        Path(path).write_text("\n".join(rows) + "\n", encoding="utf-8")
