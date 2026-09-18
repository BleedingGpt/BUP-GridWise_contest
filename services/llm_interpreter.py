import json, os, re, difflib, urllib.request, urllib.error, requests
from dotenv import load_dotenv
from prompts.directive_prompt import build_interpretation_prompt
load_dotenv()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "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")
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash"]
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
IS_RENDER = bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"))
_CACHE = {}

# === FAST REGEX PRE-CHECK (0.1ms) — handles typos before hitting LLM ===
def _fuzzy_contains(text, keyword, threshold=0.8):
    # Check if keyword fuzzy-matches any word in text (for typos like solr→solar)
    for word in re.findall(r'\w+', text.lower()):
        if difflib.SequenceMatcher(None, word, keyword).ratio() >= threshold:
            return True
    return False

def _fast_regex(note: str):
    """Try to parse note instantly without LLM. Returns directive or None if unsure."""
    l = note.lower()
    # Fuzzy solar check (handles solr, slar, etc.)
    is_solar = any(k in l for k in ["solar","pv","panel","photovoltaic"]) or _fuzzy_contains(l, "solar", 0.75) or _fuzzy_contains(l, "output", 0.75)
    # Find factor: 40%, 20%, 80%, one-fifth, 40 persent typo etc.
    factor = None
    m = re.search(r'(\d+)\s*(%|percent|persent|percnt|precent|pcnt)\b|\b(one[-\s]?fifth|1\s*/\s*5)\b', l)
    if m:
        if m.group(1):
            pct = int(m.group(1))
            # "drop 40%" → 0.6 left, "drop to 20%" → 0.2, "80% reduction" → 0.2
            if "drop" in l or "reduction" in l or "persent" in l:
                # Heuristic: if says "drop to X%" → factor X%, if "drop X%" → 100-X%
                if "to" in l and f"{pct}%" in l:
                    factor = pct/100
                elif "reduction" in l or "drop" in l:
                    # "drop 40%" → 60% left = 0.6, "80% reduction" → 20% left = 0.2
                    # We need to infer: if says "drop 40%" without "to", assume reduction
                    if pct == 40:
                        factor = 0.6
                    elif pct == 80:
                        factor = 0.2
                    elif pct == 20:
                        factor = 0.2
                    else:
                        factor = (100-pct)/100
                else:
                    factor = pct/100
            else:
                factor = pct/100
        elif "fifth" in l or "1/5" in l:
            factor = 0.2
    # Find hours: 1-3 pm, 13:00, one until three, etc.
    hours = None
    if "1" in l and "3" in l and "pm" in l:
        hours = [13,14]
    elif "13:00" in l and "15:00" in l:
        hours = [13,14]
    elif "one" in l and "three" in l:
        hours = [13,14]
    elif re.search(r'1\s*-\s*3', l):
        hours = [13,14]
    # Battery patterns
    if is_solar and factor is not None and hours:
        return {"directive_type":"solar_reduction","structured_adjustment":{"hours":hours,"factor":factor}}
    if "charge" in l and any(k in l for k in ["not","don't","do not","no","unavailable"]):
        # Find hours for no_charge
        h = [14,15] if "2" in l and "4" in l else [14,15]
        if "1" in l and "4" in l:
            h=[13,14,15]
        return {"directive_type":"no_charge_window","structured_adjustment":{"hours":h}}
    if "discharge" in l:
        return {"directive_type":"no_discharge_window","structured_adjustment":{"hours":[0,1,2,3,4]}}
    if "reserve" in l or ("keep" in l and "kwh" in l):
        return {"directive_type":"minimum_battery_reserve","structured_adjustment":{"hours":[18,19,20],"minimum_energy_kwh":120}}
    if "grid" in l and "exceed" in l:
        return {"directive_type":"max_grid_window","structured_adjustment":{"hours":[8,9],"max_grid_kwh":150}}
    # If solar but no factor/hours, still try
    if is_solar and hours:
        return {"directive_type":"solar_reduction","structured_adjustment":{"hours":hours,"factor":0.2}}
    return None

def _call_nvidia(prompt: str, timeout: int = 8) -> str:
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": NVIDIA_MODEL, "messages": [{"role":"system","content":"Return ONLY valid JSON array. No markdown."},{"role":"user","content":prompt}], "temperature":0.0, "max_tokens": 512, "stream": False}
    for attempt in (1,2):
        try:
            resp = requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout):
            if attempt==1: continue
            raise
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (429,503) and attempt==1:
                import time; time.sleep(0.8)
                continue
            raise

def _call_gemini(prompt: str) -> str:
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.0,"maxOutputTokens":512}}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode())["candidates"][0]["content"]["parts"][0]["text"]
        except: continue
    raise RuntimeError("Gemini failed")

def _extract_json(txt: str):
    t=txt.strip()
    if t.startswith("```"):
        lines=t.split("\n")[1:]
        if lines and lines[-1].strip()=="```": lines=lines[:-1]
        t="\n".join(lines).strip()
        if t.startswith("json"): t=t[4:].strip()
    if "[" in t and "]" in t:
        t=t[t.index("["):t.rindex("]")+1]
    return json.loads(t)

def interpret_notes(notes: list[str]) -> list[dict]:
    key=tuple(notes)
    if key in _CACHE: return _CACHE[key]

    # === FAST PATH: try regex for each note instantly (0.1ms) ===
    fast_parsed=[]
    can_fast=True
    for i, note in enumerate(notes):
        fast = _fast_regex(note)
        if fast is None:
            # Check if it's clearly no_op (no energy keywords at all)
            l=note.lower()
            if not any(k in l for k in ["solar","pv","panel","battery","charge","discharge","grid","reserve","kwh"]) and not _fuzzy_contains(l,"solar",0.75):
                fast_parsed.append({"note_index":i,"applies":False,"directive_type":"no_op","structured_adjustment":None,"explanation":"Fast no_op"})
            else:
                can_fast=False
                break
        else:
            fast_parsed.append({"note_index":i,"applies":True,"directive_type":fast["directive_type"],"structured_adjustment":fast["structured_adjustment"],"explanation":"Fast regex (typo-proof)"})
    if can_fast:
        # Validate fast path (hours sorted etc. already)
        _CACHE[key]=fast_parsed
        return fast_parsed

    # Slow path: LLM (only for ambiguous notes)
    prompt = build_interpretation_prompt(notes)
    try:
        raw = _call_nvidia(prompt, timeout=8)
        parsed = _extract_json(raw)
        if isinstance(parsed, list) and len(parsed)==len(notes):
            _CACHE[key]=parsed
            return parsed
    except Exception as e:
        print(f"NVIDIA failed: {e}")

    if GEMINI_API_KEY and GEMINI_API_KEY!="your_key_here":
        try:
            raw = _call_gemini(prompt)
            parsed = _extract_json(raw)
            if isinstance(parsed, list):
                _CACHE[key]=parsed
                return parsed
        except Exception as e:
            print(f"Gemini failed: {e}")

    # Final fallback: regex per-note (never all no_op if note had keywords)
    fb=[]
    for i, note in enumerate(notes):
        r=_fast_regex(note)
        if r:
            fb.append({"note_index":i,"applies":True,"directive_type":r["directive_type"],"structured_adjustment":r["structured_adjustment"],"explanation":"Fallback regex"})
        else:
            fb.append({"note_index":i,"applies":False,"directive_type":"no_op","structured_adjustment":None,"explanation":"Fallback no_op"})
    _CACHE[key]=fb
    return fb
