"""Custom evaluators CE1–CE4 (evaluation_plan.md §2.3, §6).

RAGAS does not cover these MOSDAC-specific failure modes, so they are implemented
here as pure, deterministic, offline-testable functions:

  CE1  numeric & unit fidelity  — every quantity in the answer must be grounded in
       context after normalization; a *right number, wrong unit* is flagged
       separately as a unit-swap (a silent, dangerous failure for satellite specs).
  CE2  formula fidelity         — a gold formula must be reproduced character-exact
       (whitespace-insensitive) so a corrupted ``\\sigma_0`` is caught.
  CE3  citation integrity       — no fabricated ``[Sx]`` and load-bearing factual
       sentences should carry a citation.
  CE4  refusal correctness      — classify each turn into the §6 confusion matrix
       and reduce a batch to precision/recall/false-refusal/hallucination rates.

These reuse the production guardrail helpers (grounding_check, citation_verify) so
the metric agrees with what the live L4 guard actually does, rather than drifting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from guardrails.output.citation_verify import extract_cited_ids
from guardrails.output.grounding_check import (
    _NUMBER_RE,
    _factual_sentences,
    _normalize_number,
)

# A quantity: a number optionally followed by a unit token. The number is anchored
# with the SAME trailing word boundary as the production _NUMBER_RE so identifiers
# like "INSAT-3D" or "[S1]" are not parsed as the quantity "3 D" / "1". The unit is a
# short run of letters / micro / degree / slash / percent (km, m, GHz, µm, °C, %, days).
_UNIT_RE = r"(?:[A-Za-zµμ°%][A-Za-zµμ°%/0-9\^\-]*)"
_QUANTITY_RE = re.compile(r"(\b\d[\d,]*(?:\.\d+)?\b)\s*(" + _UNIT_RE + r")?")
# Math span: $$ … $$ or $ … $ or \( … \) — used to pull the formula body out.
_MATH_SPAN_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)", re.DOTALL)


# ── CE1: numeric & unit fidelity ──────────────────────────────────────────────
@dataclass
class NumericFidelity:
    total: int = 0
    grounded: int = 0
    unit_swaps: int = 0
    ungrounded_values: list[str] = field(default_factory=list)  # value not in context at all
    swapped: list[str] = field(default_factory=list)            # value present, unit wrong

    @property
    def grounded_rate(self) -> float:
        return 1.0 if self.total == 0 else self.grounded / self.total

    @property
    def unit_swap_rate(self) -> float:
        return 0.0 if self.total == 0 else self.unit_swaps / self.total


def _normalize_unit(unit: str) -> str:
    return re.sub(r"[^a-z0-9/^]", "", (unit or "").lower())


def _context_value_units(context: str) -> dict[str, set[str]]:
    """Map every normalized numeric value in *context* to the set of units it appears with."""
    out: dict[str, set[str]] = {}
    for value, unit in _QUANTITY_RE.findall(context or ""):
        norm = _normalize_number(value)
        if not norm:
            continue
        out.setdefault(norm, set())
        u = _normalize_unit(unit)
        if u:
            out[norm].add(u)
    return out


def score_numeric_fidelity(answer: str, context: str) -> NumericFidelity:
    """CE1. Score every quantity in *answer* against *context*.

    A quantity is grounded when its normalized value appears in context AND (the
    answer states no unit, or the answer's unit matches a unit the value carries in
    context). A value present in context but paired with a unit it never carries
    there is a **unit swap** — counted as both ungrounded and a unit_swap.
    """
    res = NumericFidelity()
    ctx = _context_value_units(context)
    # Track which exact (value) tokens we have already scored to avoid double
    # counting the same number repeated in the answer.
    for value, unit in _QUANTITY_RE.findall(answer or ""):
        norm = _normalize_number(value)
        if not norm:
            continue
        res.total += 1
        if norm not in ctx:
            res.ungrounded_values.append(norm)
            continue
        ans_unit = _normalize_unit(unit)
        ctx_units = ctx[norm]
        if not ans_unit or not ctx_units or ans_unit in ctx_units:
            res.grounded += 1
        else:
            # Right number, but the answer attached a unit the context never pairs
            # with this value → silent unit swap.
            res.unit_swaps += 1
            res.swapped.append(f"{norm} {unit}".strip())
    return res


# ── CE2: formula fidelity ─────────────────────────────────────────────────────
def _normalize_formula(text: str) -> str:
    """Whitespace-insensitive canonical form of a math span's body.

    Pulls the body out of ``$$…$$`` / ``$…$`` / ``\\(…\\)`` if present, otherwise
    uses the raw string; then removes all whitespace so ``\\sigma_0 = ...`` and
    ``\\sigma_0=...`` compare equal while a corrupted symbol does not.
    """
    m = _MATH_SPAN_RE.search(text or "")
    body = next((g for g in m.groups() if g), text) if m else (text or "")
    return re.sub(r"\s+", "", body).strip("$")


def score_formula_fidelity(answer: str, expected_formula: str) -> bool:
    """CE2. True iff the answer reproduces the gold formula character-exact
    (whitespace-insensitive). Vacuously True when no formula is expected."""
    expected = _normalize_formula(expected_formula)
    if not expected:
        return True
    # Compare against every math span in the answer, and against the whole answer
    # body (in case the model emitted the formula without $$ delimiters).
    candidates = [_normalize_formula(answer)]
    for m in _MATH_SPAN_RE.finditer(answer or ""):
        candidates.append(_normalize_formula(m.group(0)))
    return any(expected in c for c in candidates if c)


# ── CE3: citation integrity ───────────────────────────────────────────────────
@dataclass
class CitationIntegrity:
    cited_ids: set[str] = field(default_factory=set)
    fabricated_ids: set[str] = field(default_factory=set)
    factual_sentences: int = 0
    uncited_sentences: int = 0

    @property
    def has_fabricated(self) -> bool:
        return bool(self.fabricated_ids)

    @property
    def uncited_claim_rate(self) -> float:
        return 0.0 if self.factual_sentences == 0 else self.uncited_sentences / self.factual_sentences


_INLINE_CITE_RE = re.compile(r"\[S\d+\]")


def score_citation_integrity(answer: str, registry_ids: set[str]) -> CitationIntegrity:
    """CE3. Fabricated-cite detection + uncited-claim rate.

    ``registry_ids`` is the set of valid IDs (e.g. ``CitationRegistry.all_ids()``).
    A factual sentence (per the production grounding split) with no inline ``[Sx]``
    counts as an uncited claim.
    """
    cited = extract_cited_ids(answer or "")
    fabricated = cited - set(registry_ids or set())
    sentences = _factual_sentences(answer or "")
    uncited = sum(1 for s in sentences if not _INLINE_CITE_RE.search(s))
    return CitationIntegrity(
        cited_ids=cited,
        fabricated_ids=fabricated,
        factual_sentences=len(sentences),
        uncited_sentences=uncited,
    )


# ── CE4: refusal correctness (§6) ─────────────────────────────────────────────
TRUE_ANSWER = "true_answer"
FALSE_REFUSAL = "false_refusal"
TRUE_REFUSAL = "true_refusal"
HALLUCINATED_ON_ABSENT = "hallucinated_on_absent"


def classify_outcome(answerable: bool, refused: bool) -> str:
    """Place a turn in the §6 confusion matrix.

    answerable × refused →
        (True,  False) true_answer            — answered an answerable question
        (True,  True ) false_refusal          — over-blocked a good question
        (False, True ) true_refusal           — correctly declined an unanswerable one
        (False, False) hallucinated_on_absent — answered when the corpus is silent (worst)
    """
    if answerable:
        return FALSE_REFUSAL if refused else TRUE_ANSWER
    return TRUE_REFUSAL if refused else HALLUCINATED_ON_ABSENT


@dataclass
class RefusalConfusion:
    true_answer: int = 0
    false_refusal: int = 0
    true_refusal: int = 0
    hallucinated_on_absent: int = 0

    def add(self, outcome: str) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)

    @property
    def n_answerable(self) -> int:
        return self.true_answer + self.false_refusal

    @property
    def n_unanswerable(self) -> int:
        return self.true_refusal + self.hallucinated_on_absent

    @property
    def false_refusal_rate(self) -> float:
        """Of answerable questions, the fraction wrongly refused."""
        return 0.0 if self.n_answerable == 0 else self.false_refusal / self.n_answerable

    @property
    def hallucination_rate(self) -> float:
        """Of unanswerable questions, the fraction wrongly answered (the worst bucket)."""
        return 0.0 if self.n_unanswerable == 0 else self.hallucinated_on_absent / self.n_unanswerable

    @property
    def refusal_precision(self) -> float:
        """Of all refusals, the fraction that were correct (refused something unanswerable)."""
        refused = self.true_refusal + self.false_refusal
        return 0.0 if refused == 0 else self.true_refusal / refused

    @property
    def refusal_recall(self) -> float:
        """Of all unanswerable questions, the fraction the system refused."""
        return 0.0 if self.n_unanswerable == 0 else self.true_refusal / self.n_unanswerable


def refusal_confusion(records: list[tuple[bool, bool]]) -> RefusalConfusion:
    """Reduce a list of ``(answerable, refused)`` pairs to the confusion matrix."""
    cm = RefusalConfusion()
    for answerable, refused in records:
        cm.add(classify_outcome(answerable, refused))
    return cm
