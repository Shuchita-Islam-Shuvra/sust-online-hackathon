import os
from dotenv import load_dotenv

# Load env file if it exists
load_dotenv()

PORT = int(os.environ.get("PORT", 8000))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# Model names
GROQ_MODEL_TIER1 = os.environ.get("GROQ_MODEL_TIER1", "llama-3.1-8b-instant")
GEMINI_MODEL_TIER2 = os.environ.get("GEMINI_MODEL_TIER2", "gemini-2.5-flash") # Direct Gemini API model
GROQ_MODEL_TIER3 = os.environ.get("GROQ_MODEL_TIER3", "llama-3.3-70b-versatile")
