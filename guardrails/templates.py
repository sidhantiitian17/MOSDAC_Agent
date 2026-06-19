"""Canonical refusal and redaction messages.

All user-visible refusals must come from here so the wording stays consistent
and can be reviewed / translated centrally.
"""

REFUSAL_GENERIC = (
    "I'm sorry, I cannot assist with that request. "
    "For MOSDAC-related queries please visit mosdac.gov.in "
    "or contact the MOSDAC helpdesk."
)

REFUSAL_OFF_TOPIC = (
    "Your question appears to be outside the scope of MOSDAC services. "
    "I can only answer questions about MOSDAC, ISRO satellites, meteorology, "
    "and oceanography. Please visit mosdac.gov.in for more information."
)

REFUSAL_NO_CONTEXT = (
    "I do not have enough information in my knowledge base to answer that. "
    "Please refer to mosdac.gov.in or contact the MOSDAC helpdesk."
)

REFUSAL_INJECTION = (
    "I cannot process that request. Please rephrase your question about "
    "MOSDAC services, satellites, or weather and ocean data."
)

REFUSAL_RATE_LIMITED = (
    "Too many requests. Please wait a moment before trying again."
)

ERROR_GENERIC = (
    "An error occurred processing your request. "
    "Please try again or contact the MOSDAC helpdesk."
)
