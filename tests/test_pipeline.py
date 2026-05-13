"""Smoke test for the IngestionPipeline (skips heavy network/DB steps)."""
from __future__ import annotations

from pathlib import Path

from graph_rag.ingestion.pipeline import IngestionPipeline


def test_pipeline_empty_folder_returns_zero_stats(tmp_path: Path):
    pipeline = IngestionPipeline(folders=[tmp_path], skip_vector=True, skip_graph=True)
    stats = pipeline.run()
    assert stats.documents_loaded == 0
    assert stats.chunks_created == 0
    assert stats.errors == []


def test_pipeline_processes_files_in_dry_mode(tmp_path: Path):
    (tmp_path / "a.txt").write_text("Apple is a large company.", encoding="utf-8")
    (tmp_path / "b.txt").write_text("Microsoft owns GitHub.", encoding="utf-8")

    pipeline = IngestionPipeline(folders=[tmp_path], skip_vector=True, skip_graph=True)
    stats = pipeline.run()
    assert stats.documents_loaded == 2
    assert stats.chunks_created >= 2
    assert stats.errors == []
