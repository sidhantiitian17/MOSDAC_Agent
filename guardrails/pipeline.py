"""GuardrailPipeline: orchestrates all security layers (L1 + L2 + L4 + L5)."""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from guardrails.config import guardrail_settings as cfg
from guardrails.decisions import Action, GuardDecision
from guardrails.retrieval.grounding_gate import CitationRegistry
from guardrails.templates import (
    ERROR_GENERIC,
    REFUSAL_INJECTION,
    REFUSAL_NO_CONTEXT,
    REFUSAL_OFF_TOPIC,
    REFUSAL_GENERIC,
)

logger = logging.getLogger(__name__)


def _metric_inc(name: str, labels: dict | None = None) -> None:
    """Best-effort metric increment — never lets observability break a request."""
    try:
        from observability import inc

        inc(name, labels)
    except Exception:
        pass


class GuardrailPipeline:
    """Stateless orchestrator; one singleton shared across requests."""

    def check_input(self, text: str, session_id: str = "") -> GuardDecision:
        if not cfg.enable:
            return GuardDecision(action=Action.ALLOW, cleaned_text=text)
        if cfg.audit and session_id:
            from guardrails.audit.abuse import is_locked_out
            if is_locked_out(session_id, cfg.abuse_lockout_threshold):
                return GuardDecision(
                    action=Action.REFUSE,
                    cleaned_text=REFUSAL_GENERIC,
                    reasons=["abuse_lockout"],
                )
        try:
            return self._check_input_inner(text, session_id)
        except Exception as exc:
            logger.exception("Input guard error: %s", exc)
            if cfg.fail_closed:
                return GuardDecision(action=Action.REFUSE, cleaned_text=ERROR_GENERIC, reasons=["guard_error"])
            return GuardDecision(action=Action.ALLOW, cleaned_text=text, reasons=["guard_error_fail_open"])

    def _check_input_inner(self, text: str, session_id: str) -> GuardDecision:
        from guardrails.input import normalize as norm_mod, injection, scope
        from guardrails.input import pii

        cleaned = norm_mod.normalize(text, max_length=cfg.max_input_length)
        if not cleaned or len(cleaned) < 2:
            return GuardDecision(action=Action.REFUSE, cleaned_text=REFUSAL_GENERIC, reasons=["empty_input"])
        if not norm_mod.check_charset(cleaned):
            return GuardDecision(action=Action.REFUSE, cleaned_text=REFUSAL_GENERIC, reasons=["invalid_charset"])

        if cfg.injection:
            action, category, _ = injection.check(cleaned)
            if action == "refuse":
                self._record_abuse(session_id)
                _metric_inc("guardrail_refusals_total", {"reason": "injection"})
                return GuardDecision(
                    action=Action.REFUSE,
                    cleaned_text=REFUSAL_INJECTION,
                    reasons=[f"injection:{category}"],
                )
            is_attack, degraded = injection.embedding_similarity_status(
                cleaned, cfg.injection_sim_threshold
            )
            if degraded:
                blocked = self._on_degraded("injection", session_id)
                if blocked is not None:
                    return blocked
            elif is_attack:
                self._record_abuse(session_id)
                _metric_inc("guardrail_refusals_total", {"reason": "injection_embedding"})
                return GuardDecision(
                    action=Action.REFUSE,
                    cleaned_text=REFUSAL_INJECTION,
                    reasons=["injection:embedding_sim"],
                )

        if cfg.pii_input:
            cleaned = pii.redact(cleaned)

        if cfg.scope_gate:
            in_scope, sim, degraded = scope.check_with_status(
                cleaned, cfg.scope_min_sim, cfg.scope_centroid_path
            )
            if degraded:
                blocked = self._on_degraded("scope", session_id)
                if blocked is not None:
                    return blocked
            elif not in_scope:
                logger.info("Off-topic blocked (sim=%.3f < %.3f)", sim, cfg.scope_min_sim)
                self._record_abuse(session_id)
                _metric_inc("guardrail_refusals_total", {"reason": "off_topic"})
                return GuardDecision(
                    action=Action.REFUSE,
                    cleaned_text=REFUSAL_OFF_TOPIC,
                    reasons=["off_topic", f"scope_sim={sim:.3f}"],
                )

        return GuardDecision(action=Action.ALLOW, cleaned_text=cleaned)

    @staticmethod
    def _on_degraded(check: str, session_id: str) -> Optional[GuardDecision]:
        """Handle an embedder-dependent check that could not run (P0-5).

        Always emits an observable signal (metric + WARN). Returns a REFUSE
        decision when GUARD_EMBEDDER_REQUIRED is set (strict prod posture), else
        None to fail open (preserving availability) — but never silently.
        """
        logger.warning("Guardrail '%s' ran DEGRADED (embedder unavailable).", check)
        _metric_inc("guardrail_degraded_total", {"check": check})
        if cfg.embedder_required:
            _metric_inc("guardrail_refusals_total", {"reason": f"{check}_degraded"})
            return GuardDecision(
                action=Action.REFUSE,
                cleaned_text=ERROR_GENERIC,
                reasons=[f"{check}_degraded_fail_closed"],
            )
        return None

    def check_retrieval_groundable(
        self,
        hits: list,
        manifest_path: str = "",
    ) -> Tuple[bool, CitationRegistry, float]:
        if not cfg.enable:
            return True, CitationRegistry(), 1.0
        try:
            from guardrails.retrieval.grounding_gate import build_registry_from_hits, check_groundable
            passes, top_score = check_groundable(hits, cfg.retrieval_min_score, cfg.min_supporting_passages)
            if not passes:
                return False, CitationRegistry(), top_score
            registry = build_registry_from_hits(
                hits, manifest_path=manifest_path, check_allowlist=cfg.source_allowlist
            )
            return True, registry, top_score
        except Exception as exc:
            logger.exception("Retrieval grounding gate error: %s", exc)
            if cfg.fail_closed:
                return False, CitationRegistry(), 0.0
            return True, CitationRegistry(), 0.0

    def check_output(
        self,
        answer: str,
        registry: CitationRegistry,
        passages: Optional[List[str]] = None,
        context: str = "",
    ) -> Tuple[str, List[dict], List[str]]:
        if not cfg.enable:
            return answer, [], []
        try:
            return self._check_output_inner(answer, registry, passages or [], context)
        except Exception as exc:
            logger.exception("Output guard error: %s", exc)
            if cfg.fail_closed:
                return REFUSAL_GENERIC, [], ["output_guard_error"]
            return answer, [], ["output_guard_error_fail_open"]

    def _check_output_inner(
        self,
        answer: str,
        registry: CitationRegistry,
        passages: List[str],
        context: str,
    ) -> Tuple[str, List[dict], List[str]]:
        from guardrails.output import citation_verify, grounding_check, leakage, pii_out, safety

        reasons: List[str] = []

        if cfg.leakage_check and leakage.check_leakage(answer):
            answer = leakage.scrub_leakage(answer)
            reasons.append("leakage_scrubbed")

        citations: List[dict] = []
        if cfg.citation_verify and registry:
            answer, citations = citation_verify.verify(answer, registry)

        # ── Grounding enforcement (config-driven) ───────────────────────────
        # Detect ungrounded numbers and sentences, then ACT on them per
        # GUARD_GROUNDING_ACTION: "flag" (log only, legacy), "strip" (remove the
        # ungrounded sentences, refusing if too little survives), or "refuse"
        # (any ungrounded content → canonical refusal). Strips the spec
        # hallucinations that previously reached the user.
        bad_nums: List[str] = []
        if context:
            _, bad_nums = grounding_check.check_numeric_grounding(answer, context)
            if bad_nums:
                reasons.append(f"ungrounded_numbers:{len(bad_nums)}")

        ungrounded: List[str] = []
        if passages and cfg.grounding_min_sim > 0:
            _, ungrounded = grounding_check.check_sentence_grounding(answer, passages, cfg.grounding_min_sim)
            if ungrounded:
                reasons.append(f"ungrounded_sentences:{len(ungrounded)}")

        action = (cfg.grounding_action or "flag").lower()
        if action != "flag" and (bad_nums or ungrounded):
            total = max(1, len(grounding_check._factual_sentences(answer)))
            over_ratio = len(ungrounded) / total > cfg.grounding_max_ungrounded_ratio
            if action == "refuse" or (bad_nums and not ungrounded) or over_ratio:
                # Bare fabricated numbers, an over-the-ratio answer, or refuse-mode
                # → don't try to salvage; refuse outright.
                reasons.append("grounding_refused")
                return REFUSAL_NO_CONTEXT, [], reasons
            if action == "strip" and ungrounded:
                stripped = grounding_check.strip_ungrounded(answer, ungrounded)
                if not stripped:
                    reasons.append("grounding_refused")
                    return REFUSAL_NO_CONTEXT, [], reasons
                if stripped != answer:
                    reasons.append("grounding_stripped")
                    # Re-verify citations against the trimmed answer.
                    if cfg.citation_verify and registry:
                        stripped, citations = citation_verify.verify(stripped, registry)
                answer = stripped

        if cfg.pii_output:
            answer = pii_out.redact_output(answer)

        if cfg.toxicity and safety.check_toxicity(answer):
            reasons.append("toxicity")
            return REFUSAL_GENERIC, [], reasons

        return answer, citations, reasons

    @staticmethod
    def _record_abuse(session_id: str) -> None:
        if cfg.audit and session_id:
            try:
                from guardrails.audit.abuse import record_event
                record_event(session_id)
            except Exception:
                pass


_pipeline: GuardrailPipeline | None = None


def get_pipeline() -> GuardrailPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = GuardrailPipeline()
    return _pipeline
