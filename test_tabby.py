"""Standalone Tabby ML connectivity check.

Credentials are loaded from .env only — nothing is hardcoded here.
Run from the project root so load_dotenv() finds .env:  python test_tabby.py
"""
import os
import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

load_dotenv()  # pull TABBY_* from .env into the environment

TABBY_URL = os.getenv("TABBY_BASE_URL", "http://localhost:8080/v1")
TABBY_TOKEN = os.getenv("TABBY_API_TOKEN", "")
TABBY_MODEL = os.getenv("TABBY_MODEL", "Qwen2-1.5B-Instruct")

if not TABBY_TOKEN:
    sys.exit("❌ TABBY_API_TOKEN not set in .env — add it before running this test.")

print("⏳ Tabby ML se connection establish kar rahe hain (Streaming Mode)...")

try:
    # streaming=True is mandatory — Tabby times out on non-streaming calls.
    local_llm = ChatOpenAI(
        base_url=TABBY_URL,
        api_key=TABBY_TOKEN,
        model=TABBY_MODEL,
        temperature=0.1,
        streaming=True,
    )

    messages = [HumanMessage(content="Explain what a Knowledge Graph is in one sentence.")]

    print("🤖 Prompt sent! Tabby is typing...\n")

    print("Response: ", end="")
    for chunk in local_llm.stream(messages):
        print(chunk.content, end="")
        sys.stdout.flush()

    print("\n\n✅ Connection 100% Successful! Pipeline Ready! 🎉")

except Exception as e:
    print("\n❌ Connection Failed! Error details:")
    print(e)
