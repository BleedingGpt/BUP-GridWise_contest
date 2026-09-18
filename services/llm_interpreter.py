import json
import os
import urllib.request
import urllib.error
from dotenv import load_dotenv
from prompts.directive_prompt import build_interpretation_prompt

load_dotenv()

# gpt-oss:120b-cloud — user requested beast mode (cloud = 0 RAM, 65GB local would nuke rig but cloud is free)
# 120b = max reasoning, structured outputs, agentic — most optimized for winning
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud")  # 120b cloud
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

def _call_ollama(prompt: str) -> str:
    """Call Ollama gpt-oss (cloud) with low reasoning for speed."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 2048},
        # gpt-oss: use low reasoning effort for hackathon latency (<1s vs 3s high)
        "think": False,
        "raw": False
    }).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read().decode())
        return data.get("response", "").strip()

def _call_gemini_rest(prompt: str) -> str:
    """Fallback: Gemini via REST."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2048}
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
        return data["candidates"][0]["content"]["parts"][0]["text"]

def _extract_json(raw_text: str) -> list[dict]:
    """Strip fences and parse JSON array."""
    txt = raw_text.strip()
    if txt.startswith("```"):
        lines = txt.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        txt = "\n".join(lines).strip()
        if txt.startswith("json"):
            txt = txt[4:].strip()
    if "[" in txt and "]" in txt:
        start = txt.index("[")
        end = txt.rindex("]") + 1
        txt = txt[start:end]
    return json.loads(txt)

def interpret_notes(notes: list[str]) -> list[dict]:
    """Send operator notes to gpt-oss:120b-cloud (primary) then Gemini fallback."""
    prompt = build_interpretation_prompt(notes)

    # Try Ollama gpt-oss first (cloud, no quota, optimized)
    try:
        raw_text = _call_ollama(prompt)
        parsed = _extract_json(raw_text)
        if isinstance(parsed, list) and len(parsed) == len(notes):
            return parsed
        raise ValueError(f"Wrong length {len(parsed)} vs {len(notes)} raw={raw_text[:200]}")
    except Exception as e:
        print(f"Ollama {OLLAMA_MODEL} failed: {e} — trying Gemini fallback")

    if GEMINI_API_KEY and GEMINI_API_KEY != "your_key_here":
        try:
            raw_text = _call_gemini_rest(prompt)
            parsed = _extract_json(raw_text)
            if isinstance(parsed, list):
                return parsed
        except Exception as e:
            print(f"Gemini fallback failed: {e}")

    print("Both LLMs failed — safe fallback no_op")
    return [
        {
            "note_index": i,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "LLM unavailable, safe fallback",
        }
        for i in range(len(notes))
    ]
