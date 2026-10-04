"""Thittam: multilingual, multi-country welfare-scheme agent (Gemini).
Curated country packs (data/<ISO>/*.json) use deterministic rules.
Countries without a pack fall back to cited web research, clearly labelled unverified."""
import glob, json, os
from flask import Flask, jsonify, redirect, request, send_from_directory
from google import genai
from google.genai import types

BASE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder="static")
from auth import bp, current_user, secret
app.secret_key = secret()
app.register_blueprint(bp)
from social import sp, start_bot
app.register_blueprint(sp)
start_bot()
client = genai.Client()  # reads GEMINI_API_KEY (free key from aistudio.google.com)
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

PACKS = {}
for f in sorted(glob.glob(os.path.join(BASE, "data", "*", "*.json"))):
    PACKS.setdefault(os.path.basename(os.path.dirname(f)), []).extend(json.load(open(f, encoding="utf-8")))

def check(s, p):
    r, miss = s["rules"], []
    def t(key, ok):
        if p.get(key) is None: miss.append(key); return True
        return ok()
    tests = [("gender", lambda: p["gender"] == r.get("gender")), ("age", lambda: p["age"] >= r.get("min_age", 0)),
             ("age", lambda: p["age"] <= r.get("max_age", 200)), ("occupation", lambda: p["occupation"] in r.get("occupations", [p["occupation"]])),
             ("income_lakh", lambda: p["income_lakh"] <= r.get("max_income_lakh", 1e9)), ("income", lambda: p["income"] <= r.get("max_income", 1e18)), ("govt_school", lambda: p["govt_school"] == r.get("govt_school"))]
    need = {"gender": "gender", "age": "min_age" in r or "max_age" in r, "occupation": "occupations", "income_lakh": "max_income_lakh", "income": "max_income", "govt_school": "govt_school"}
    for key, fn in tests:
        rule = need[key]
        if (rule is True) or (isinstance(rule, str) and rule in r):
            if key == "age" and ("min_age" not in r and "max_age" not in r): continue
            if not t(key, fn): return None
    return sorted(set(miss))

def find_schemes(country, profile, region=None):
    out = []
    for s in PACKS.get(country, []):
        if region and s.get("state") not in (region, "India (all states)") and s.get("state"): continue
        m = check(s, profile)
        if m is not None: out.append({**s, "status": "eligible" if not m else "possible", "needs_info": m})
    return out

PROFILE_SCHEMA = """{"profile":{"age":int|null,"gender":"male"|"female"|null,"occupation":"student"|"farmer"|"business"|"employed"|"homemaker"|"unemployed"|null,"income_lakh":number|null (annual family income in lakh INR, India only),"income":number|null (annual household income in local currency, non-India),"govt_school":true|false|null,"region":string|null},"question":string|null,"reply":string}"""

def contents(msgs):
    return [types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part(text=m["content"])]) for m in msgs]

def curated_system(country, lang):
    return (f"You help people discover government schemes in {country}. Reply in {lang} unless the user writes in another language. "
            "Extract the user's profile from the WHOLE conversation. Never guess unknown fields (use null). "
            "If age, occupation or income is still unknown, put ONE short follow-up question in 'question', else null. 'reply' is one warm sentence. "
            "Return ONLY JSON in exactly this shape: " + PROFILE_SCHEMA)

def research_system(country, lang):
    return (f"You help people discover government benefits in {country}. Reply in {lang}. No curated data exists, so use Google Search and rely ONLY on official government sources. "
            "Give the source URL for every claim, ask ONE short question if key info is missing, never invent amounts, and state clearly that this is AI-researched, not verified; confirm on the official site.")

_L = {}
def localize(items, lang):
    """Translate scheme card text into the user's language (cached)."""
    if lang.strip().lower() == "english" or not items: return items
    keys = ("benefit", "conditions", "documents", "apply")
    need = [s for s in items if (s["id"], lang) not in _L]
    if need:
        try:
            r = client.models.generate_content(model=MODEL, contents=json.dumps({s["id"]: {k: s[k] for k in keys} for s in need}, ensure_ascii=False),
                config=types.GenerateContentConfig(system_instruction=f"Translate the string values of this JSON into {lang}. Keep the exact JSON structure and keys. Keep URLs, numbers, scheme names and acronyms unchanged. Return only JSON.", response_mime_type="application/json"))
            for k, v in json.loads(r.text).items(): _L[(k, lang)] = {x: v[x] for x in keys if x in v}
        except Exception as e: print("translate error:", e, flush=True)
    return [{**s, **_L.get((s["id"], lang), {})} for s in items]

@app.post("/api/chat")
def chat():
    u = current_user()
    if not u: return jsonify(reply="Please log in first.", schemes=[], curated=True), 401
    known = u["profile"]
    j = request.json; country = (j.get("country") or "IN").upper(); lang = j.get("lang") or "English"
    msgs = [{"role": m["role"], "content": m["content"]} for m in j["messages"]]
    try:
        if country not in PACKS:
            r = client.models.generate_content(model=MODEL, contents=contents(msgs), config=types.GenerateContentConfig(
                system_instruction=research_system(country, lang), tools=[types.Tool(google_search=types.GoogleSearch())]))
            return jsonify(reply=r.text or "No answer found.", schemes=[], curated=False)
        r = client.models.generate_content(model=MODEL, contents=contents(msgs), config=types.GenerateContentConfig(
            system_instruction=curated_system(country, lang) + " Known profile from the user's account (trust it, do not ask again): " + json.dumps(known), response_mime_type="application/json"))
        data = json.loads(r.text); prof = {**known, **{k: v for k, v in (data.get("profile") or {}).items() if v is not None}}
        found = find_schemes(country, prof, prof.get("region"))
        shown = localize([s for s in found if s["status"] == "eligible"], lang)
        reply = (data.get("reply") or "") + ("\n" + data["question"] if data.get("question") else "")
        if not shown and not data.get("question"): reply += "\nNo matching scheme found in our data yet."
        return jsonify(reply=reply, schemes=shown, curated=True, profile=prof)
    except Exception as e:
        return jsonify(reply=f"Server error: {e}", schemes=[], curated=True), 500

@app.get("/sw.js")
def service_worker():
    r = send_from_directory("static", "sw.js"); r.headers["Service-Worker-Allowed"] = "/"; r.headers["Cache-Control"] = "no-cache"; return r

@app.get("/api/countries")
def countries(): return jsonify(sorted(PACKS))

def page(name, need_login):
    u = current_user()
    if need_login and not u: return redirect("/login")
    if not need_login and u: return redirect("/")
    return send_from_directory("static", name)

@app.get("/")
def index(): return page("index.html", True)

@app.get("/login")
def login_page(): return page("login.html", False)

@app.get("/profile")
def profile_page(): return page("profile.html", True)

if __name__ == "__main__": app.run(port=int(os.environ.get("PORT", 5000)), debug=False)
