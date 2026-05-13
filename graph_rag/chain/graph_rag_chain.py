"""LCEL chain: parallel-retrieve (graph + vector) -> format -> LLM -> string output."""
from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnableParallel

from graph_rag.llm.longcat_client import get_llm
from graph_rag.retrieval.hybrid_retriever import HybridRetriever

SYSTEM_PROMPT = """You are an expert assistant with access to a knowledge graph and document database.

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

HUMAN_TEMPLATE = """{history}{question}"""


def build_graph_rag_chain(retriever: HybridRetriever | None = None, llm=None):
    """Construct the LCEL chain. Returns a runnable accepting {'question': str, 'history': str}."""
    retriever = retriever or HybridRetriever()
    llm = llm or get_llm()

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", HUMAN_TEMPLATE),
        ]
    )

    def _retrieve(payload: dict) -> dict:
        # Retrieve using only the current question so history doesn't dilute the embedding
        question = payload["question"]
        ctx = retriever.retrieve(question)
        history = payload.get("history", "")
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
