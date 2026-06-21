"""Golden evaluation dataset — schema, JSONL loader, validation, checksum.

Implements §3.3 of evaluation_plan.md. A golden item is the unit of evaluation:
a question, its curated reference answer, the contexts/entities/quantities/formula
that a correct answer must ground in, and whether it is *answerable* at all
(``answerable=false`` ⇒ the only correct behaviour is a refusal — see §6).

Records live in ``tests/eval/golden/<version>/*.jsonl`` (one JSON object per line).
The loader accepts either a single ``.jsonl`` file or a directory of them, so a
versioned gold set can be split across files (single.jsonl, refusals.jsonl, …).

Everything here is pure I/O + validation — no live services — so it is unit-tested
offline. ``golden_checksum`` produces the stable hash recorded in each run's
manifest so a score is always tied to the exact dataset that produced it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

# Strata defined in evaluation_plan.md §3.2. Items must be tagged with one of
# these so per-stratum confidence intervals and the per-stratum gate can be computed.
STRATA: tuple[str, ...] = (
    "single",
    "multihop",
    "comparison",
    "formula",
    "numeric_edge",
    "followup",
    "should_refuse_oos",
    "should_refuse_unsafe",
    "answerable_but_sparse",
)

# Strata whose correct behaviour is a refusal (answerable should be False).
REFUSAL_STRATA: frozenset[str] = frozenset({"should_refuse_oos", "should_refuse_unsafe"})

DEFAULT_GOLDEN_DIR = "tests/eval/golden/v1"


@dataclass
class Quantity:
    """A value+unit pair a correct answer must ground (CE1). Unit may be empty."""

    value: float
    unit: str = ""

    @staticmethod
    def from_obj(obj: dict | "Quantity") -> "Quantity":
        if isinstance(obj, Quantity):
            return obj
        return Quantity(value=float(obj["value"]), unit=str(obj.get("unit", "") or ""))


@dataclass
class GoldenItem:
    """One evaluation example. Mirrors the JSONL schema in evaluation_plan.md §3.3."""

    id: str
    stratum: str
    user_input: str
    reference: str = ""
    reference_contexts: list[str] = field(default_factory=list)
    expected_entities: list[str] = field(default_factory=list)
    expected_quantities: list[Quantity] = field(default_factory=list)
    expected_formula: str = ""
    answerable: bool = True
    setup: list[str] = field(default_factory=list)

    # ── validation ────────────────────────────────────────────────────────────
    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty list means valid."""
        problems: list[str] = []
        if not self.id:
            problems.append("missing id")
        if not self.user_input.strip():
            problems.append(f"{self.id}: empty user_input")
        if self.stratum not in STRATA:
            problems.append(f"{self.id}: unknown stratum {self.stratum!r} (expected one of {STRATA})")
        # A refusal-stratum item that is marked answerable is a contradiction that
        # would silently corrupt the refusal confusion matrix (§6).
        if self.stratum in REFUSAL_STRATA and self.answerable:
            problems.append(f"{self.id}: stratum {self.stratum!r} must have answerable=false")
        # An answerable item with no reference cannot be scored for correctness.
        if self.answerable and not self.reference.strip():
            problems.append(f"{self.id}: answerable item has no reference answer")
        if self.stratum == "formula" and not self.expected_formula.strip():
            problems.append(f"{self.id}: formula stratum item has no expected_formula")
        if self.stratum == "followup" and not self.setup:
            problems.append(f"{self.id}: followup item has no setup turns")
        return problems

    # ── serialization ───────────────────────────────────────────────────────────
    @staticmethod
    def from_dict(row: dict) -> "GoldenItem":
        return GoldenItem(
            id=str(row.get("id", "")),
            stratum=str(row.get("stratum", "")),
            user_input=str(row.get("user_input", "")),
            reference=str(row.get("reference", "") or ""),
            reference_contexts=[str(c) for c in (row.get("reference_contexts") or [])],
            expected_entities=[str(e) for e in (row.get("expected_entities") or [])],
            expected_quantities=[Quantity.from_obj(q) for q in (row.get("expected_quantities") or [])],
            expected_formula=str(row.get("expected_formula", "") or ""),
            answerable=bool(row.get("answerable", True)),
            setup=[str(s) for s in (row.get("setup") or [])],
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        # Drop empties so the on-disk JSONL stays readable.
        return {k: v for k, v in d.items() if v not in ([], "", None)} | {
            "id": self.id,
            "stratum": self.stratum,
            "user_input": self.user_input,
            "answerable": self.answerable,
        }


class GoldenDatasetError(ValueError):
    """Raised when a golden dataset is malformed or fails validation."""


def _iter_jsonl(path: Path) -> Iterable[tuple[int, str, dict]]:
    """Yield (line_no, file_name, parsed_row) for every non-blank line."""
    files: list[Path]
    if path.is_dir():
        files = sorted(p for p in path.glob("*.jsonl"))
        if not files:
            raise GoldenDatasetError(f"no .jsonl files found in directory {path}")
    elif path.is_file():
        files = [path]
    else:
        raise GoldenDatasetError(f"golden dataset path does not exist: {path}")

    for f in files:
        for i, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            try:
                yield i, f.name, json.loads(line)
            except json.JSONDecodeError as exc:
                raise GoldenDatasetError(f"{f.name}:{i}: invalid JSON: {exc}") from exc


def load_golden(path: str | Path = DEFAULT_GOLDEN_DIR, *, strict: bool = True) -> list[GoldenItem]:
    """Load and validate a golden dataset from a JSONL file or directory.

    Args:
        path:   a ``.jsonl`` file or a directory containing ``*.jsonl`` files.
        strict: when True (default) any validation problem or duplicate id raises
                ``GoldenDatasetError``; the gate must never run on a malformed set.
    """
    items: list[GoldenItem] = []
    problems: list[str] = []
    seen: dict[str, str] = {}

    for line_no, fname, row in _iter_jsonl(Path(path)):
        item = GoldenItem.from_dict(row)
        if item.id in seen:
            problems.append(f"{fname}:{line_no}: duplicate id {item.id!r} (first seen in {seen[item.id]})")
        else:
            seen[item.id] = fname
        problems.extend(item.validate())
        items.append(item)

    if not items:
        raise GoldenDatasetError(f"golden dataset is empty: {path}")
    if problems and strict:
        joined = "\n  - ".join(problems)
        raise GoldenDatasetError(f"golden dataset failed validation:\n  - {joined}")
    return items


def stratum_counts(items: list[GoldenItem]) -> dict[str, int]:
    counts: dict[str, int] = {s: 0 for s in STRATA}
    for it in items:
        counts[it.stratum] = counts.get(it.stratum, 0) + 1
    return counts


def golden_checksum(items: list[GoldenItem]) -> str:
    """Stable SHA-256 over the dataset content, for the run manifest.

    Order-independent (sorted by id) and serialization-independent (canonical
    JSON) so re-saving the same items in a different on-disk order/format yields
    the same checksum — only a real content change moves it.
    """
    canon = [
        json.dumps(GoldenItem.to_dict(it), sort_keys=True, ensure_ascii=True)
        for it in sorted(items, key=lambda x: x.id)
    ]
    return hashlib.sha256("\n".join(canon).encode("utf-8")).hexdigest()
