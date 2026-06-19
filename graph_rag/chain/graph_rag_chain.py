"""LCEL chain: parallel-retrieve (graph + vector) -> format -> LLM -> string output."""
from __future__ import annotations

from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnableParallel

from graph_rag.config import settings
from graph_rag.llm.tabby_client import get_llm
from graph_rag.retrieval.hybrid_retriever import HybridRetriever

_DEFAULT_SYSTEM_PROMPT = """You are an expert assistant with access to a knowledge graph and document database.

Use the provided context to answer the user's question accurately and concisely.

KNOWLEDGE GRAPH (entity relationships extracted from source documents):
{graph_context}

DOCUMENT PASSAGES (semantically relevant text excerpts):
{vector_context}

Rules:
- Only use facts grounded in the context above.
- Cite the [Source: ...] when stating specific facts from passages.
- If the answer is not in the context, say "I don't have enough information to answer that."
- Prefer relationship-based reasoning when graph paths are present.
"""


def _load_system_prompt() -> str:
    """Load system prompt from file. Falls back to default if file not found."""
    path = Path(settings.system_prompt_path)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return _DEFAULT_SYSTEM_PROMPT

HUMAN_TEMPLATE = """{history}{question}"""


def build_graph_rag_chain(retriever: HybridRetriever | None = None, llm=None, contextualizer=None):
    """Construct the LCEL chain. Returns a runnable accepting {'question': str, 'history': str}."""
    retriever = retriever or HybridRetriever()
    llm = llm or get_llm()
    if contextualizer is None:
        from graph_rag.retrieval.query_contextualizer import QueryContextualizer

        contextualizer = QueryContextualizer()

    system_text = _load_system_prompt()

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_text),
            ("human", HUMAN_TEMPLATE),
        ]
    )

    def _retrieve(payload: dict) -> dict:
        question = payload["question"]
        history = payload.get("history", "")
        # History-aware retrieval: rewrite a follow-up into a standalone query so
        # retrieval targets the right entity. The user's literal question (below)
        # is unchanged — only the search query is contextualized.
        search_query = contextualizer.contextualize(question, history).search_query
        ctx = retriever.retrieve(search_query)
        return {
            "graph_context": ctx["graph_context"],
            "vector_context": ctx["vector_context"],
            "question": question,
            "history": f"{history}\n\n" if history else "",
        }

    chain = (
        RunnableParallel({"retrieved": RunnableLambda(_retrieve)})
        | RunnableLambda(lambda x: x["retrieved"])
        | prompt
        | llm
        | StrOutputParser()
    )
    return chain
