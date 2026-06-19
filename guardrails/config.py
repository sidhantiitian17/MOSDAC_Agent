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

    # L2 Retrieval
    retrieval_min_score: float = 0.20
    min_supporting_passages: int = 1
    source_allowlist: bool = True

    # L4 Output
    citation_verify: bool = True
    grounding_min_sim: float = 0.40
    toxicity: bool = True
    leakage_check: bool = True

    # L5 Audit/Abuse
    audit: bool = True
    rate_limit_per_min: int = 20
    session_ttl_seconds: int = 86400
    abuse_lockout_threshold: int = 10

    # Paths
    scope_centroid_path: str = "./guardrails_data/scope_centroid.npy"
    injection_corpus_path: str = "./tests/guardrails/injection_corpus.txt"


guardrail_settings = GuardrailSettings()
