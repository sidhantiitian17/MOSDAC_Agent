"""Graph RAG Chatbot — CLI entry point.

Commands:
    python main.py ingest     # run full ingestion pipeline (load -> embed -> KG)
    python main.py chat       # interactive REPL
    python main.py test       # health-check every component
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("graph_rag.main")


def cmd_ingest(argv: list[str] | None = None) -> int:
    """Run ingestion. Flags: --skip-vector, --skip-graph"""
    from graph_rag.ingestion.pipeline import IngestionPipeline

    argv = argv or []
    pipeline = IngestionPipeline(
        skip_vector="--skip-vector" in argv,
        skip_graph="--skip-graph" in argv,
    )
    stats = pipeline.run()
    print(stats.summary())
    return 0 if not stats.errors else 1


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
    print(f"  BGE_MODEL_NAME         = {settings.bge_model_name}")
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

    print("\nChecking embedder...")
    try:
        from graph_rag.embeddings.bge_embedder import get_embedder

        emb = get_embedder()
        vec = emb.embed_query("hello world")
        print(f"  embedding dimension = {len(vec)}")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        ok = False

    print("\nChecking ChromaDB...")
    try:
        from graph_rag.vector_store.chroma_store import ChromaStore
        from graph_rag.embeddings.bge_embedder import get_embedder

        store = ChromaStore(embedder=get_embedder())
        print(f"  count = {store.count()}")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        ok = False

    print("\nChecking Neo4j...")
    try:
        from graph_rag.knowledge_graph.neo4j_store import Neo4jStore

        with Neo4jStore() as neo:
            alive = neo.ping()
            print(f"  ping = {alive}")
            if alive:
                print(f"  schema = {neo.schema_report()}")
            else:
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
    print(f"Unknown command: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
