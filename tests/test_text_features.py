"""Symbol-aware tokenization and structure-feature detection (text_features.py)."""
from graph_rag import text_features as tf


# ── Symbol-preserving tokenizer (BM25 channel) ──────────────────────────────

def test_latex_command_becomes_word_token():
    # \sigma must reduce to 'sigma' so a prose query "sigma" can match the formula.
    toks = tf.tokenize_symbolic(r"$$\sigma^0 = 10\log_{10}(P)$$")
    assert "sigma" in toks
    assert "^" in toks and "=" in toks and "_" in toks


def test_query_and_corpus_share_tokens():
    q = tf.tokenize_symbolic("what is sigma naught")
    d = tf.tokenize_symbolic(r"the backscatter coefficient \sigma")
    assert "sigma" in q and "sigma" in d


def test_numbers_with_decimals_preserved():
    toks = tf.tokenize_symbolic("frequency 5.6 GHz over 1400 km")
    assert "5.6" in toks and "1400" in toks


def test_ordinary_punctuation_dropped():
    toks = tf.tokenize_symbolic("Hello, world. (test) [ref]: value;")
    assert "," not in toks and "." not in toks and "(" not in toks and ":" not in toks


# ── Formula query detection & fragment extraction ───────────────────────────

def test_looks_like_formula_query():
    assert tf.looks_like_formula_query("value of sigma^0")
    assert tf.looks_like_formula_query(r"\sigma_0 definition")
    assert tf.looks_like_formula_query("compute σ₀")
    # A hyphenated sensor id is NOT a formula query (avoid false positives).
    assert not tf.looks_like_formula_query("swath width of INSAT-3D")


def test_extract_formula_fragments():
    frags = tf.extract_formula_fragments("compute sigma^0 and T_b together")
    norm = [tf.normalize_for_match(f) for f in frags]
    assert "sigma^0" in norm and "t_b" in norm


def test_inline_math_fragment_stripped_of_delimiters():
    frags = tf.extract_formula_fragments(r"the term $E=mc^2$ here")
    assert any(tf.normalize_for_match(f) == "e=mc^2" for f in frags)


def test_normalize_for_match_whitespace_insensitive():
    assert tf.normalize_for_match("σ ^ 0") == tf.normalize_for_match("σ^0")
    assert tf.normalize_for_match("1400 km") == "1400km"


# ── Structure features (ingestion-side chunk tagging) ───────────────────────

def test_has_formula():
    assert tf.has_formula(r"text $$E=mc^2$$ more")
    assert tf.has_formula(r"inline $x_i$ value")
    assert tf.has_formula(r"a \frac command")
    assert not tf.has_formula("plain prose with numbers 12 and 34")


def test_has_table():
    assert tf.has_table("| a | b |\n|---|---|\n| 1 | 2 |")
    assert not tf.has_table("no table, just a | pipe in prose")


def test_numeric_density_range():
    assert tf.numeric_density("") == 0.0
    nd = tf.numeric_density("swath 1400 km resolution 1 km orbit 36000 km")
    assert 0.0 < nd <= 1.0
    assert tf.numeric_density("no digits here at all") == 0.0
