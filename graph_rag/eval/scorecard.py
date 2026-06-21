"""Production go/no-go scorecard (evaluation_plan.md §9).

Encodes the hard gates as declarative thresholds over a flat summary dict so the
gate logic is pure and unit-tested without any live run. The rule (§9): **every**
hard gate must be green for a GO; a high average never papers over a red stratum,
and a gate that could not be evaluated (missing metric) blocks the GO rather than
silently passing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── canonical metric keys expected in the summary dict ────────────────────────
FAITHFULNESS = "faithfulness"
CONTEXT_RECALL = "context_recall"
CONTEXT_PRECISION = "context_precision"
FACTUAL_CORRECTNESS = "factual_correctness"
ANSWER_RELEVANCY = "answer_relevancy"
HALLUCINATION_RATE = "hallucination_rate"
FALSE_REFUSAL_RATE = "false_refusal_rate"
CE1_GROUNDED_RATE = "ce1_grounded_rate"
CE1_UNIT_SWAP_RATE = "ce1_unit_swap_rate"
CE2_FORMULA_PASS_RATE = "ce2_formula_pass_rate"
CE3_FABRICATED_CITE_RATE = "ce3_fabricated_cite_rate"
CE3_UNCITED_CLAIM_RATE = "ce3_uncited_claim_rate"
SECURITY_PASS_RATE = "security_pass_rate"
JUDGE_KAPPA = "judge_kappa"

MIN = "min"  # higher is better; value must be >= threshold
MAX = "max"  # lower is better;  value must be <= threshold


@dataclass(frozen=True)
class GateThresholds:
    """The §9 initial thresholds. Tune after the first calibrated baseline."""

    faithfulness_min: float = 0.90
    faithfulness_stratum_min: float = 0.85
    hallucination_rate_max: float = 0.02
    ce1_grounded_min: float = 0.95
    ce1_unit_swap_max: float = 0.01
    ce2_formula_pass_min: float = 0.90
    ce3_fabricated_cite_max: float = 0.0
    ce3_uncited_claim_max: float = 0.10
    context_recall_min: float = 0.85
    context_precision_min: float = 0.70
    factual_correctness_min: float = 0.75
    answer_relevancy_min: float = 0.80
    false_refusal_max: float = 0.08
    security_pass_min: float = 1.0
    judge_kappa_min: float = 0.6


@dataclass
class GateDef:
    name: str
    key: str
    direction: str  # MIN or MAX
    threshold: float
    rationale: str


def _gates(t: GateThresholds) -> list[GateDef]:
    return [
        GateDef("Faithfulness", FAITHFULNESS, MIN, t.faithfulness_min,
                "Grounding is the core promise — hardest gate."),
        GateDef("Hallucination on absent", HALLUCINATION_RATE, MAX, t.hallucination_rate_max,
                "Answering when the corpus is silent is unacceptable."),
        GateDef("Numeric fidelity (CE1)", CE1_GROUNDED_RATE, MIN, t.ce1_grounded_min,
                "Wrong satellite spec = wrong science."),
        GateDef("Unit-swap rate (CE1)", CE1_UNIT_SWAP_RATE, MAX, t.ce1_unit_swap_max,
                "Right number / wrong unit is a silent, dangerous failure."),
        GateDef("Formula fidelity (CE2)", CE2_FORMULA_PASS_RATE, MIN, t.ce2_formula_pass_min,
                "Explicit product goal (F)."),
        GateDef("Fabricated citations (CE3)", CE3_FABRICATED_CITE_RATE, MAX, t.ce3_fabricated_cite_max,
                "A fabricated [Sx] destroys trust."),
        GateDef("Uncited claims (CE3)", CE3_UNCITED_CLAIM_RATE, MAX, t.ce3_uncited_claim_max,
                "Load-bearing claims must cite a source."),
        GateDef("Context recall", CONTEXT_RECALL, MIN, t.context_recall_min,
                "Can't ground what wasn't retrieved."),
        GateDef("Context precision", CONTEXT_PRECISION, MIN, t.context_precision_min,
                "Noise in context drives hallucination."),
        GateDef("Answer correctness", FACTUAL_CORRECTNESS, MIN, t.factual_correctness_min,
                "End-to-end usefulness."),
        GateDef("Answer relevancy", ANSWER_RELEVANCY, MIN, t.answer_relevancy_min,
                "Don't wander / dodge."),
        GateDef("False refusal", FALSE_REFUSAL_RATE, MAX, t.false_refusal_max,
                "Over-blocking kills usability."),
        GateDef("Security suite", SECURITY_PASS_RATE, MIN, t.security_pass_min,
                "Injection / scope / PII — non-negotiable."),
        GateDef("Judge trust (kappa)", JUDGE_KAPPA, MIN, t.judge_kappa_min,
                "If the instrument is untrusted, the gate is void."),
    ]


PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class GateResult:
    name: str
    key: str
    value: float | None
    threshold: float
    direction: str
    status: str
    note: str = ""

    @property
    def passed(self) -> bool:
        return self.status == PASS


@dataclass
class ScoreCard:
    results: list[GateResult] = field(default_factory=list)
    stratum_faithfulness: dict[str, float] = field(default_factory=dict)
    stratum_floor: float = 0.85
    stratum_violations: list[str] = field(default_factory=list)

    @property
    def go(self) -> bool:
        """GO only when every gate PASSed and no stratum breached its faithfulness floor."""
        return (
            bool(self.results)
            and all(r.passed for r in self.results)
            and not self.stratum_violations
        )

    def render(self) -> str:
        head = "## Production Gate — " + ("✅ GO" if self.go else "❌ NO-GO")
        lines = [
            head,
            "",
            "| Gate | Value | Threshold | Result |",
            "|------|-------|-----------|--------|",
        ]
        icon = {PASS: "✅", FAIL: "❌", SKIP: "⚠️"}
        for r in self.results:
            val = "—" if r.value is None else f"{r.value:.3f}"
            cmp = "≥" if r.direction == MIN else "≤"
            note = f" — {r.note}" if r.note else ""
            lines.append(
                f"| {r.name} | {val} | {cmp} {r.threshold:.3f} | {icon.get(r.status, '?')} {r.status}{note} |"
            )
        if self.stratum_faithfulness:
            lines += ["", f"**Per-stratum faithfulness floor (≥ {self.stratum_floor:.2f}):**"]
            for stratum, v in sorted(self.stratum_faithfulness.items()):
                ok = "✅" if v >= self.stratum_floor else "❌"
                lines.append(f"- {ok} `{stratum}`: {v:.3f}")
        if self.stratum_violations:
            lines += ["", "**Stratum violations (force NO-GO):** " + ", ".join(self.stratum_violations)]
        return "\n".join(lines)


def _check(value: float | None, direction: str, threshold: float) -> str:
    if value is None:
        return SKIP
    if direction == MIN:
        return PASS if value >= threshold else FAIL
    return PASS if value <= threshold else FAIL


def build_scorecard(
    summary: dict[str, float | None],
    *,
    thresholds: GateThresholds | None = None,
    stratum_faithfulness: dict[str, float] | None = None,
) -> ScoreCard:
    """Evaluate every §9 gate against *summary* and return a ScoreCard.

    Args:
        summary: flat mapping of metric key → value (None ⇒ not evaluated ⇒ SKIP,
                 which blocks the GO).
        thresholds: override the default §9 thresholds.
        stratum_faithfulness: per-stratum faithfulness means; any below the floor
                 forces NO-GO regardless of the overall mean.
    """
    t = thresholds or GateThresholds()
    results: list[GateResult] = []
    for g in _gates(t):
        value = summary.get(g.key)
        status = _check(value, g.direction, g.threshold)
        note = "metric not evaluated" if status == SKIP else g.rationale if status == FAIL else ""
        results.append(GateResult(g.name, g.key, value, g.threshold, g.direction, status, note))

    strat = stratum_faithfulness or {}
    violations = [s for s, v in strat.items() if v < t.faithfulness_stratum_min]
    return ScoreCard(
        results=results,
        stratum_faithfulness=strat,
        stratum_floor=t.faithfulness_stratum_min,
        stratum_violations=violations,
    )
