import os, re, json, time
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Tried in order; first that answers wins. 2.5-flash has free Google Search grounding
# (500/day) until its shutdown; 3.x grounding needs a billed project (5,000 free/month).
MODELS = [m.strip() for m in os.getenv("GEMINI_MODELS", "gemini-2.5-flash,gemini-3.5-flash-lite").split(",") if m.strip()]
API_KEY = os.getenv("GEMINI_API_KEY", "")
TAVILY_KEY = os.getenv("TAVILY_API_KEY", "")
# "tavily" = Tavily search + free Gemini extraction (no billing needed); "grounding" = Gemini Google Search grounding
MODE = os.getenv("SEARCH_MODE", "tavily" if TAVILY_KEY else "grounding")
EXTRACT_MODELS = [m.strip() for m in os.getenv("EXTRACT_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",") if m.strip()]
APP_TOKEN = os.getenv("APP_TOKEN", "")  # shared secret so strangers can't burn your quota
TTL = 24 * 3600
BASE = os.path.dirname(os.path.abspath(__file__))
URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

app = FastAPI()
_cache = {}  # query -> (timestamp, result); same query => same answer


class Req(BaseModel):
    name: str
    qty: str


def build_query(name: str, qty: str) -> str:
    name, qty = name.strip(), qty.strip()
    if re.fullmatch(r"\d+(\.\d+)?", qty):  # pure number = count ("2" + "eggs")
        return f"protein and calories in {qty} {name}"
    return f"protein and calories in {qty} of {name}"


PROMPT = """Search Google for exactly this query: "__Q__"

Read the top results (featured snippets, nutrition sites). Report total calories (kcal) and total protein (g) for EXACTLY the quantity in the query. If a source gives per-100 g or per-serving values, scale them to the quantity. If sources give a range, report the low and high. Use only numbers stated in the search results, not your own memory. If the results have no usable numbers, reply {"error": "not found"}.

Reply with ONLY this JSON:
{"cal_low": number, "cal_high": number, "pro_low": number, "pro_high": number, "basis": "max 15 words on what the sources said"}
If there is only one value, low = high."""


EXTRACT_PROMPT = """Web search query: "__Q__"

Search results:
__R__

From these results only (not your memory), report total calories (kcal) and total protein (g) for EXACTLY the quantity in the query. If a result gives per-100 g or per-serving values, scale them to the quantity. If results give a range, report the low and high. If the results have no usable numbers, reply {"error": "not found"}.

Reply with ONLY this JSON:
{"cal_low": number, "cal_high": number, "pro_low": number, "pro_high": number, "basis": "max 15 words on what the sources said"}
If there is only one value, low = high."""


def extract_json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def parse_result(text: str, sources: list, q: str, model: str, searched: list, engine: str) -> dict:
    data = extract_json(text)
    if not data or "error" in data:
        raise HTTPException(404, "No usable numbers found in search results")
    try:
        cl, ch = sorted([float(data["cal_low"]), float(data["cal_high"])])
        pl, ph = sorted([float(data["pro_low"]), float(data["pro_high"])])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(502, "Could not read model output")
    if not (0 <= cl and ch <= 6000 and 0 <= pl and ph <= 600):
        raise HTTPException(502, "Implausible values returned")
    return {
        "query": q,
        "cal": round((cl + ch) / 2),
        "pro": round((pl + ph) / 2, 1),
        "cal_range": [round(cl), round(ch)],
        "pro_range": [round(pl, 1), round(ph, 1)],
        "basis": str(data.get("basis", ""))[:140],
        "sources": sources[:5],
        "model": model,
        "engine": engine,
        "searched": searched[:3],  # queries Google actually ran
    }


def call_gemini(model: str, prompt: str, grounded: bool):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0},
    }
    if grounded:
        body["tools"] = [{"google_search": {}}]
    r = httpx.post(URL.format(model), json=body, headers={"x-goog-api-key": API_KEY}, timeout=60)
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        return r.status_code, msg, None
    return 200, "", r.json()


@app.post("/api/lookup")
def lookup(req: Req, x_app_token: Optional[str] = Header(None)):
    if APP_TOKEN and x_app_token != APP_TOKEN:
        raise HTTPException(401, "Bad token")
    if not API_KEY:
        raise HTTPException(500, "GEMINI_API_KEY not set on server")
    name, qty = req.name.strip()[:80], req.qty.strip()[:30]
    if not name or not qty:
        raise HTTPException(400, "name and qty required")
    q = build_query(name, qty)
    hit = _cache.get(q)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]

    out = lookup_tavily(q) if MODE == "tavily" else lookup_grounded(q)
    _cache[q] = (time.time(), out)
    return out


def first_text(data):
    cands = (data or {}).get("candidates") or []
    if not cands:
        return None, {}
    c = cands[0]
    return "".join(p.get("text", "") for p in c.get("content", {}).get("parts", [])), c.get("groundingMetadata") or {}


def lookup_grounded(q: str) -> dict:
    errors = []
    for model in MODELS:
        try:
            code, msg, data = call_gemini(model, PROMPT.replace("__Q__", q), True)
        except httpx.HTTPError as e:
            errors.append(f"{model}: network {e.__class__.__name__}")
            continue
        if code != 200:
            errors.append(f"{model}: {code} {msg[:120]}")
            continue
        text, gm = first_text(data)
        if text is None:
            errors.append(f"{model}: empty response")
            continue
        sources = []
        for ch in gm.get("groundingChunks", []):
            w = ch.get("web") or {}
            if w.get("uri") and all(s["url"] != w["uri"] for s in sources):
                sources.append({"title": w.get("title") or w["uri"], "url": w["uri"]})
        return parse_result(text, sources, q, model, gm.get("webSearchQueries", []), "google")
    raise HTTPException(502, " | ".join(errors) or "no model configured")


def lookup_tavily(q: str) -> dict:
    if not TAVILY_KEY:
        raise HTTPException(500, "TAVILY_API_KEY not set on server")
    try:
        r = httpx.post("https://api.tavily.com/search", json={"query": q, "max_results": 5, "search_depth": "basic"},
                       headers={"Authorization": f"Bearer {TAVILY_KEY}"}, timeout=30)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Tavily network error {e.__class__.__name__}")
    if r.status_code != 200:
        raise HTTPException(502, f"Tavily {r.status_code}: {r.text[:150]}")
    results = r.json().get("results") or []
    if not results:
        raise HTTPException(404, "No search results")
    sources = [{"title": x.get("title") or x["url"], "url": x["url"]} for x in results if x.get("url")]
    ctx = "\n\n".join(f"[{i+1}] {x.get('title','')} ({x.get('url','')})\n{(x.get('content') or '')[:900]}" for i, x in enumerate(results))
    prompt = EXTRACT_PROMPT.replace("__Q__", q).replace("__R__", ctx)
    errors = []
    for model in EXTRACT_MODELS:
        try:
            code, msg, data = call_gemini(model, prompt, False)
        except httpx.HTTPError as e:
            errors.append(f"{model}: network {e.__class__.__name__}")
            continue
        if code != 200:
            errors.append(f"{model}: {code} {msg[:120]}")
            continue
        text, _ = first_text(data)
        if text is None:
            errors.append(f"{model}: empty response")
            continue
        return parse_result(text, sources, q, model, [], "tavily")
    raise HTTPException(502, " | ".join(errors) or "no extract model configured")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
