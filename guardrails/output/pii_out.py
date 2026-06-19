"""Redact PII from LLM output before it is returned to the user (L4).

Reuses guardrails.input.pii — same Presidio + India-regex logic applied to output.
This catches PII echoed from the user's own input (e.g., if they asked a question
containing their Aadhaar and the model quoted it back) or PII that leaked from
a source document into the retrieved passages.
"""
from __future__ import annotations


def redact_output(text: str) -> str:
    """Apply PII redaction to the model's response."""
    from guardrails.input.pii import redact
    return redact(text)
