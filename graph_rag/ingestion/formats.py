"""Central, extensible registry of ingestible file formats.

Single source of truth mapping a file *suffix* to how it is loaded, the
canonical ``source_type`` it is tagged with, which Docling ``InputFormat`` (if
any) parses it, and any per-type metadata defaults.

Why this exists (see alldoc.md §9, "New source_type values break downstream
filters"): the previous pipeline hard-coded ``if suffix in PDF_SUFFIXES`` /
``if source_type == "pdf"`` checks scattered across loader.py and
preprocessor.py. Adding a format meant editing several files and risked a
silently-dropped type. This registry replaces every such hard-coded branch with
one declarative table:

  * Adding a new format = one ``_register(...)`` row here. No edits to dispatch
    code, no new ``if suffix == ...`` branches anywhere.
  * Kill-switches (office / images) are config-driven, so a whole category can be
    disabled from ``.env`` without touching code.
  * ``source_type`` values flow straight from this table into chunk metadata, so
    loader, preprocessor, and any downstream consumer always agree on the tag.

This module imports only ``settings`` — never docling, loader, or preprocessor —
so it is import-cycle-free and cheap to load.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from graph_rag.config import settings

# ── Routing categories: which loader path a format takes. ───────────────────
CATEGORY_PDF = "pdf"          # dedicated _load_pdf cascade (Docling + pypdf/OCR fallback)
CATEGORY_HTML = "html"        # BS4 junk filter → Docling, via preprocess_file
CATEGORY_TEXT = "text"        # raw read, no Docling
CATEGORY_DOCLING = "docling"  # generic Docling parse via preprocess_file


@dataclass(frozen=True)
class FormatSpec:
    """Declarative description of how one family of suffixes is ingested."""

    source_type: str                   # canonical metadata tag, e.g. "docx", "image"
    category: str                      # one of the CATEGORY_* routing buckets
    docling_format: str | None = None  # InputFormat enum NAME ("DOCX", "IMAGE", ...)
    enable_flag: str | None = None     # settings attr gating this format (kill-switch)
    size_limit_flag: str | None = None # settings attr (MB) bounding file size (OOM guard)
    pre_normalize: str | None = None   # normalization hook id, e.g. "gif_to_png"
    apply_quality_gate: bool = False   # run the garbage-data gate after parse
    preserve_page_number: bool = False # keep per-chunk page_number from the splitter
    metadata_defaults: dict = field(default_factory=dict)


# ── The registry. To support a new format, add a row below — nothing else. ──
_REGISTRY: dict[str, FormatSpec] = {}


def _register(suffixes: set[str], spec: FormatSpec) -> None:
    for suffix in suffixes:
        _REGISTRY[suffix] = spec


# Core formats (always enabled) ----------------------------------------------
_register(
    {".pdf"},
    FormatSpec(
        source_type="pdf",
        category=CATEGORY_PDF,
        docling_format="PDF",
        preserve_page_number=True,
    ),
)
_register(
    {".html", ".htm", ".xhtml"},
    FormatSpec(
        source_type="html",
        category=CATEGORY_HTML,
        docling_format="HTML",
        # The BS4 junk filter is HTML's own quality mechanism; the deterministic
        # gate targets the high-junk office/image formats (alldoc.md §5).
        apply_quality_gate=False,
        metadata_defaults={"domain_type": "web_scrape"},
    ),
)
_register(
    {".txt", ".md"},
    FormatSpec(source_type="text", category=CATEGORY_TEXT),
)

# Office / structured documents (gated by INGEST_ENABLE_OFFICE) ---------------
_register(
    {".docx"},
    FormatSpec(
        source_type="docx",
        category=CATEGORY_DOCLING,
        docling_format="DOCX",
        enable_flag="ingest_enable_office",
        apply_quality_gate=True,
        metadata_defaults={"domain_type": "document"},
    ),
)
_register(
    {".pptx"},
    FormatSpec(
        source_type="pptx",
        category=CATEGORY_DOCLING,
        docling_format="PPTX",
        enable_flag="ingest_enable_office",
        apply_quality_gate=True,
        metadata_defaults={"domain_type": "document"},
    ),
)
_register(
    {".adoc"},
    FormatSpec(
        source_type="asciidoc",
        category=CATEGORY_DOCLING,
        docling_format="ASCIIDOC",
        enable_flag="ingest_enable_office",
        apply_quality_gate=True,
        metadata_defaults={"domain_type": "document"},
    ),
)
_register(
    {".xlsx"},
    FormatSpec(
        source_type="xlsx",
        category=CATEGORY_DOCLING,
        docling_format="XLSX",
        enable_flag="ingest_enable_office",
        apply_quality_gate=True,
        metadata_defaults={"domain_type": "tabular"},
    ),
)
_register(
    {".csv"},
    FormatSpec(
        source_type="csv",
        category=CATEGORY_DOCLING,
        docling_format="CSV",
        enable_flag="ingest_enable_office",
        apply_quality_gate=True,
        metadata_defaults={"domain_type": "tabular"},
    ),
)

# Images, OCR-only (gated by INGEST_ENABLE_IMAGES + size cap) -----------------
_IMAGE_SPEC = FormatSpec(
    source_type="image",
    category=CATEGORY_DOCLING,
    docling_format="IMAGE",
    enable_flag="ingest_enable_images",
    size_limit_flag="ingest_image_max_mb",
    apply_quality_gate=True,
    metadata_defaults={"domain_type": "image_ocr"},
)
_register({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}, _IMAGE_SPEC)
# GIF is not a first-class Docling IMAGE format; normalise the first frame to PNG
# first (alldoc.md §3). Same spec otherwise.
_register(
    {".gif"},
    FormatSpec(
        source_type="image",
        category=CATEGORY_DOCLING,
        docling_format="IMAGE",
        enable_flag="ingest_enable_images",
        size_limit_flag="ingest_image_max_mb",
        pre_normalize="gif_to_png",
        apply_quality_gate=True,
        metadata_defaults={"domain_type": "image_ocr"},
    ),
)


# ── Lookup / introspection helpers (the only public API). ───────────────────

def get_spec(suffix: str) -> FormatSpec | None:
    """Return the FormatSpec for a file suffix (".docx"), or None if unknown."""
    return _REGISTRY.get(suffix.lower())


def _flag_enabled(flag: str | None) -> bool:
    """A None flag means 'core / always on'; otherwise read the settings bool."""
    if not flag:
        return True
    return bool(getattr(settings, flag, True))


def is_enabled(spec: FormatSpec) -> bool:
    """True unless this format's kill-switch is turned off in config."""
    return _flag_enabled(spec.enable_flag)


def is_supported(suffix: str) -> bool:
    spec = get_spec(suffix)
    return spec is not None and is_enabled(spec)


def supported_suffixes() -> set[str]:
    """Every currently-enabled suffix (respects the office/image kill-switches)."""
    return {suffix for suffix, spec in _REGISTRY.items() if is_enabled(spec)}


def docling_input_format_names() -> list[str]:
    """Distinct InputFormat enum NAMES for all enabled Docling-parsed formats.

    The unified converter builds its ``allowed_formats`` from this — so a new
    registry row automatically flows into the converter with no edit there.
    """
    names: list[str] = []
    for spec in _REGISTRY.values():
        if spec.docling_format and is_enabled(spec) and spec.docling_format not in names:
            names.append(spec.docling_format)
    return names


def _spec_for_source_type(source_type: str) -> FormatSpec | None:
    for spec in _REGISTRY.values():
        if spec.source_type == source_type:
            return spec
    return None


def metadata_defaults_for(source_type: str) -> dict:
    """Per-type default metadata (e.g. domain_type) for ``enrich_chunks``."""
    spec = _spec_for_source_type(source_type)
    return dict(spec.metadata_defaults) if spec else {}


def preserves_page_number(source_type: str) -> bool:
    """Whether chunks of this type should carry a per-chunk page_number."""
    spec = _spec_for_source_type(source_type)
    return bool(spec and spec.preserve_page_number)


def within_size_limit(spec: FormatSpec, path: Path) -> tuple[bool, str]:
    """Enforce the per-format size cap (OOM guard). Returns (ok, reason)."""
    if not spec.size_limit_flag:
        return True, ""
    limit = getattr(settings, spec.size_limit_flag, 0) or 0
    if limit <= 0:
        return True, ""
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
    except OSError:
        return True, ""  # can't stat → let the loader try and fail gracefully
    if size_mb > limit:
        return False, f"{size_mb:.0f} MB > {limit} MB cap"
    return True, ""
