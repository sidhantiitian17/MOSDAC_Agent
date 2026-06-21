"""Graph RAG Chatbot — CLI entry point.

Commands:
    python main.py ingest                  # incremental ingestion (files + Drupal if URL set)
    python main.py ingest --force          # re-ingest every file, ignoring the content-hash manifest
    python main.py ingest --skip-drupal    # file ingestion only, skip Drupal even if URL is set
    python main.py ingest --skip-files     # Drupal ingestion only, skip file discovery
    python main.py ingest --skip-vector    # KG only, no Chroma writes
    python main.py ingest --skip-graph     # Chroma only, no Neo4j writes
    python main.py chat                    # interactive REPL
    python main.py test                    # health-check every component
    python main.py eval                    # run the legacy Phase-0 harness (cheap, deterministic)
    python main.py ragas-eval              # run the RAGAS production gate (evaluation_plan.md)
    python main.py build-communities       # build GraphRAG community summaries (Phase 6)
"""
from __future__ import annotations

import logging
import sys

# Force UTF-8 output on all platforms (prevents UnicodeEncodeError on Windows
# when stdout is redirected to a file/pipe and locale is cp1252).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("graph_rag.main")


def cmd_ingest(argv: list[str] | None = None) -> int:
    """Run ingestion. Flags: --skip-vector, --skip-graph, --force, --skip-drupal, --skip-files"""
    import os
    from dotenv import load_dotenv
    from graph_rag.ingestion.pipeline import IngestionPipeline

    load_dotenv()

    argv = argv or []
    skip_files = "--skip-files" in argv
    stats = None

    # ── Step 1: file-based ingestion (HTML + PDF) ────────────────────────────
    if not skip_files:
        pipeline = IngestionPipeline(
            skip_vector="--skip-vector" in argv,
            skip_graph="--skip-graph" in argv,
            force="--force" in argv,
        )
        stats = pipeline.run()
        print(stats.summary())
    else:
        print("(File ingestion skipped via --skip-files)")

    # ── Step 2: Drupal ingestion (auto when DRUPAL_JSONAPI_URL is set) ────────
    drupal_url = os.getenv("DRUPAL_JSONAPI_URL", "").strip()
    skip_drupal = "--skip-drupal" in argv

    if drupal_url and not skip_drupal:
        print("\n── Drupal ingestion ──────────────────────────────────────────")
        try:
            from drupal_ingest import DrupalConfig, run as drupal_run
            d_stats = drupal_run(DrupalConfig.from_env())
            print(
                f"Drupal: scanned {d_stats['scanned']} | "
                f"new {d_stats['new']} | updated {d_stats['updated']} | "
                f"skipped {d_stats['skipped']} | errors {d_stats['errors']}"
            )
        except Exception as exc:
            print(f"Drupal ingestion failed: {exc}")
    elif skip_drupal:
        print("\n(Drupal ingestion skipped via --skip-drupal)")
    else:
        print("\n(DRUPAL_JSONAPI_URL not set — Drupal ingestion skipped)")

    return 0 if (stats is None or not stats.errors) else 1


def cmd_chat() -> int:
    from graph_rag.chat.chatbot import GraphRagChatbot

    bot = GraphRagChatbot()
    print("Graph RAG chatbot ready. Type 'exit' or 'quit' to leave, 'reset' to clear history.")
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("Bye.")
            return 0
        if user_input.lower() == "reset":
            bot.reset()
            print("(history cleared)")
            continue
        answer = bot.chat(user_input)
        print(f"\nAssistant: {answer}")


def cmd_test() -> int:
    """Smoke test of every layer; full assertions live in tests/."""
    from graph_rag.config import settings

    ok = True
    print("Checking configuration...")
    print(f"  TABBY_MODEL            = {settings.tabby_model}")
    print(f"  TABBY_BASE_URL         = {settings.tabby_base_url}")
    print(f"  OLLAMA_BASE_URL        = {settings.ollama_base_url}")
    print(f"  OLLAMA_EMBEDDING_MODEL = {settings.ollama_embedding_model}")
    print(f"  NEO4J_URI              = {settings.neo4j_uri}")
    print(f"  CHROMA_PERSIST_DIR     = {settings.chroma_persist_dir}")
    print(f"  DOWNLOADS_DIR          = {settings.downloads_dir}")
    print(f"  ATLASES_DIR            = {settings.atlases_dir}")

    print("\nChecking loader...")
    try:
        from graph_rag.ingestion.loader import load_all_documents

        docs = load_all_documents()
        print(f"  loaded {len(docs)} documents")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        ok = False

    # Shared dependency probes — the SAME code the API /ready endpoint uses (P0-4),
    # so the CLI smoke test and the live readiness check can never drift apart.
    print("\nChecking dependencies (embedder / ChromaDB / Neo4j / LLM)...")
    try:
        from graph_rag.health import readiness

        report = readiness(cache_seconds=0.0, include_llm=True)
        for name, res in report["checks"].items():
            status = "ok" if res["ok"] else "FAILED"
            print(f"  {name:10s} = {status} ({res['detail']})")
            # LLM is a soft dependency for readiness but a hard one for the smoke test.
            if not res["ok"]:
                ok = False
    except Exception as exc:
        print(f"  FAILED: {exc}")
        ok = False

    print("\nChecking entity extractor...")
    try:
        from graph_rag.knowledge_graph.extractor import EntityRelationExtractor

        ex = EntityRelationExtractor()
        triples = ex.extract("Apple acquired Beats Electronics in 2014.")
        print(f"  extracted {len(triples)} triples")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        ok = False

    print("\nChecking LLM...")
    try:
        from graph_rag.llm.tabby_client import get_llm

        llm = get_llm()
        resp = llm.invoke("Reply with just the word OK.")
        print(f"  LLM reply: {getattr(resp, 'content', resp)[:80]}")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        ok = False

    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


def cmd_eval(argv: list[str] | None = None) -> int:
    """Run the evaluation harness. Flags: --limit N, --no-judge, --out PATH, --set PATH"""
    from graph_rag.eval.harness import DEFAULT_QUESTION_SET, EvalHarness

    argv = argv or []
    limit: int | None = None
    out = "eval_results.md"
    qset = DEFAULT_QUESTION_SET
    use_judge = "--no-judge" not in argv
    for i, a in enumerate(argv):
        if a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
        elif a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
        elif a == "--set" and i + 1 < len(argv):
            qset = argv[i + 1]

    harness = EvalHarness(use_judge=use_judge)
    questions = harness.load(qset)
    n = len(questions) if limit is None else min(limit, len(questions))
    print(f"Running eval on {n} question(s) (judge={'on' if use_judge else 'off'})...")
    results = harness.run(questions, limit=limit)
    print("\n" + harness.scorecard(results))
    harness.save_markdown(results, out)
    print(f"\nSaved detailed results to {out}")
    return 0


def cmd_ragas_eval(argv: list[str] | None = None) -> int:
    """Run the RAGAS production gate (evaluation_plan.md).

    Flags:
        --gold PATH     golden dataset file or dir (default tests/eval/golden/v1)
        --config NAME   PROD (default) or RAW (guards flag-only) or BOTH
        --smoke         cheaper metric subset for fast iteration / CI tripwire
        --limit N       evaluate only the first N items
        --out DIR       output directory (default eval_runs)
        --kappa F       judge↔human agreement to feed the gate (else SKIP)

    Requires a configured judge (RAGAS_JUDGE_MODEL, …) and the live pipeline
    (Chroma/Neo4j/Tabby). See §4 of the plan.
    """
    from dotenv import load_dotenv

    from graph_rag.eval.dataset import DEFAULT_GOLDEN_DIR, load_golden
    from graph_rag.eval.ragas_runner import run_gate

    load_dotenv()
    argv = argv or []
    gold = DEFAULT_GOLDEN_DIR
    config = "PROD"
    smoke = "--smoke" in argv
    out = "eval_runs"
    limit: int | None = None
    kappa: float | None = None
    for i, a in enumerate(argv):
        if a == "--gold" and i + 1 < len(argv):
            gold = argv[i + 1]
        elif a == "--config" and i + 1 < len(argv):
            config = argv[i + 1].upper()
        elif a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
        elif a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
        elif a == "--kappa" and i + 1 < len(argv):
            kappa = float(argv[i + 1])

    items = load_golden(gold)
    if limit is not None:
        items = items[:limit]
    configs = ["PROD", "RAW"] if config == "BOTH" else [config]

    overall_go = True
    for cfg in configs:
        print(f"\n=== RAGAS gate: {cfg} config · {len(items)} items · smoke={smoke} ===")
        bundle = run_gate(items, config_name=cfg, smoke=smoke, out_dir=out, judge_kappa=kappa)
        card = bundle.go_scorecard()
        print(card.render())
        if cfg == "PROD":
            overall_go = card.go
    return 0 if overall_go else 1


def cmd_build_communities(argv: list[str] | None = None) -> int:
    """Build GraphRAG community summaries. Flags: --limit N, --min-degree N"""
    from graph_rag.knowledge_graph.community import CommunitySummarizer

    argv = argv or []
    limit: int | None = None
    min_degree: int | None = None
    for i, a in enumerate(argv):
        if a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
        elif a == "--min-degree" and i + 1 < len(argv):
            min_degree = int(argv[i + 1])

    count = CommunitySummarizer().build(limit=limit, min_degree=min_degree)
    print(f"Built {count} community summaries.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "ingest":
        return cmd_ingest(argv[1:])
    if cmd == "chat":
        return cmd_chat()
    if cmd == "test":
        return cmd_test()
    if cmd == "eval":
        return cmd_eval(argv[1:])
    if cmd == "ragas-eval":
        return cmd_ragas_eval(argv[1:])
    if cmd == "build-communities":
        return cmd_build_communities(argv[1:])
    print(f"Unknown command: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
