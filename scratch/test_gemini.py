import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
model = "gemini-2.5-flash"

async def test_gemini():
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "Hello, answer in JSON: {\"reply\": \"hi\"}"}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.0
        }
    }
    async with httpx.AsyncClient() as client:
        try:
            print(f"Calling Gemini URL: https://generativelanguage.googleapis.com/... (key length: {len(GEMINI_API_KEY)})")
            response = await client.post(url, json=payload, timeout=8.0)
            print("Status Code:", response.status_code)
            print("Response:", response.text)
        except Exception as e:
            print("Error occurred:", str(e))

asyncio.run(test_gemini())
