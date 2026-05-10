# LongCat LLM Setup Guide

## Prerequisites

- Python 3.8+
- `pip` package manager

---

## 1. Install Dependencies

Install the required packages based on your preferred API format:

```bash
# For OpenAI-compatible usage
pip install openai python-dotenv

# For Anthropic-compatible usage
pip install anthropic python-dotenv

# Or install all at once
pip install openai anthropic python-dotenv
```text

---

## 2. Create the `.env` File

In the root of your project directory, create a file named `.env` and add your LongCat API key:

```env
# .env

LONGCAT_API_KEY=your_api_key_here

# Optional: set a default model
LONGCAT_MODEL=LongCat-Flash-Chat

# Optional: set API format preference (openai or anthropic)
LONGCAT_API_FORMAT=openai
```

> **Keep your `.env` file private.** Never commit it to version control.

Add `.env` to your `.gitignore`:

```bash
echo ".env" >> .gitignore
```

---

## 3. Load the LongCat LLM

### Option A — Using the OpenAI SDK

```python
# longcat_openai.py

import os
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables from .env
load_dotenv()

api_key = os.getenv("LONGCAT_API_KEY")
model   = os.getenv("LONGCAT_MODEL", "LongCat-Flash-Chat")

if not api_key:
    raise ValueError("LONGCAT_API_KEY is not set in your .env file.")

# Initialize client pointed at LongCat's OpenAI-compatible endpoint
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

if __name__ == "__main__":
    reply = chat("Hello, please introduce yourself.")
    print(reply)
```

### Option B — Using the Anthropic SDK

```python
# longcat_anthropic.py

import os
from dotenv import load_dotenv
from anthropic import Anthropic

# Load environment variables from .env
load_dotenv()

api_key = os.getenv("LONGCAT_API_KEY")
model   = os.getenv("LONGCAT_MODEL", "LongCat-Flash-Chat")

if not api_key:
    raise ValueError("LONGCAT_API_KEY is not set in your .env file.")

# Initialize client pointed at LongCat's Anthropic-compatible endpoint
client = Anthropic(
    api_key=f"Bearer {api_key}",
    base_url="https://api.longcat.chat/anthropic/",
    default_headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
)

def chat(user_message: str) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=1000,
        messages=[
            {"role": "user", "content": user_message}
        ]
    )
    return response.content[0].text

if __name__ == "__main__":
    reply = chat("Hello, please introduce yourself.")
    print(reply)
```

---

## 4. Project Structure

Your project should look like this:

```text
your-project/
├── .env                  # API key and config (never commit this)
├── .gitignore            # Should include .env
├── longcat_openai.py     # OpenAI-style client (Option A)
├── longcat_anthropic.py  # Anthropic-style client (Option B)
└── requirements.txt
```

**`requirements.txt`:**

```txt
openai
anthropic
python-dotenv
```

---

## 5. Environment Variables Reference

| Variable            | Required | Default               | Description                                      |
|---------------------|----------|-----------------------|--------------------------------------------------|
| `LONGCAT_API_KEY`   | Yes      | —                     | Your LongCat API key                             |
| `LONGCAT_MODEL`     | No       | `LongCat-Flash-Chat`  | Model name to use                                |
| `LONGCAT_API_FORMAT`| No       | `openai`              | API format preference (`openai` or `anthropic`)  |

### Available Models

| Model                         | API Format         | Notes                          |
|-------------------------------|--------------------|----------------------------------| #ignore
| `LongCat-Flash-Chat`          | OpenAI / Anthropic | General-purpose chat           |
| `LongCat-Flash-Thinking`      | OpenAI / Anthropic | Deep-thinking                  |
| `LongCat-Flash-Thinking-2601` | OpenAI / Anthropic | Upgraded deep-thinking         |
| `LongCat-Flash-Lite`          | OpenAI / Anthropic | Efficient MoE model            |
| `LongCat-2.0-Preview`         | OpenAI / Anthropic | High-performance agentic       |
| `LongCat-Flash-Omni-2603`     | OpenAI only        | Multimodal (text/audio/video)  |

---

## 6. Quota & Rate Limits

- **Free daily quota:** 500,000 tokens/day for most models; 50,000,000 tokens/day for `LongCat-Flash-Lite`.
- Quota resets at **midnight Beijing Time (UTC+8)** — unused quota does not roll over.
- If you hit the rate limit, the API returns HTTP **429**. Implement exponential backoff:

```python
import time

def chat_with_retry(message: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            return chat(message)
        except Exception as e:
            if "429" in str(e) and attempt < retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s ...
                print(f"Rate limited. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
```

---

## 7. Run & Test

```bash
# Test with OpenAI-compatible client
python longcat_openai.py

# Test with Anthropic-compatible client
python longcat_anthropic.py
```

A successful run prints the model's reply directly to the console.

---

## Troubleshooting

| Error                        | Likely Cause                              | Fix                                          |
|------------------------------|-------------------------------------------|----------------------------------------------|
| `LONGCAT_API_KEY is not set` | `.env` file missing or key not defined    | Create `.env` and add your key               |
| HTTP 401 Unauthorized        | API key is invalid or malformed           | Double-check the key in your `.env`          |
| HTTP 429 Too Many Requests   | Daily quota exceeded or rate limit hit    | Wait and retry; request a quota increase     |
| HTTP 500 / 502               | Upstream server issue                     | Retry after a short delay                    |
| `ModuleNotFoundError`        | Dependencies not installed                | Run `pip install -r requirements.txt`        |
