"""Offline tests for the statistics helpers (graph_rag/eval/stats.py)."""
from __future__ import annotations

from graph_rag.eval.stats import (
    bootstrap_ci,
    excludes_zero,
    mean,
    paired_bootstrap_delta,
)


def test_mean_ignores_none():
    assert mean([1.0, 2.0, None, 3.0]) == 2.0
    assert mean([]) == 0.0


def test_bootstrap_ci_deterministic_with_seed():
    vals = [0.8, 0.9, 1.0, 0.7, 0.85, 0.95]
    a = bootstrap_ci(vals, seed=42)
    b = bootstrap_ci(vals, seed=42)
    assert (a.mean, a.lo, a.hi, a.n) == (b.mean, b.lo, b.hi, b.n)
    assert a.lo <= a.mean <= a.hi and a.n == 6


def test_bootstrap_ci_degenerate_small_n():
    assert bootstrap_ci([]).n == 0
    one = bootstrap_ci([0.5])
    assert one.n == 1 and one.lo == one.hi == one.mean == 0.5


def test_paired_delta_detects_real_improvement():
    baseline = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
    candidate = [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9]
    ci = paired_bootstrap_delta(baseline, candidate)
    assert ci.mean > 0 and excludes_zero(ci)


def test_paired_delta_no_difference_includes_zero():
    same = [0.6, 0.7, 0.8, 0.6, 0.7, 0.8]
    ci = paired_bootstrap_delta(same, same)
    assert ci.mean == 0.0 and not excludes_zero(ci)


def test_paired_delta_requires_aligned_lists():
    import pytest

    with pytest.raises(ValueError):
        paired_bootstrap_delta([0.1, 0.2], [0.1])
