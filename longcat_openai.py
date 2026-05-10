import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.getenv("LONGCAT_API_KEY")
model = os.getenv("LONGCAT_MODEL", "LongCat-Flash-Chat")

if not api_key:
    raise ValueError("LONGCAT_API_KEY is not set in your .env file.")

client = OpenAI(
    api_key=api_key,
    base_url="https://api.longcat.chat/openai"
)


def chat(user_message: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": user_message}
        ],
        max_tokens=1000,
        temperature=0.7,
    )
    return response.choices[0].message.content


def chat_with_retry(message: str, retries: int = 3) -> str:
    import time
    for attempt in range(retries):
        try:
            return chat(message)
        except Exception as e:
            if "429" in str(e) and attempt < retries - 1:
                wait = 2 ** attempt
                print(f"Rate limited. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


if __name__ == "__main__":
    reply = chat("Hello, please introduce yourself.")
    print(reply)
