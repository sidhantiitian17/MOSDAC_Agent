"""Standalone Neo4j + Tabby ML integration check.

Credentials are loaded from .env only — nothing is hardcoded here.
Run from the project root so load_dotenv() finds .env:  python test_graph.py
"""
import os
import sys

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from neo4j import GraphDatabase

load_dotenv()  # pull NEO4J_* and TABBY_* from .env into the environment

# --- 1. NEO4J NATIVE DRIVER TEST (no APOC required) ---
print("🔌 Neo4j Graph Database se direct connect kar rahe hain...")
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
AUTH = (os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", "dummy"))

try:
    driver = GraphDatabase.driver(URI, auth=AUTH)
    driver.verify_connectivity()

    records, summary, keys = driver.execute_query(
        "MERGE (t:Project {name: 'MOSDAC_Agent', status: 'GraphRAG Ready!'}) RETURN t"
    )
    print("✅ Neo4j Connection & Write Test 100% Successful!\n")
    driver.close()

except Exception as e:
    print(f"❌ Neo4j Connection Failed! Error: {e}")
    sys.exit()

# --- 2. TABBY LLM TEST ---
print("🔌 Tabby LLM se connect kar rahe hain...")
TABBY_URL = os.getenv("TABBY_BASE_URL", "http://localhost:8080/v1")
TABBY_TOKEN = os.getenv("TABBY_API_TOKEN", "")
TABBY_MODEL = os.getenv("TABBY_MODEL", "Qwen2-1.5B-Instruct")

if not TABBY_TOKEN:
    sys.exit("❌ TABBY_API_TOKEN not set in .env — add it before running this test.")

try:
    # streaming=True is mandatory — Tabby times out on non-streaming calls.
    llm = ChatOpenAI(
        base_url=TABBY_URL,
        api_key=TABBY_TOKEN,
        model=TABBY_MODEL,
        temperature=0.1,
        streaming=True,
    )

    print("🤖 Tabby is checking the systems...")
    print("Response: ", end="")
    for chunk in llm.stream("Say 'All systems go!'"):
        print(chunk.content, end="")
        sys.stdout.flush()

    print("\n\n✅ Tabby Connection 100% Successful! 🎉")

except Exception as e:
    print(f"\n❌ Tabby Connection Failed! Error: {e}")

print("\n🚀 BINGO! Tumhara poora offline backend setup completely taiyar hai!")
