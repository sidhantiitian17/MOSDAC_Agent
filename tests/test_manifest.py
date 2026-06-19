"""Tests for incremental ingestion — content hashing, the manifest, and loader skipping."""
from __future__ import annotations

from pathlib import Path

from graph_rag.ingestion.loader import load_all_documents
from graph_rag.ingestion.manifest import IngestionManifest, compute_file_hash


# ── compute_file_hash ──────────────────────────────────────────────────────
def test_same_content_same_hash_regardless_of_path(tmp_path: Path):
    # Arrange
    a = tmp_path / "a.txt"
    b = tmp_path / "nested" / "b.txt"
    b.parent.mkdir()
    a.write_text("identical bytes", encoding="utf-8")
    b.write_text("identical bytes", encoding="utf-8")

    # Act / Assert — content hash is path-independent.
    assert compute_file_hash(a) == compute_file_hash(b)


def test_one_byte_change_changes_hash(tmp_path: Path):
    # Arrange
    f = tmp_path / "f.txt"
    f.write_text("version one", encoding="utf-8")
    before = compute_file_hash(f)

    # Act
    f.write_text("version two", encoding="utf-8")
    after = compute_file_hash(f)

    # Assert
    assert before != after


# ── IngestionManifest round-trip ───────────────────────────────────────────
def test_record_save_load_roundtrip(tmp_path: Path):
    # Arrange
    path = tmp_path / "ingest_manifest.json"
    manifest = IngestionManifest.load(path)
    manifest.record("abc123", source="downloads/x.pdf", file_name="x.pdf", chunk_count=7)

    # Act
    manifest.save()
    reloaded = IngestionManifest.load(path)

    # Assert
    assert reloaded.is_ingested("abc123")
    assert not reloaded.is_ingested("unknown-hash")
    assert reloaded.entries["abc123"]["chunk_count"] == 7
    assert reloaded.entries["abc123"]["file_name"] == "x.pdf"
    assert "ingested_at" in reloaded.entries["abc123"]


def test_missing_manifest_is_empty(tmp_path: Path):
    manifest = IngestionManifest.load(tmp_path / "does-not-exist.json")
    assert manifest.entries == {}
    assert not manifest.is_ingested("anything")


def test_corrupt_manifest_loads_as_empty(tmp_path: Path):
    # Arrange — write garbage that is not valid JSON.
    path = tmp_path / "ingest_manifest.json"
    path.write_text("{ this is not json", encoding="utf-8")

    # Act — must not raise.
    manifest = IngestionManifest.load(path)

    # Assert
    assert manifest.entries == {}


# ── loader filtering (the heart of the feature) ────────────────────────────
def test_loader_skips_files_already_in_manifest(tmp_path: Path):
    # Arrange — two files; pre-seed the manifest with the hash of the first.
    seen = tmp_path / "seen.txt"
    fresh = tmp_path / "fresh.txt"
    seen.write_text("already ingested content", encoding="utf-8")
    fresh.write_text("brand new content", encoding="utf-8")

    manifest = IngestionManifest(path=tmp_path / "m.json")
    manifest.record(
        compute_file_hash(seen), source=str(seen), file_name="seen.txt", chunk_count=1
    )

    # Act
    docs = load_all_documents([tmp_path], manifest=manifest)

    # Assert — only the fresh file is loaded, and it is tagged with its hash.
    names = {d.metadata["file_name"] for d in docs}
    assert names == {"fresh.txt"}
    assert all(d.metadata.get("file_hash") for d in docs)
    assert docs[0].metadata["file_hash"] == compute_file_hash(fresh)


def test_force_ignores_manifest(tmp_path: Path):
    # Arrange — manifest already knows the file, but force should re-load it.
    f = tmp_path / "f.txt"
    f.write_text("some content", encoding="utf-8")
    manifest = IngestionManifest(path=tmp_path / "m.json")
    manifest.record(compute_file_hash(f), source=str(f), file_name="f.txt", chunk_count=1)

    # Act
    docs = load_all_documents([tmp_path], manifest=manifest, force=True)

    # Assert
    assert len(docs) == 1
    assert docs[0].metadata["file_name"] == "f.txt"


def test_no_manifest_loads_everything_without_hash_tag(tmp_path: Path):
    # Arrange
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    (tmp_path / "b.md").write_text("two", encoding="utf-8")

    # Act — default (manifest=None) must behave exactly as before.
    docs = load_all_documents([tmp_path])

    # Assert
    assert len(docs) == 2
    assert all("file_hash" not in d.metadata for d in docs)
