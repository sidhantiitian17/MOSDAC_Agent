"""Entity + relationship extraction via spaCy NER and dependency parsing.

Produces Triple objects (subject, relation, object) that can be stored in Neo4j.
Falls back to lighter parsing when spaCy isn't installed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

logger = logging.getLogger(__name__)

ENTITY_LABELS = {"PERSON", "ORG", "GPE", "LOC", "PRODUCT", "EVENT", "NORP", "WORK_OF_ART", "FAC"}


@dataclass(frozen=True)
class Triple:
    subject: str
    subject_type: str
    relation: str
    object_: str
    object_type: str
    source_chunk_id: str = ""
    source_path: str = ""
    confidence: float = 1.0

    def as_dict(self) -> dict:
        return {
            "subject": self.subject,
            "subject_type": self.subject_type,
            "relation": self.relation,
            "object": self.object_,
            "object_type": self.object_type,
            "source_chunk_id": self.source_chunk_id,
            "source_path": self.source_path,
            "confidence": self.confidence,
        }


@lru_cache(maxsize=1)
def _load_spacy():
    try:
        import spacy
    except ImportError:
        logger.warning("spaCy not installed; using regex-only fallback extractor.")
        return None

    for model in ("en_core_web_trf", "en_core_web_sm"):
        try:
            return spacy.load(model)
        except Exception:
            continue

    logger.warning(
        "No spaCy English model installed. Run: python -m spacy download en_core_web_sm"
    )
    return None


def _sanitize_relation(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", text.strip().upper())
    cleaned = cleaned.strip("_")
    return cleaned or "RELATED_TO"


class EntityRelationExtractor:
    """Extracts (subject -[relation]-> object) triples from free text."""

    def __init__(self):
        self._nlp = _load_spacy()

    def extract(
        self,
        text: str,
        source_chunk_id: str = "",
        source_path: str = "",
    ) -> list[Triple]:
        if not text or not text.strip():
            return []
        if self._nlp is None:
            return self._fallback(text, source_chunk_id, source_path)
        return list(self._spacy_triples(text, source_chunk_id, source_path))

    def extract_entities(self, text: str) -> list[tuple[str, str]]:
        if not text or self._nlp is None:
            return []
        doc = self._nlp(text)
        return [(ent.text.strip(), ent.label_) for ent in doc.ents if ent.label_ in ENTITY_LABELS]

    def _spacy_triples(
        self,
        text: str,
        source_chunk_id: str,
        source_path: str,
    ) -> Iterable[Triple]:
        doc = self._nlp(text)
        for sent in doc.sents:
            subjects = []
            objects = []
            verb = None
            for tok in sent:
                if tok.dep_ in ("nsubj", "nsubjpass") and tok.head.pos_ in ("VERB", "AUX"):
                    subjects.append(tok)
                    verb = tok.head
                if tok.dep_ in ("dobj", "pobj", "attr", "oprd") and tok.head.pos_ in ("VERB", "ADP", "AUX"):
                    objects.append(tok)
                    if verb is None and tok.head.pos_ in ("VERB", "AUX"):
                        verb = tok.head

            if not (subjects and objects and verb):
                continue

            relation = _sanitize_relation(verb.lemma_)
            for s in subjects:
                s_span = self._noun_span(s)
                s_type = self._entity_type(s_span, doc)
                for o in objects:
                    o_span = self._noun_span(o)
                    o_type = self._entity_type(o_span, doc)
                    if s_span and o_span and s_span.lower() != o_span.lower():
                        yield Triple(
                            subject=s_span,
                            subject_type=s_type,
                            relation=relation,
                            object_=o_span,
                            object_type=o_type,
                            source_chunk_id=source_chunk_id,
                            source_path=source_path,
                            confidence=0.7,
                        )

    @staticmethod
    def _noun_span(token) -> str:
        for chunk in token.sent.noun_chunks:
            if token in chunk:
                return chunk.text.strip()
        return token.text.strip()

    @staticmethod
    def _entity_type(span: str, doc) -> str:
        s = span.lower()
        for ent in doc.ents:
            if ent.text.lower() == s:
                return ent.label_
        return "CONCEPT"

    @staticmethod
    def _fallback(text: str, source_chunk_id: str, source_path: str) -> list[Triple]:
        """Regex SVO fallback when spaCy isn't available."""
        triples: list[Triple] = []
        pattern = re.compile(
            r"([A-Z][\w &-]{2,40})\s+(is|are|was|were|has|have|owns|owned|founded|acquired|developed|created|leads|manages|builds)\s+([A-Z][\w &-]{2,40})"
        )
        for m in pattern.finditer(text):
            subj, verb, obj = m.group(1), m.group(2), m.group(3)
            triples.append(
                Triple(
                    subject=subj.strip(),
                    subject_type="CONCEPT",
                    relation=_sanitize_relation(verb),
                    object_=obj.strip(),
                    object_type="CONCEPT",
                    source_chunk_id=source_chunk_id,
                    source_path=source_path,
                    confidence=0.4,
                )
            )
        return triples
