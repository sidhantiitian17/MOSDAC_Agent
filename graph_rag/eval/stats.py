"""Statistical helpers for the eval gate (evaluation_plan.md §8).

A headline metric without a confidence interval is not a result. These helpers
produce a bootstrap 95% CI for every reported mean and a paired bootstrap test for
A/B regression checks, so a 0.02 swing is not mistaken for progress.

Pure functions, deterministic under a fixed seed, unit-tested offline.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CI:
    mean: float
    lo: float
    hi: float
    n: int

    def __str__(self) -> str:
        if self.n == 0:
            return "n/a (n=0)"
        return f"{self.mean:.3f} [{self.lo:.3f}, {self.hi:.3f}] (n={self.n})"


def mean(values: list[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def bootstrap_ci(
    values: list[float],
    *,
    confidence: float = 0.95,
    n_resamples: int = 2000,
    seed: int = 1234,
) -> CI:
    """Percentile bootstrap CI for the mean of *values*.

    Deterministic given *seed*. Falls back to a degenerate interval (mean==lo==hi)
    for n<=1 so callers never special-case tiny strata.
    """
    clean = [float(v) for v in values if v is not None]
    n = len(clean)
    if n == 0:
        return CI(mean=0.0, lo=0.0, hi=0.0, n=0)
    m = sum(clean) / n
    if n == 1:
        return CI(mean=m, lo=m, hi=m, n=1)

    import numpy as np

    rng = np.random.default_rng(seed)
    arr = np.asarray(clean, dtype=float)
    # Resample indices with replacement → (n_resamples, n) → per-row means.
    idx = rng.integers(0, n, size=(n_resamples, n))
    boot_means = arr[idx].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    lo = float(np.quantile(boot_means, alpha))
    hi = float(np.quantile(boot_means, 1.0 - alpha))
    return CI(mean=m, lo=lo, hi=hi, n=n)


def paired_bootstrap_delta(
    baseline: list[float],
    candidate: list[float],
    *,
    confidence: float = 0.95,
    n_resamples: int = 2000,
    seed: int = 1234,
) -> CI:
    """Bootstrap CI on the paired mean difference (candidate − baseline).

    Requires the two lists to be aligned per-item (same questions, same order). A
    change is "real" only when the returned interval excludes 0 (§8). The ``mean``
    field carries the point estimate of the difference.
    """
    if len(baseline) != len(candidate):
        raise ValueError(
            f"paired test needs aligned lists; got {len(baseline)} vs {len(candidate)}"
        )
    diffs = [float(c) - float(b) for b, c in zip(baseline, candidate)]
    return bootstrap_ci(diffs, confidence=confidence, n_resamples=n_resamples, seed=seed)


def excludes_zero(ci: CI) -> bool:
    """True when the CI lies entirely above or entirely below 0 (a significant change)."""
    return ci.lo > 0.0 or ci.hi < 0.0
