"""Chat service - pure business logic, decoupled from FastAPI.

This layer can be unit-tested without spinning up the HTTP server, and reused
by any transport (FastAPI, gRPC, CLI, etc.).

Guardrails integration (guardplan.md):
    L1  check_input()            - before any retrieval/LLM spend
    L2  check_retrieval_groundable() - after retrieval, before LLM
    L4  check_output()           - after LLM, before return/store
    L5  log_request()            - audit every request
"""
from __future__ import annotations

import base64
import binascii
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage

from chat_api.config import chat_api_settings
from chat_api.session import SessionStore
from graph_rag.config import settings as graph_settings

logger = logging.getLogger(__name__)


def _metric_inc(name: str, labels: Optional[Dict[str, str]] = None) -> None:
    try:
        from observability import inc

        inc(name, labels)
    except Exception:
        pass


def _metric_observe(name: str, value: float) -> None:
    try:
        from observability import observe

        observe(name, value)
    except Exception:
        pass


class ChatService:
    """Coordinates retriever, chain, and LLM around a session store."""

    def __init__(
        self,
        retriever,
        chain,
        llm,
        sessions: SessionStore,
        max_history: Optional[int] = None,
        repo=None,
    ) -> None:
        self._retriever = retriever
        self._chain = chain
        self._llm = llm
        self._sessions = sessions
        # Conversation store for per-user persisted history (None = persistence off,
        # every request behaves like an anonymous ephemeral session).
        self._repo = repo
        self._max_history = max_history if max_history is not None else chat_api_settings.max_history_turns
        self._contextualizer = None
        self._summarizer = None
        self._titler = None
        self._answer_cache = None
        if chat_api_settings.enable_answer_cache:
            from chat_api.answer_cache import AnswerCache

            self._answer_cache = AnswerCache(
                max_entries=chat_api_settings.answer_cache_size,
                ttl_seconds=chat_api_settings.answer_cache_ttl_seconds,
            )

    def _contextualize(self, message: str, history_prefix: str) -> str:
        try:
            if self._contextualizer is None:
                from graph_rag.retrieval.query_contextualizer import QueryContextualizer
                self._contextualizer = QueryContextualizer()
            return self._contextualizer.contextualize(message, history_prefix).search_query
        except Exception:
            return message

    def _get_summarizer(self):
        if self._summarizer is None:
            from graph_rag.chat.summarizer import ConversationSummarizer
            self._summarizer = ConversationSummarizer()
        return self._summarizer

    def _get_titler(self):
        if self._titler is None:
            from chat_api.titler import ConversationTitler
            self._titler = ConversationTitler()
        return self._titler

    def _remember_overflow(self, session_id: str) -> None:
        if not graph_settings.enable_conversation_summary:
            self._sessions.trim(session_id, self._max_history)
            return
        keep = graph_settings.summary_keep_recent_turns
        turns = self._sessions.get(session_id)
        if len(turns) <= keep * 2:
            return
        overflow = turns[: len(turns) - keep * 2]
        summary = self._get_summarizer().update(self._sessions.get_summary(session_id), overflow)
        self._sessions.set_summary(session_id, summary)
        self._sessions.trim(session_id, keep)

    @staticmethod
    def _format_history_prefix(turns: List[Dict[str, Any]], summary: str = "") -> str:
        """Render a list of {role, content} turns into the prompt history prefix.

        Shared by the ephemeral session-store path and the persisted per-user
        conversation path so both produce an identical prompt shape.
        """
        if not turns and not summary:
            return ""
        lines: List[str] = []
        if summary:
            lines.append(f"Summary of earlier conversation: {summary}")
        for t in turns:
            role = "User" if t["role"] == "user" else "Assistant"
            content = t["content"] if isinstance(t["content"], str) else "[image]"
            lines.append(f"{role}: {content}")
        return "Conversation so far:\n" + "\n".join(lines) + "\n\nNew question: "

    def _build_history_prefix(self, session_id: str) -> str:
        turns = self._sessions.get(session_id)
        summary = ""
        if graph_settings.enable_conversation_summary:
            summary = self._sessions.get_summary(session_id)
        return self._format_history_prefix(turns, summary)

    def _history_prefix_from_messages(self, messages) -> str:
        """Build the prompt prefix from persisted conversation messages (DB)."""
        recent = messages[-self._max_history * 2:] if self._max_history else messages
        turns = [{"role": m.role, "content": m.content} for m in recent]
        return self._format_history_prefix(turns)

    def _validate_screenshot(self, screenshot_b64: str) -> None:
        if not chat_api_settings.enable_screenshot:
            raise ValueError("Screenshot uploads are disabled in this deployment.")
        try:
            raw_size = (len(screenshot_b64) * 3) // 4
        except Exception:
            raw_size = 0
        if raw_size > chat_api_settings.max_screenshot_bytes:
            raise ValueError(
                f"Screenshot too large: {raw_size} bytes > "
                f"{chat_api_settings.max_screenshot_bytes} byte limit."
            )
        try:
            base64.b64decode(screenshot_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Invalid base64 screenshot data: {exc}") from exc
        # Hard gate (M6): never feed an image to a text-only model. Checked here —
        # after size/format validation but BEFORE any conversation row is created
        # in the authenticated path — so a no-VLM deployment refuses cleanly.
        if not chat_api_settings.vision_model:
            raise ValueError(
                "Screenshot analysis is not available: no vision model is configured "
                "on this deployment (set CHAT_API_VISION_MODEL)."
            )

    def _answer_with_image(
        self,
        message: str,
        screenshot_b64: str,
        mime: str,
        history_prefix: str,
    ) -> Tuple[str, List[dict], bool, float]:
        """Multimodal answer path. Returns (answer, citations, grounded, top_score).

        Runs the SAME L2 grounding gate as the text path (P0-3) so attaching an
        image can no longer bypass the relevance floor — the bot still answers
        only when the knowledge base supports it.
        """
        from guardrails import get_pipeline
        from guardrails.templates import REFUSAL_NO_CONTEXT
        from graph_rag.config import settings as gs

        self._validate_screenshot(screenshot_b64)  # also hard-gates on vision_model (M6)
        search_query = self._contextualize(message, history_prefix)
        ctx = self._retriever.retrieve(search_query)
        hits = ctx.get("_hits", [])

        pipeline = get_pipeline()
        # L2 grounding gate — refuse BEFORE spending the (vision) LLM call.
        passes, registry, top_score = pipeline.check_retrieval_groundable(
            hits, gs.ingest_manifest_path
        )
        if not passes:
            return REFUSAL_NO_CONTEXT, [], False, top_score

        rag_preamble = (
            f"KNOWLEDGE GRAPH:\n{ctx['graph_context']}\n\n"
            f"DOCUMENT PASSAGES:\n{ctx['vector_context']}\n\n"
            f"User question about the attached screenshot: {message}"
        )
        content: List[Dict[str, Any]] = []
        if history_prefix:
            content.append({"type": "text", "text": history_prefix})
        content.append({"type": "text", "text": rag_preamble})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{screenshot_b64}"},
        })
        from graph_rag.llm.tabby_client import llm_slot

        with llm_slot():
            response = self._llm.invoke([HumanMessage(content=content)])
        raw_answer = response.content if hasattr(response, "content") else str(response)

        # L4 output guard — same leakage/PII/toxicity/citation checks as the text path,
        # now using the registry built from the grounded hits.
        passages = [h.text for h in hits]
        context = ctx.get("vector_context", "") + "\n" + ctx.get("graph_context", "")
        clean_answer, citations, _reasons = pipeline.check_output(
            raw_answer, registry, passages, context
        )
        return clean_answer, citations, True, top_score

    def _answer_text_only(
        self,
        message: str,
        history_prefix: str,
    ) -> Tuple[str, List[dict], bool, float]:
        """
        Returns (answer, citations, grounded, top_score).
        Performs pre-flight retrieval for grounding gate + citation registry,
        then invokes the chain (second retrieval inside chain is acceptable overhead).
        """
        from guardrails import get_pipeline
        from guardrails.templates import REFUSAL_NO_CONTEXT
        from graph_rag.config import settings as gs

        pipeline = get_pipeline()
        manifest_path = gs.ingest_manifest_path

        # Answer cache: serve a previously grounded answer for an identical
        # (question, history, corpus) without re-running retrieval + LLM.
        if self._answer_cache is not None:
            cached = self._answer_cache.get(message, history_prefix)
            if cached is not None:
                _metric_inc("answer_cache_total", {"result": "hit"})
                return cached[0], cached[1], True, 1.0
            _metric_inc("answer_cache_total", {"result": "miss"})

        search_query = self._contextualize(message, history_prefix)

        # Pre-flight retrieval for grounding gate (L2)
        try:
            ctx = self._retriever.retrieve(search_query)
            hits = ctx.get("_hits", [])
        except Exception as exc:
            logger.warning("Pre-flight retrieval failed: %s", exc)
            return REFUSAL_NO_CONTEXT, [], False, 0.0

        passes, registry, top_score = pipeline.check_retrieval_groundable(hits, manifest_path)
        if not passes:
            return REFUSAL_NO_CONTEXT, [], False, top_score

        # Invoke the chain, passing pre-retrieved context so the chain skips its
        # internal retrieval call (eliminates the double round-trip to the retriever).
        # The concurrency slot (P1-5) throttles load on the shared LLM endpoint.
        from graph_rag.llm.tabby_client import llm_slot

        with llm_slot():
            answer = self._chain.invoke({
                "question": message,
                "history": history_prefix,
                "pre_retrieved": {
                    "graph_context": ctx.get("graph_context", ""),
                    "vector_context": ctx.get("vector_context", ""),
                },
            })

        # L4 output guard
        passages = [h.text for h in hits]
        context = ctx.get("vector_context", "") + "\n" + ctx.get("graph_context", "")
        clean_answer, citations, _reasons = pipeline.check_output(answer, registry, passages, context)

        # Cache grounded, non-refused answers only.
        if self._answer_cache is not None and clean_answer != REFUSAL_NO_CONTEXT:
            self._answer_cache.put(message, history_prefix, clean_answer, citations)

        return clean_answer, citations, True, top_score

    def _generate(
        self,
        safe_message: str,
        history_prefix: str,
        screenshot_b64: Optional[str],
        screenshot_mime: Optional[str],
    ) -> Tuple[str, List[dict], bool, bool, float]:
        """Run the answer pipeline (image or text) given a precomputed history
        prefix. Shared by the ephemeral and persisted chat entry points so the
        load-bearing RAG + guardrail logic lives in exactly one place.

        Returns (answer, citations, grounded, refused, top_score).
        """
        from guardrails.templates import REFUSAL_NO_CONTEXT

        if screenshot_b64:
            answer, citations, grounded, top_score = self._answer_with_image(
                message=safe_message,
                screenshot_b64=screenshot_b64,
                mime=screenshot_mime or "image/png",
                history_prefix=history_prefix,
            )
        else:
            answer, citations, grounded, top_score = self._answer_text_only(
                safe_message, history_prefix
            )
        refused = (answer == REFUSAL_NO_CONTEXT)
        return answer, citations, grounded, refused, top_score

    def chat(
        self,
        session_id: str,
        message: str,
        screenshot_b64: Optional[str] = None,
        screenshot_mime: Optional[str] = "image/png",
    ) -> Tuple[str, List[dict], bool, bool]:
        """
        Entry point (anonymous / ephemeral). History lives in the session store.
        Returns (answer, citations, grounded, refused).
        """
        from guardrails import get_pipeline
        from guardrails.audit.logger import log_request
        from guardrails.config import guardrail_settings as gcfg
        from guardrails.templates import REFUSAL_NO_CONTEXT

        pipeline = get_pipeline()
        start = time.monotonic()

        self._remember_overflow(session_id)

        # L1 Input guard
        decision = pipeline.check_input(message, session_id)
        if decision.is_refused:
            latency = (time.monotonic() - start) * 1000
            if gcfg.audit:
                log_request(
                    session_id=session_id,
                    action="refuse",
                    reason_codes=decision.reasons,
                    grounded=False,
                    refused=True,
                    top_score=0.0,
                    latency_ms=latency,
                    has_citations=False,
                )
            return decision.cleaned_text, [], False, True

        safe_message = decision.cleaned_text

        history_prefix = self._build_history_prefix(session_id)
        answer, citations, grounded, refused, top_score = self._generate(
            safe_message, history_prefix, screenshot_b64, screenshot_mime
        )

        # Store only the PII-redacted message (L5)
        self._sessions.append(session_id, "user", safe_message)
        self._sessions.append(session_id, "assistant", answer)

        latency = (time.monotonic() - start) * 1000
        _metric_inc("chat_requests_total", {"action": "refuse" if refused else "allow"})
        _metric_observe("chat_request_latency_ms", latency)
        if gcfg.audit:
            log_request(
                session_id=session_id,
                action="refuse" if refused else "allow",
                reason_codes=[],
                grounded=grounded,
                refused=refused,
                top_score=top_score,
                latency_ms=latency,
                has_citations=bool(citations),
            )

        return answer, citations, grounded, refused

    def chat_authenticated(
        self,
        *,
        user,
        session_id: str,
        message: str,
        conversation_id: Optional[str] = None,
        screenshot_b64: Optional[str] = None,
        screenshot_mime: Optional[str] = "image/png",
        background=None,
    ) -> Tuple[str, List[dict], bool, bool, Optional[str]]:
        """Authenticated, persisted chat for a single user.

        Returns (answer, citations, grounded, refused, conversation_id). A new
        conversation is created when ``conversation_id`` is None; an existing one is
        continued only if the user owns it (else :class:`ConversationNotFoundError`).
        The brand-new conversation gets a short LLM title generated off the request
        path via ``background``.
        """
        from guardrails import get_pipeline
        from guardrails.audit.logger import log_request
        from guardrails.config import guardrail_settings as gcfg
        from chat_api.db.repository import ConversationNotFoundError

        # Persistence disabled → fall back to the ephemeral session-store path.
        if self._repo is None:
            answer, citations, grounded, refused = self.chat(
                session_id=session_id,
                message=message,
                screenshot_b64=screenshot_b64,
                screenshot_mime=screenshot_mime,
            )
            return answer, citations, grounded, refused, None

        pipeline = get_pipeline()
        start = time.monotonic()

        # L1 input guard (abuse/audit keyed on session_id, as in the anonymous path).
        decision = pipeline.check_input(message, session_id)
        if decision.is_refused:
            latency = (time.monotonic() - start) * 1000
            if gcfg.audit:
                log_request(
                    session_id=session_id, action="refuse", reason_codes=decision.reasons,
                    grounded=False, refused=True, top_score=0.0, latency_ms=latency,
                    has_citations=False,
                )
            return decision.cleaned_text, [], False, True, conversation_id
        safe_message = decision.cleaned_text

        # Validate a screenshot BEFORE creating a conversation so a bad upload never
        # leaves an empty row behind.
        if screenshot_b64:
            self._validate_screenshot(screenshot_b64)

        # Resolve / create the conversation (ownership enforced in the repo).
        is_new = conversation_id is None
        if is_new:
            conv = self._repo.create_conversation(user.id)
            conversation_id = conv.id
            history_prefix = ""
        else:
            conv = self._repo.get_conversation(user.id, conversation_id)
            if conv is None:
                raise ConversationNotFoundError(conversation_id)
            history_prefix = self._history_prefix_from_messages(
                self._repo.list_messages(user.id, conversation_id)
            )

        answer, citations, grounded, refused, top_score = self._generate(
            safe_message, history_prefix, screenshot_b64, screenshot_mime
        )

        # Persist the turn (PII-redacted user message + assistant answer).
        self._repo.append_message(user.id, conversation_id, "user", safe_message)
        self._repo.append_message(user.id, conversation_id, "assistant", answer)

        # Title a brand-new, answered conversation off the request path.
        if is_new and not refused and background is not None:
            background.add_task(
                self._title_conversation, user.id, conversation_id, safe_message, answer
            )

        latency = (time.monotonic() - start) * 1000
        _metric_inc("chat_requests_total", {"action": "refuse" if refused else "allow"})
        _metric_observe("chat_request_latency_ms", latency)
        if gcfg.audit:
            log_request(
                session_id=session_id, action="refuse" if refused else "allow",
                reason_codes=[], grounded=grounded, refused=refused, top_score=top_score,
                latency_ms=latency, has_citations=bool(citations),
            )
        return answer, citations, grounded, refused, conversation_id

    def _title_conversation(self, user_id: str, conversation_id: str, question: str, answer: str) -> None:
        """Background task: generate and store a short conversation title."""
        if self._repo is None:
            return
        try:
            title = self._get_titler().make_title(question, answer)
            self._repo.update_title(user_id, conversation_id, title)
        except Exception as exc:  # noqa: BLE001 — titling is best-effort
            logger.info("Title update failed (%s); keeping default.", exc)

    # ── Conversation management (per-user, ownership-enforced) ───────────────
    def list_conversations(self, user_id: str, limit: int = 50):
        if self._repo is None:
            return []
        return self._repo.list_conversations(user_id, limit)

    def get_conversation_with_messages(self, user_id: str, conversation_id: str):
        """Return (Conversation, [Message]) or None if not found / not owned."""
        if self._repo is None:
            return None
        conv = self._repo.get_conversation(user_id, conversation_id)
        if conv is None:
            return None
        return conv, self._repo.list_messages(user_id, conversation_id)

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        if self._repo is None:
            return False
        return self._repo.delete_conversation(user_id, conversation_id)

    def chat_stream(self, session_id: str, message: str, user=None, conversation_id=None, background=None):
        """SSE generator (P1-6): yields ``token`` events while generating, then a
        single authoritative ``final`` event whose answer has passed the L4 guard.

        The input guard (L1) and grounding gate (L2) run BEFORE any streaming, so a
        refusal is emitted as a one-shot ``final`` event with no tokens.
        """
        import json as _json

        from guardrails import get_pipeline
        from guardrails.audit.logger import log_request
        from guardrails.config import guardrail_settings as gcfg
        from guardrails.templates import REFUSAL_NO_CONTEXT
        from graph_rag.config import settings as gs
        from graph_rag.llm.tabby_client import llm_slot

        def _event(kind: str, payload: dict) -> str:
            return f"event: {kind}\ndata: {_json.dumps(payload)}\n\n"

        # Flush an SSE comment immediately so the proxy/client see bytes before the
        # retrieval + LLM-prefill gap (which precedes the first real token). This
        # resets nginx's proxy_read_timeout and lets the widget show "connected".
        yield ": keepalive\n\n"

        def _final(answer, citations, grounded, refused):
            _metric_inc("chat_requests_total", {"action": "refuse" if refused else "allow"})
            return _event("final", {
                "answer": answer, "session_id": session_id,
                "conversation_id": conversation_id,
                "citations": citations, "grounded": grounded, "refused": refused,
            })

        # Persisted per-user history when an authenticated user + repo are present;
        # otherwise the ephemeral session-store path (anonymous behaviour).
        use_db = bool(user) and self._repo is not None
        is_new = False

        start = time.monotonic()
        pipeline = get_pipeline()
        if not use_db:
            self._remember_overflow(session_id)

        # L1 input guard
        decision = pipeline.check_input(message, session_id)
        if decision.is_refused:
            yield _final(decision.cleaned_text, [], False, True)
            return
        safe_message = decision.cleaned_text

        # Resolve conversation + history.
        if use_db:
            if conversation_id is None:
                is_new = True          # created at the end, after a successful answer
                history_prefix = ""
            else:
                conv = self._repo.get_conversation(user.id, conversation_id)
                if conv is None:
                    yield _final("Conversation not found.", [], False, True)
                    return
                history_prefix = self._history_prefix_from_messages(
                    self._repo.list_messages(user.id, conversation_id)
                )
        else:
            history_prefix = self._build_history_prefix(session_id)

        if self._answer_cache is not None:
            cached = self._answer_cache.get(safe_message, history_prefix)
            if cached is not None:
                _metric_inc("answer_cache_total", {"result": "hit"})
                yield _event("token", {"text": cached[0]})
                yield _final(cached[0], cached[1], True, False)
                return

        search_query = self._contextualize(safe_message, history_prefix)
        try:
            ctx = self._retriever.retrieve(search_query)
            hits = ctx.get("_hits", [])
        except Exception as exc:
            logger.warning("Stream pre-flight retrieval failed: %s", exc)
            yield _final(REFUSAL_NO_CONTEXT, [], False, True)
            return

        passes, registry, top_score = pipeline.check_retrieval_groundable(hits, gs.ingest_manifest_path)
        if not passes:
            yield _final(REFUSAL_NO_CONTEXT, [], False, True)
            return

        # Stream generation; accumulate for the post-stream output guard.
        chunks: List[str] = []
        try:
            with llm_slot():
                for piece in self._chain.stream({
                    "question": safe_message,
                    "history": history_prefix,
                    "pre_retrieved": {
                        "graph_context": ctx.get("graph_context", ""),
                        "vector_context": ctx.get("vector_context", ""),
                    },
                }):
                    text = piece if isinstance(piece, str) else getattr(piece, "content", str(piece))
                    if text:
                        chunks.append(text)
                        yield _event("token", {"text": text})
        except Exception as exc:
            logger.exception("Streaming generation failed: %s", exc)
            yield _final(REFUSAL_NO_CONTEXT, [], False, True)
            return

        raw_answer = "".join(chunks)
        passages = [h.text for h in hits]
        context = ctx.get("vector_context", "") + "\n" + ctx.get("graph_context", "")
        clean_answer, citations, _reasons = pipeline.check_output(raw_answer, registry, passages, context)
        refused = (clean_answer == REFUSAL_NO_CONTEXT)

        if use_db:
            if is_new:
                conv = self._repo.create_conversation(user.id)
                conversation_id = conv.id
            self._repo.append_message(user.id, conversation_id, "user", safe_message)
            self._repo.append_message(user.id, conversation_id, "assistant", clean_answer)
            if is_new and not refused and background is not None:
                background.add_task(
                    self._title_conversation, user.id, conversation_id, safe_message, clean_answer
                )
        else:
            self._sessions.append(session_id, "user", safe_message)
            self._sessions.append(session_id, "assistant", clean_answer)
        if self._answer_cache is not None and not refused:
            self._answer_cache.put(safe_message, history_prefix, clean_answer, citations)

        latency = (time.monotonic() - start) * 1000
        _metric_observe("chat_request_latency_ms", latency)
        if gcfg.audit:
            log_request(
                session_id=session_id, action="refuse" if refused else "allow",
                reason_codes=[], grounded=not refused, refused=refused,
                top_score=top_score, latency_ms=latency, has_citations=bool(citations),
            )
        yield _final(clean_answer, citations, not refused, refused)

    def clear_session(self, session_id: str) -> None:
        self._sessions.clear(session_id)

    def reload(self) -> dict:
        """Pick up a re-ingest without a restart (P1-4): rebuild the keyword index,
        invalidate the answer cache, and drop stale embedding/guardrail caches."""
        reloaded = {"bm25": False, "answer_cache": False, "corpus_version": None}
        try:
            if hasattr(self._retriever, "reload"):
                self._retriever.reload()
                reloaded["bm25"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Retriever reload failed: %s", exc)
        try:
            from chat_api.answer_cache import bump_corpus_version

            reloaded["corpus_version"] = bump_corpus_version()
            if self._answer_cache is not None:
                self._answer_cache.clear()
                reloaded["answer_cache"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Answer-cache reload failed: %s", exc)
        try:
            from guardrails.input.injection import reset_attack_corpus_cache
            from guardrails.input.scope import invalidate_centroid_cache

            reset_attack_corpus_cache()
            invalidate_centroid_cache()
        except Exception:
            pass
        return reloaded
