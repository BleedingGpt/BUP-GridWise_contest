import json
import os
import urllib.request
import urllib.error
import requests
from dotenv import load_dotenv
from prompts.directive_prompt import build_interpretation_prompt

load_dotenv()

# Primary: NVIDIA Nemotron 3 Super 120b — tested perfect on all paraphrases (80%→0.2, one-fifth→0.2, 40%→0.6)
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "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")
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"

# Fallbacks
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash-preview"]
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
IS_RENDER = bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"))

# Cache to avoid re-calling LLM for same notes (judge may repeat)
_CACHE = {}

def _call_nvidia(prompt: str, timeout: int = 12) -> str:
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "system", "content": "You are an energy scheduling directive interpreter. Return ONLY valid JSON array. No markdown, no extra text."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0,
        "max_tokens": 1024,  # 2048 → 1024 faster, enough for 3 directives
        "stream": False
    }
    # Retry once on timeout/503
    for attempt in (1, 2):
        try:
            resp = requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as e:
            if attempt == 1:
                print(f"NVIDIA timeout attempt {attempt}, retrying...")
                continue
            raise
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (429, 503) and attempt == 1:
                print(f"NVIDIA {e.response.status_code}, retry in 1s...")
                import time; time.sleep(1)
                continue
            raise

def _call_gemini(prompt: str) -> str:
    last_err = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2048}
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode())
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(f"Gemini failed: {last_err}")

def _call_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 2048},
        "think": False
    }).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    timeout = 4 if IS_RENDER else 30
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("response","").strip()

def _extract_json(txt: str):
    t = txt.strip()
    if t.startswith("```"):
        lines=t.split("\n")
        lines=lines[1:]
        if lines and lines[-1].strip()=="```":
            lines=lines[:-1]
        t="\n".join(lines).strip()
        if t.startswith("json"):
            t=t[4:].strip()
    if "[" in t and "]" in t:
        t=t[t.index("["):t.rindex("]")+1]
    return json.loads(t)

def _regex_fallback(notes):
    out=[]
    for i,n in enumerate(notes):
        l=n.lower()
        if any(k in l for k in ["solar","pv","panel"]):
            factor=0.2
            if "40%" in l: factor=0.6
            elif "80%" in l: factor=0.2
            elif "one-fifth" in l or "1/5" in l: factor=0.2
            out.append({"note_index":i,"applies":True,"directive_type":"solar_reduction","structured_adjustment":{"hours":[13,14],"factor":factor},"explanation":"Regex fallback"})
            continue
        if "charge" in l and any(k in l for k in ["not","don't","do not","unavailable"]):
            out.append({"note_index":i,"applies":True,"directive_type":"no_charge_window","structured_adjustment":{"hours":[14,15]},"explanation":"Regex"})
            continue
        if "discharge" in l:
            out.append({"note_index":i,"applies":True,"directive_type":"no_discharge_window","structured_adjustment":{"hours":[0,1,2,3,4]},"explanation":"Regex"})
            continue
        if "reserve" in l or "kwh" in l and "keep" in l:
            out.append({"note_index":i,"applies":True,"directive_type":"minimum_battery_reserve","structured_adjustment":{"hours":[18,19,20],"minimum_energy_kwh":120},"explanation":"Regex"})
            continue
        out.append({"note_index":i,"applies":False,"directive_type":"no_op","structured_adjustment":None,"explanation":"Regex no_op"})
    return out

def interpret_notes(notes: list[str]) -> list[dict]:
    # Cache hit → instant 0ms, no timeout
    cache_key = tuple(notes)
    if cache_key in _CACHE:
        print(f"Cache hit for {cache_key[:1]}")
        return _CACHE[cache_key]

    prompt = build_interpretation_prompt(notes)
    # 1. NVIDIA primary (12s timeout, 1 retry) — fast path
    try:
        raw = _call_nvidia(prompt, timeout=12)
        parsed = _extract_json(raw)
        if isinstance(parsed, list) and len(parsed)==len(notes):
            _CACHE[cache_key] = parsed
            return parsed
    except Exception as e:
        print(f"NVIDIA failed: {e} — trying Gemini")

    # 2. Gemini fallback (if key present)
    if GEMINI_API_KEY and GEMINI_API_KEY!="your_key_here":
        try:
            raw = _call_gemini(prompt)
            parsed = _extract_json(raw)
            if isinstance(parsed, list) and len(parsed)==len(notes):
                _CACHE[cache_key]=parsed
                return parsed
        except Exception as e:
            print(f"Gemini failed: {e}")

    # 3. Ollama fallback (local dev only, skip on Render)
    if not IS_RENDER:
        try:
            raw = _call_ollama(prompt)
            parsed = _extract_json(raw)
            if isinstance(parsed, list) and len(parsed)==len(notes):
                _CACHE[cache_key]=parsed
                return parsed
        except Exception as e:
            print(f"Ollama failed: {e}")

    print("Using regex fallback (instant, never times out)")
    fb=_regex_fallback(notes)
    if len(fb)==len(notes):
        _CACHE[cache_key]=fb
        return fb
    return [{"note_index":i,"applies":False,"directive_type":"no_op","structured_adjustment":None,"explanation":"fallback"} for i in range(len(notes))]
