"""GuardrailSettings — every control is an env-driven flag, fail-closed by default.

Set in .env or environment:
    GUARD_ENABLE=true
    GUARD_FAIL_CLOSED=true
    GUARD_PII_INPUT=true            GUARD_PII_OUTPUT=true
    GUARD_INJECTION=true            GUARD_INJECTION_SIM_THRESHOLD=0.80
    GUARD_SCOPE_GATE=true           GUARD_SCOPE_MIN_SIM=0.35
    GUARD_RETRIEVAL_MIN_SCORE=0.20  GUARD_MIN_SUPPORTING_PASSAGES=1
    GUARD_CITATION_VERIFY=true      GUARD_GROUNDING_MIN_SIM=0.40
    GUARD_TOXICITY=true             GUARD_RATE_LIMIT_PER_MIN=20
    GUARD_AUDIT=true                GUARD_SESSION_TTL_SECONDS=86400
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class GuardrailSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Master switches
    enable: bool = True
    fail_closed: bool = True

    # L1 Input
    pii_input: bool = True
    pii_output: bool = True
    injection: bool = True
    injection_sim_threshold: float = 0.80
    scope_gate: bool = True
    scope_min_sim: float = 0.35
    max_input_length: int = 2000

    # When the embedder is down, the scope gate and the injection embedding tier
    # cannot run. By default they fail OPEN (availability) but the degradation is
    # now ALWAYS observable (metric + WARN). Set EMBEDDER_REQUIRED=true to instead
    # fail CLOSED — refuse while the embedder is unavailable (strict prod posture).
    embedder_required: bool = False

    # L2 Retrieval
    retrieval_min_score: float = 0.20
    min_supporting_passages: int = 1
    source_allowlist: bool = True
    # Scan retrieved passages for injection directives (indirect injection, P1-3)
    # and neutralize them before they reach the prompt. Hits are kept intact for
    # grounding/citation; only the LLM-facing context copy is sanitized.
    context_injection_scan: bool = True

    # L4 Output
    citation_verify: bool = True
    grounding_min_sim: float = 0.40
    # What to do with ungrounded numbers/sentences detected at output time:
    #   "flag"   — log only (legacy behaviour; hallucinations still reach the user)
    #   "strip"  — remove ungrounded sentences; refuse if too little survives
    #   "refuse" — any ungrounded content → canonical "no info" refusal
    grounding_action: str = "strip"
    # In "strip" mode, refuse instead if more than this fraction of factual
    # sentences are ungrounded (the answer is mostly unsupported).
    grounding_max_ungrounded_ratio: float = 0.5
    toxicity: bool = True
    leakage_check: bool = True

    # L5 Audit/Abuse
    audit: bool = True
    rate_limit_per_min: int = 20
    session_ttl_seconds: int = 86400
    abuse_lockout_threshold: int = 10
    # Durable audit sink: when set, every PII-safe audit record is ALSO appended to
    # this file via a size-rotating handler (queryable, survives restarts). Empty =
    # stdout logger only. Never contains raw user text (see audit/logger.py).
    audit_log_path: str = ""
    audit_log_max_bytes: int = 50 * 1024 * 1024
    audit_log_backups: int = 5

    # Paths
    scope_centroid_path: str = "./guardrails_data/scope_centroid.npy"
    injection_corpus_path: str = "./tests/guardrails/injection_corpus.txt"
    # Optional: domain scope seed phrases, one per line (# comments allowed). When
    # set and present, REPLACES the built-in MOSDAC seeds so the same image serves
    # a different domain without a code change (P2-1). Empty → built-in seeds.
    scope_seed_path: str = ""


guardrail_settings = GuardrailSettings()
