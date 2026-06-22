"""Docling-based PDF → Markdown parser (primary parser).

Produces one structured Markdown string per PDF:
  - headings/sections preserved,
  - tables as Markdown tables (TableFormer),
  - formulas as $$...$$ LaTeX (CodeFormula enrichment),
  - image/heatmap text extracted via system Tesseract OCR.

NO vision-language model is enabled — picture description/classification is OFF.
Image understanding is limited to OCR of text *inside* raster imagery.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from graph_rag.config import settings

logger = logging.getLogger(__name__)


def _should_force_full_page_ocr(path: Path) -> bool:
    """Atlas PDFs are raster imagery with burned-in labels — OCR every page.

    A text-layer heuristic would wrongly conclude 'has text' from a stray caption
    and skip OCR, dropping the geographic labels we need from heatmaps.
    """
    needle = settings.docling_force_full_page_ocr_dirs.lower()
    return bool(needle) and needle in str(path).lower().replace("\\", "/")


@lru_cache(maxsize=2)
def _build_converter(force_full_page_ocr: bool):
    """Build and cache a DocumentConverter. Model load is expensive — reuse it.

    Two cache slots: one for force-full-page-OCR (atlases), one for normal docs.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    ocr_options = TesseractCliOcrOptions(
        lang=[settings.docling_ocr_lang],
        force_full_page_ocr=force_full_page_ocr,
    )

    pipeline_options = PdfPipelineOptions()
    # OFFLINE: load models from the pre-downloaded artifacts dir (no Hub calls).
    if settings.docling_artifacts_path:
        pipeline_options.artifacts_path = settings.docling_artifacts_path
    pipeline_options.do_ocr = True
    pipeline_options.ocr_options = ocr_options
    pipeline_options.do_table_structure = settings.docling_do_table_structure
    pipeline_options.do_formula_enrichment = settings.docling_do_formula_enrichment
    pipeline_options.table_structure_options.do_cell_matching = True

    # NO VLM — do not describe or classify pictures.
    pipeline_options.do_picture_description = False
    pipeline_options.do_picture_classification = False
    pipeline_options.generate_picture_images = False

    if settings.docling_max_pages > 0:
        pipeline_options.page_range = (1, settings.docling_max_pages)

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def parse_pdf_to_markdown(path: Path) -> str:
    """Parse a PDF into a single Markdown string. Raises on failure.

    The caller (_load_pdf in loader.py) catches the exception and falls back to the
    legacy pypdf/PyMuPDF/OCR cascade, so a Docling failure never loses a document.
    """
    converter = _build_converter(_should_force_full_page_ocr(path))
    result = converter.convert(str(path))
    markdown = result.document.export_to_markdown()
    if not markdown or not markdown.strip():
        raise ValueError(f"Docling produced empty Markdown for {path.name}")
    logger.info("Docling parsed %s → %d chars of Markdown", path.name, len(markdown))
    return markdown
