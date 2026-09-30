# Tracalo

Two free keys, no card:
1. GEMINI_API_KEY  - aistudio.google.com/apikey
2. TAVILY_API_KEY  - app.tavily.com (1,000 searches/month free)

Run:
  pip install -r requirements.txt
  export GEMINI_API_KEY=...
  export TAVILY_API_KEY=tvly-...
  export APP_TOKEN=pick-a-secret
  uvicorn app:app --host 0.0.0.0 --port 8000
Windows PowerShell: $env:NAME="value" instead of export.

Phone (same Wi-Fi): http://<PC-IP>:8000
Render: Build `pip install -r requirements.txt`, Start `uvicorn app:app --host 0.0.0.0 --port $PORT`, env vars as above.

Modes (SEARCH_MODE, auto-picked):
- tavily (default when TAVILY_API_KEY is set): Tavily search, then free Gemini model reads the snippets.
  Results are NOT Google's; use each item's "verify" link to compare.
- grounding: Gemini Google Search grounding. Needs a billing-enabled Google project (free keys get 429/404).

Data lives in each browser's localStorage.
