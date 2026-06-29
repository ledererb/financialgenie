import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import anthropic

# Load .env
env_path = Path("/Users/balazslederer/Desktop/financialgenie/config/.env")
if env_path.exists():
    load_dotenv(env_path)

api_key = os.getenv("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=api_key)

models = [
    "claude-3-5-sonnet-latest",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620",
    "claude-3-haiku-20240307",
    "claude-3-opus-20240229",
    "claude-sonnet-4-20250514",
    "claude-sonnet-4-6"
]

for model in models:
    try:
        print(f"Testing {model}...")
        response = client.messages.create(
            model=model,
            max_tokens=10,
            messages=[{"role": "user", "content": "Hello!"}],
        )
        print(f"  SUCCESS! Response: {response.content[0].text}")
        break
    except Exception as e:
        print(f"  FAILED: {e}")
