"""Shared, dependency-light text features: symbol-aware tokenization + structure detection.

One source of truth for "how do we read math, symbols and structure out of a
chunk or a query". Used by BOTH:

  * ingestion  — `enrich_chunks` tags chunks with ``has_formula`` / ``has_table`` /
    ``numeric_density`` so retrieval can bias toward quantitative content.
  * retrieval  — the BM25 keyword channel tokenizes with ``tokenize_symbolic`` so
    LaTeX/operators/Greek survive, and the exact-formula fast path matches the
    verbatim symbol runs from a query against the corpus.

This module imports only the standard library, so it is import-cycle-free and
cheap to load from anywhere (ingestion, retrieval, guardrails).
"""
from __future__ import annotations

import re

# ── Math / structure detectors ──────────────────────────────────────────────
_DISPLAY_MATH = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)")
_LATEX_CMD = re.compile(r"\\[A-Za-z]+")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_WORDISH = re.compile(r"[^\s]+")

# Symbol-bearing fragments worth matching EXACTLY (formula fast path): a $…$
# span, a LaTeX command (optionally with a sub/superscript), or a bare
# sub/superscript run like ``sigma^0`` / ``T_b``.
_FORMULA_FRAGMENT = re.compile(
    r"\$\$.+?\$\$"                                   # display math
    r"|\$.+?\$"                                      # inline math
    r"|\\[A-Za-z]+(?:\s*[\^_]\s*\{?[A-Za-z0-9]+\}?)?"  # \sigma, \sigma^0, \frac
    r"|[A-Za-z0-9]+\s*[\^_]\s*\{?[A-Za-z0-9]+\}?"   # sigma^0, T_b, 10^3
)

# A query "looks mathematical" if it carries any of these. ``-`` `*` `/` are
# deliberately excluded — they appear in ordinary prose (dates, ranges, paths)
# and would cause false positives.
_FORMULA_QUERY = re.compile(r"[=^_~]|\\[A-Za-z]+|\$|[≤≥≈≠±×÷∑∫√∂∇πλσθφμΩ°]")

# ── Symbol-aware tokenization (for BM25 index AND query) ─────────────────────
# One token = a run of letters, OR a number (decimals kept), OR a single
# "interesting" symbol. Ordinary prose punctuation is dropped so BM25 is not
# flooded; math/operator/Greek symbols are kept so a formula stays searchable.
_TOKEN_PATTERN = re.compile(r"[A-Za-z]+|\d+(?:\.\d+)?|[^\sA-Za-z0-9]", re.UNICODE)
_KEEP_SYMBOLS = set("=^_+<>%°µπλσθφαβγδωΩΔ∑∫√∞±×÷≤≥≈≠∂∇⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉")

_WS = re.compile(r"\s+")


def tokenize_symbolic(text: str) -> list[str]:
    """Tokenize for keyword search while preserving math/symbol tokens.

    Guarantees that the SAME function is applied to corpus and query (so they
    can match). Transformations:
      * ``\\sigma`` → ``sigma``  (LaTeX command name, so prose "sigma" matches)
      * letters     → lowercased word tokens
      * ``1400`` / ``4.5`` → numeric tokens kept verbatim
      * ``= ^ _ °`` Greek/operator symbols → kept as their own tokens
      * ordinary punctuation (``. , ; : ( ) [ ] | # …``) → dropped
    """
    if not text:
        return []
    # \sigma -> " sigma " so the command name becomes a normal word token.
    text = _LATEX_CMD.sub(lambda m: " " + m.group(0)[1:] + " ", text)
    tokens: list[str] = []
    for m in _TOKEN_PATTERN.finditer(text):
        tok = m.group(0)
        first = tok[0]
        if first.isalpha():
            tokens.append(tok.lower())
        elif first.isdigit():
            tokens.append(tok)
        elif tok in _KEEP_SYMBOLS:
            tokens.append(tok)
        # else: ordinary punctuation — dropped.
    return tokens


def normalize_for_match(text: str) -> str:
    """Whitespace-stripped, lowercased form for verbatim substring matching.

    Removing all whitespace lets ``σ ^ 0`` match ``σ^0`` and ``1400 km`` match
    ``1400km`` regardless of how the OCR/LaTeX spaced the formula.
    """
    return _WS.sub("", (text or "")).lower()


def extract_formula_fragments(text: str) -> list[str]:
    """Distinctive symbol-bearing fragments to match exactly (formula fast path).

    Returns de-duplicated fragments with ``$`` delimiters stripped. Empty list
    when the text has none, so callers can cheaply skip the fast path.
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _FORMULA_FRAGMENT.finditer(text or ""):
        frag = m.group(0).strip("$ ").strip()
        key = normalize_for_match(frag)
        if len(key) >= 2 and key not in seen:
            seen.add(key)
            out.append(frag)
    return out


def looks_like_formula_query(query: str) -> bool:
    """True when a query carries math notation worth the exact-match fast path."""
    return bool(_FORMULA_QUERY.search(query or ""))


# ── Chunk structure features (ingestion-side tagging) ───────────────────────
def has_formula(text: str) -> bool:
    """True if the text contains display/inline math or a LaTeX command."""
    t = text or ""
    return bool(_DISPLAY_MATH.search(t) or _INLINE_MATH.search(t) or _LATEX_CMD.search(t))


def has_table(text: str) -> bool:
    """True if the text contains a Markdown table (≥2 pipe-delimited rows)."""
    return len(_TABLE_ROW.findall(text or "")) >= 2


def numeric_density(text: str) -> float:
    """Fraction of whitespace tokens that contain a number, in [0, 1].

    A cheap signal of quantitative content (specs, measurements, table rows) used
    to bias retrieval toward chunks that actually carry numbers for a numeric query.
    """
    tokens = _WORDISH.findall(text or "")
    if not tokens:
        return 0.0
    numeric = sum(1 for t in tokens if _NUMBER.search(t))
    return round(numeric / len(tokens), 4)
