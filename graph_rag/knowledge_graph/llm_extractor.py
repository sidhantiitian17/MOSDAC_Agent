"""Schema-guided LLM extraction of typed triples from a text chunk.

This is the primary extractor: it prompts a Tabby ML model (selected by
TABBY_EXTRACTION_MODEL — switchable from .env without code changes) with the
MOSDAC ontology and asks for strict JSON triples. Compared with the spaCy SVO
parser it captures implicit, cross-clause, and table-derived relationships and
assigns correct domain types — producing a far denser, more reasoning-friendly
graph.

Design choices:
  * Relations are clamped to the controlled vocabulary via ontology helpers, so
    a noisy small model can't pollute the schema.
  * Numbers are intentionally NOT asked of the LLM here — the deterministic
    `quantity_parser` mines specs far more reliably. Each tool does what it is
    best at: LLM for relations, regex for measurements.
  * Streaming is used because Tabby ML times out on non-streaming calls.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache

from graph_rag.config import settings
from graph_rag.knowledge_graph.extractor import EntityRelationExtractor, Triple
from graph_rag.knowledge_graph.ontology import canonical_relation, normalize_node_type

logger = logging.getLogger(__name__)

# Relations/types advertised to the model. Kept short so a small model stays focused.
_ALLOWED_RELATIONS = (
    "CARRIES, HAS_INSTRUMENT, HAS_BAND, PRODUCES, MEASURES, LAUNCHED_BY, "
    "LAUNCHED_ON, OPERATED_BY, OPERATES_IN, PART_OF, DERIVED_FROM, USES, "
    "PROVIDES, LOCATED_IN, APPLIES_TO, RELATED_TO"
)
_ALLOWED_TYPES = (
    "Satellite, Sensor, Instrument, Mission, Product, Parameter, Algorithm, "
    "Organization, Location, Orbit, Band, Application, Concept"
)

_SYSTEM_PROMPT = f"""You extract a knowledge graph from text about satellites, \
sensors, and Earth-observation data (ISRO / MOSDAC domain).

Return ONLY valid minified JSON, no prose, no markdown fences, of the form:
{{"triples":[{{"subject":"...","subject_type":"...","relation":"...","object":"...","object_type":"...","confidence":0.0}}]}}

Rules:
- relation must be one of: {_ALLOWED_RELATIONS}
- subject_type and object_type must be one of: {_ALLOWED_TYPES}
- Choose the MOST SPECIFIC relation that the text states.
- Only extract facts explicitly stated in the text. Never invent entities or links.
- Output each distinct fact only ONCE. Never repeat a triple.
- subject and object are short entity names (noun phrases), not full sentences.
- If nothing is extractable, return {{"triples":[]}}.

Example:
Text: "Oceansat-2 carries the OCM sensor, which measures chlorophyll concentration."
JSON: {{"triples":[{{"subject":"Oceansat-2","subject_type":"Satellite","relation":"CARRIES","object":"OCM","object_type":"Sensor","confidence":0.95}},{{"subject":"OCM","subject_type":"Sensor","relation":"MEASURES","object":"chlorophyll concentration","object_type":"Parameter","confidence":0.9}}]}}"""


def _all_balanced_objects(text: str) -> list[str]:
    """Return every balanced {...} substring, at any nesting depth.

    Stack-based: an object is recorded when its closing brace is seen. A
    truncated outer object never closes, so its already-complete inner objects
    are still recovered — this is what lets us salvage triples from a response
    that hit the token limit mid-array.
    """
    spans: list[str] = []
    stack: list[int] = []
    for i, ch in enumerate(text):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            spans.append(text[start : i + 1])
    return spans


def _extract_json(raw: str) -> dict | None:
    """Parse model output to {"triples":[...]}, tolerating truncation and noise.

    1. Strict: return the first object that parses and carries a `triples` key.
    2. Salvage: if the wrapper is truncated, recover the individual complete
       triple objects that did make it through.
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip code fences if the model added them despite instructions.
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()

    objects = _all_balanced_objects(text)

    # Strict pass — a well-formed wrapper.
    for obj in objects:
        if '"triples"' in obj:
            try:
                data = json.loads(obj)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and "triples" in data:
                return data

    # Salvage pass — pull complete triple objects out of a truncated array.
    salvaged: list[dict] = []
    for obj in objects:
        if '"subject"' not in obj or '"object"' not in obj:
            continue
        try:
            parsed = json.loads(obj)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "subject" in parsed and "object" in parsed:
            salvaged.append(parsed)
    return {"triples": salvaged} if salvaged else None


class LLMExtractor:
    """Extract typed Triples from text using a Tabby ML chat model."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_token: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        self._model = model or settings.extraction_model_name()
        self._base_url = base_url or settings.extraction_llm_base_url
        self._api_token = api_token or settings.extraction_llm_api_token
        self._temperature = settings.extraction_temperature if temperature is None else temperature
        self._max_tokens = max_tokens or settings.extraction_max_tokens
        self._client = None

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ImportError("openai not installed. Run: pip install openai") from exc
            if not self._api_token:
                raise ValueError(
                    "EXTRACTION_LLM_API_TOKEN / TABBY_API_TOKEN is not set — "
                    "cannot run LLM extraction."
                )
            self._client = OpenAI(api_key=self._api_token, base_url=self._base_url)
        return self._client

    def _complete(self, messages: list[dict], max_tokens: int | None = None) -> str:
        """Stream a chat completion and return the accumulated text."""
        client = self._get_client()
        stream = client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            max_tokens=max_tokens or self._max_tokens,
            stream=True,  # Tabby ML requires streaming
        )
        parts: list[str] = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                parts.append(delta.content)
        return "".join(parts)

    def extract(
        self,
        text: str,
        source_chunk_id: str = "",
        source_path: str = "",
    ) -> list[Triple]:
        if not text or not text.strip():
            return []
        snippet = text[: settings.extraction_max_chars]
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Text:\n{snippet}\n\nJSON:"},
        ]
        try:
            raw = self._complete(messages)
        except Exception as exc:
            logger.warning("LLM extraction call failed (%s); skipping chunk.", exc)
            return []

        data = _extract_json(raw)
        if not data or "triples" not in data:
            logger.debug("LLM extraction returned no parseable JSON for chunk %s", source_chunk_id)
            return []

        triples: list[Triple] = []
        seen: set[tuple[str, str, str]] = set()
        for row in data.get("triples", []):
            triple = self._validate_row(row, source_chunk_id, source_path)
            if triple is None:
                continue
            sig = (triple.subject.lower(), triple.relation, triple.object_.lower())
            if sig in seen:  # small models repeat triples — keep one
                continue
            seen.add(sig)
            triples.append(triple)
        return triples

    @staticmethod
    def _validate_row(row: dict, chunk_id: str, path: str) -> Triple | None:
        if not isinstance(row, dict):
            return None
        subject = str(row.get("subject", "")).strip()
        object_ = str(row.get("object", "")).strip()
        if not subject or not object_ or subject.lower() == object_.lower():
            return None
        relation = canonical_relation(str(row.get("relation", "")))
        if relation is None:
            return None
        try:
            confidence = float(row.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence = 0.8
        confidence = min(max(confidence, 0.0), 1.0)
        return Triple(
            subject=subject,
            subject_type=normalize_node_type(row.get("subject_type")),
            relation=relation,
            object_=object_,
            object_type=normalize_node_type(row.get("object_type")),
            source_chunk_id=chunk_id,
            source_path=path,
            confidence=confidence,
        )

    # Reuse spaCy NER for query-time entity spotting so this class is a drop-in
    # replacement for EntityRelationExtractor wherever extract_entities is needed.
    def extract_entities(self, text: str) -> list[tuple[str, str]]:
        return EntityRelationExtractor().extract_entities(text)


@lru_cache(maxsize=1)
def llm_extraction_available() -> bool:
    """Cheap one-shot probe: is the extraction endpoint reachable and answering?"""
    if not settings.extraction_llm_api_token:
        return False
    try:
        out = LLMExtractor()._complete(
            [{"role": "user", "content": "Reply with the single character: 1"}],
            max_tokens=5,
        )
        return bool(out and out.strip())
    except Exception as exc:
        logger.info("LLM extraction unavailable (%s) — will use spaCy fallback.", exc)
        return False


def get_extractor():
    """Factory honoring EXTRACTION_BACKEND: returns an extractor with .extract().

    - "spacy": always the offline spaCy SVO extractor.
    - "llm":   always the LLM extractor (errors if endpoint is down).
    - "auto":  LLM when reachable, else spaCy.
    """
    backend = (settings.extraction_backend or "auto").strip().lower()
    if backend == "spacy":
        logger.info("KG extraction backend: spaCy (forced).")
        return EntityRelationExtractor()
    if backend == "llm":
        logger.info("KG extraction backend: LLM model=%s", settings.extraction_model_name())
        return LLMExtractor()
    # auto
    if llm_extraction_available():
        logger.info("KG extraction backend: LLM model=%s (auto).", settings.extraction_model_name())
        return LLMExtractor()
    logger.info("KG extraction backend: spaCy (auto fallback — LLM endpoint unreachable).")
    return EntityRelationExtractor()
