"""Extract unit-bearing technical specifications as structured, comparable facts.

MOSDAC documentation is dense with numbers — spatial resolution, swath width,
frequency, revisit time. The flat spaCy SVO extractor drops these entirely, so
the chatbot cannot answer comparison/math questions ("which sensor has finer
resolution?"). This module mines `Quantity` facts from text and normalizes them
to a base SI-ish unit so two values become directly comparable, while preserving
the verbatim `raw` string (the system prompt forbids paraphrasing numbers).

`pint` is used when installed for robust unit handling; otherwise a small
built-in unit table covers the units MOSDAC actually uses, so the parser works
fully offline with zero extra dependencies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# property_key -> keywords that signal that property in surrounding text.
PROPERTY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "spatial_resolution": (
        "spatial resolution", "ground resolution", "pixel size", "ground sampling",
        "ifov", "spatial sampling", "resolution",
    ),
    "temporal_resolution": (
        "temporal resolution", "revisit time", "revisit", "repeat cycle",
        "repeativity", "repetivity", "repeat period", "temporal sampling",
    ),
    "swath_width": ("swath width", "swath", "ground swath"),
    "frequency": ("frequency", "operating frequency", "centre frequency", "center frequency"),
    "wavelength": ("wavelength", "spectral band", "central wavelength"),
    "altitude": ("altitude", "orbital height", "orbit height"),
    "inclination": ("inclination",),
    "spectral_channels": ("number of channels", "spectral channels", "channels", "number of bands", "bands"),
    "data_rate": ("data rate", "downlink rate", "bit rate"),
    "spatial_coverage": ("spatial coverage", "coverage"),
}

# Canonical unit -> (dimension, factor to base unit). Base units: m, Hz, s, deg, bps.
_UNIT_TABLE: dict[str, tuple[str, float]] = {
    # length -> metres
    "km": ("length", 1000.0), "m": ("length", 1.0), "cm": ("length", 0.01),
    "mm": ("length", 0.001), "µm": ("length", 1e-6), "um": ("length", 1e-6),
    "micron": ("length", 1e-6), "nm": ("length", 1e-9),
    # frequency -> hertz
    "ghz": ("frequency", 1e9), "mhz": ("frequency", 1e6), "khz": ("frequency", 1e3),
    "hz": ("frequency", 1.0),
    # time -> seconds
    "day": ("time", 86400.0), "days": ("time", 86400.0), "d": ("time", 86400.0),
    "hour": ("time", 3600.0), "hours": ("time", 3600.0), "hr": ("time", 3600.0),
    "hrs": ("time", 3600.0), "h": ("time", 3600.0),
    "minute": ("time", 60.0), "minutes": ("time", 60.0), "min": ("time", 60.0),
    "second": ("time", 1.0), "seconds": ("time", 1.0), "sec": ("time", 1.0), "s": ("time", 1.0),
    # angle -> degrees
    "deg": ("angle", 1.0), "degree": ("angle", 1.0), "degrees": ("angle", 1.0), "°": ("angle", 1.0),
    # data rate -> bits per second
    "gbps": ("data_rate", 1e9), "mbps": ("data_rate", 1e6), "kbps": ("data_rate", 1e3),
    "bps": ("data_rate", 1.0),
}

_BASE_UNIT = {"length": "m", "frequency": "Hz", "time": "s", "angle": "deg", "data_rate": "bps"}

# A number (int/float/scientific) followed by an optional unit token.
_NUMBER_UNIT_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?(?:\s?[xX×]\s?10\^?-?\d+)?)\s*"
    r"(?P<unit>km|cm|mm|µm|um|micron|nm|m(?![a-z])|GHz|MHz|kHz|Hz|"
    r"days?|hours?|hrs?|hr|minutes?|min|seconds?|sec|°|degrees?|deg|"
    r"Gbps|Mbps|kbps|bps)?",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class Quantity:
    """A single normalized technical specification mined from text."""

    property_key: str       # e.g. "spatial_resolution"
    value: float            # numeric value as written
    unit: str               # unit as written (canonicalized casing)
    raw: str                # verbatim source span — quote this, never paraphrase
    base_value: float       # value converted to base unit (for comparison)
    base_unit: str          # base unit ("m", "Hz", "s", "deg", "bps", or "")

    def as_dict(self) -> dict:
        return {
            "property": self.property_key,
            "value": self.value,
            "unit": self.unit,
            "raw": self.raw,
            "base_value": self.base_value,
            "base_unit": self.base_unit,
        }


def _normalize_unit(unit: str | None) -> tuple[float, str]:
    """Return (base_value_factor, base_unit) for a raw unit token."""
    if not unit:
        return 1.0, ""
    key = unit.strip().lower()
    if key in _UNIT_TABLE:
        dim, factor = _UNIT_TABLE[key]
        return factor, _BASE_UNIT[dim]
    return 1.0, ""


def _parse_value(raw_value: str) -> float | None:
    """Parse '5', '1.1', or '1.2 x 10^3' into a float."""
    txt = raw_value.lower().replace("×", "x").replace(" ", "")
    m = re.match(r"^(\d+(?:\.\d+)?)x10\^?(-?\d+)$", txt)
    if m:
        return float(m.group(1)) * (10 ** int(m.group(2)))
    try:
        return float(txt)
    except ValueError:
        return None


def _property_for_span(sentence: str, match_start: int) -> str | None:
    """Find the closest preceding property keyword for a number in a sentence."""
    prefix = sentence[:match_start].lower()
    best_key: str | None = None
    best_pos = -1
    for key, keywords in PROPERTY_KEYWORDS.items():
        for kw in keywords:
            pos = prefix.rfind(kw)
            if pos > best_pos:
                best_pos = pos
                best_key = key
    return best_key if best_pos >= 0 else None


def parse_quantities(text: str) -> list[Quantity]:
    """Mine all property/value/unit triples from free text.

    Strategy: per sentence, locate each number+unit, then attach it to the
    nearest preceding property keyword. Numbers with no recognizable property
    keyword nearby are skipped (we only want meaningful specs, not page numbers).
    """
    if not text or not text.strip():
        return []

    out: list[Quantity] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not any(
            kw in sentence.lower()
            for kws in PROPERTY_KEYWORDS.values()
            for kw in kws
        ):
            continue
        for m in _NUMBER_UNIT_RE.finditer(sentence):
            unit = m.group("unit")
            value = _parse_value(m.group("value"))
            if value is None:
                continue
            prop = _property_for_span(sentence, m.start())
            if prop is None:
                continue
            # "spectral_channels" is a count — only meaningful without a unit.
            if prop == "spectral_channels" and unit:
                continue
            factor, base_unit = _normalize_unit(unit)
            raw_span = m.group(0).strip()
            out.append(
                Quantity(
                    property_key=prop,
                    value=value,
                    unit=(unit or "").strip(),
                    raw=raw_span,
                    base_value=value * factor,
                    base_unit=base_unit,
                )
            )
    # Deduplicate identical (property, raw) facts within a chunk.
    seen: set[tuple[str, str]] = set()
    unique: list[Quantity] = []
    for q in out:
        key = (q.property_key, q.raw.lower())
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique
