"""Guardrails: defense-in-depth security for the MOSDAC chatbot.

Layers:
    L0 Transport/Gateway  - TLS, CORS, rate-limit, security headers
    L1 Input Guard        - normalize, PII redact, injection detect, scope gate
    L2 Retrieval Guard    - cypher-safe, source allowlist, relevance-floor gate
    L3 Generation Guard   - prompt hardening (system_prompt.txt)
    L4 Output Guard       - citation verify, grounding, PII, toxicity, leakage
    L5 Audit/Abuse        - PII-safe log, abuse counter, lockout
"""
from guardrails.decisions import Action, GuardDecision
from guardrails.pipeline import GuardrailPipeline, get_pipeline

__all__ = ["Action", "GuardDecision", "GuardrailPipeline", "get_pipeline"]
