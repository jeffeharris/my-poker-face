#!/usr/bin/env python3
"""Test whether .env file needs quotes around the API key."""

import os
from dotenv import load_dotenv

# Load the .env file with override to force using .env values
load_dotenv(override=True)

# Get the API key
api_key = os.getenv("OPENAI_API_KEY")

print(f"API key loaded: {api_key[:20]}...{api_key[-20:]}")
print(f"Length: {len(api_key)}")
print(f"Starts with quotes: {api_key.startswith(chr(34))}")
print(f"Ends with quotes: {api_key.endswith(chr(34))}")

# Test with OpenAI
try:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    # Try a simple completion
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Say 'test'"}],
        max_tokens=5
    )
    print("\n✅ API key works! Response:", response.choices[0].message.content)
except Exception as e:
    print(f"\n❌ API key failed: {e}")