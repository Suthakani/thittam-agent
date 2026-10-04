"""Telegram phone verification (free, real) and Google Sign-In. No extra libraries needed.
Env: TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME, GOOGLE_CLIENT_ID."""
import json, os, secrets, threading, time, urllib.parse, urllib.request
from flask import Blueprint, jsonify, request, session
from auth import clean_profile, db

sp = Blueprint("social", __name__)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN"); BOT = (os.environ.get("TELEGRAM_BOT_USERNAME") or "").lstrip("@"); GCID = os.environ.get("GOOGLE_CLIENT_ID")
PENDING = {}  # start-token -> {phone, tg_id, name, profile, exp}

def tg(method, **p):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", json.dumps(p).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=40))

def handle(m):
    uid = m["from"]["id"]; text = m.get("text") or ""
    if text.startswith("/start "):
        t = text.split(" ", 1)[1].strip(); p = PENDING.get(t)
        if p and p["exp"] > time.time():
            p["tg_id"] = uid
            tg("sendMessage", chat_id=m["chat"]["id"], text="Welcome to Thittam. Tap the button below to share your phone number and finish signing in.",
               reply_markup={"keyboard": [[{"text": "📱 Share my phone number", "request_contact": True}]], "one_time_keyboard": True, "resize_keyboard": True})
        else: tg("sendMessage", chat_id=m["chat"]["id"], text="This link expired. Please start again on the website.")
    elif m.get("contact"):
        c = m["contact"]
        if c.get("user_id") != uid:
            tg("sendMessage", chat_id=m["chat"]["id"], text="Please share your own number using the button."); return
        for p in PENDING.values():
            if p["tg_id"] == uid and p["exp"] > time.time() and not p["phone"]:
                p["phone"] = "+" + c["phone_number"].lstrip("+")
                tg("sendMessage", chat_id=m["chat"]["id"], text="Verified ✔ Go back to the website.", reply_markup={"remove_keyboard": True}); return

def poll():
    off = 0
    while True:
        try:
            for u in tg("getUpdates", offset=off, timeout=25).get("result", []):
                off = u["update_id"] + 1
                if "message" in u: handle(u["message"])
        except Exception as e:
            print("Telegram poll error:", e, flush=True); time.sleep(5)

def start_bot():
    if TOKEN and BOT: threading.Thread(target=poll, daemon=True).start(); print("Telegram bot polling started", flush=True)

def ensure_phone_col(c):
    try: c.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    except Exception: pass

def login_or_create(c, where, val, name, profile, email=None, phone=None):
    ensure_phone_col(c)
    r = c.execute(f"SELECT id FROM users WHERE {where}=?", (val,)).fetchone()
    if r:
        c.execute("UPDATE users SET verified=1 WHERE id=?", (r["id"],)); uid = r["id"]
    else:
        prof = dict(profile or {}); prof["name"] = name
        cur = c.execute("INSERT INTO users(name,email,pw,profile,verified,phone) VALUES(?,?,?,?,1,?)", (name, email, None, json.dumps(prof), phone)); uid = cur.lastrowid
    c.commit(); session.clear(); session["uid"] = uid

@sp.get("/api/config")
def config(): return jsonify(telegram=bool(TOKEN and BOT), google=GCID or None)

@sp.post("/api/tg/start")
def tg_start():
    if not (TOKEN and BOT): return jsonify(error="Telegram login is not set up on this server."), 503
    now = time.time()
    for k in [k for k, v in PENDING.items() if v["exp"] < now]: PENDING.pop(k, None)
    if len(PENDING) > 1000: return jsonify(error="Busy, try again shortly."), 429
    j = request.json or {}; t = secrets.token_urlsafe(16)
    PENDING[t] = {"phone": None, "tg_id": None, "name": (j.get("name") or "").strip()[:60], "profile": clean_profile(j.get("profile")), "exp": now + 600}
    return jsonify(token=t, link=f"https://t.me/{BOT}?start={t}")

@sp.get("/api/tg/status")
def tg_status():
    p = PENDING.get(request.args.get("token", ""))
    if not p or p["exp"] < time.time(): return jsonify(error="Expired. Please start again."), 410
    if not p["phone"]: return jsonify(done=False)
    PENDING.pop(request.args["token"], None)
    with db() as c: login_or_create(c, "phone", p["phone"], p["name"] or "User " + p["phone"][-4:], p["profile"], phone=p["phone"])
    return jsonify(done=True)

@sp.post("/api/google")
def google():
    if not GCID: return jsonify(error="Google sign-in is not set up on this server."), 503
    try:
        info = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote((request.json or {}).get("credential", "")), timeout=15))
    except Exception: return jsonify(error="Google sign-in failed."), 401
    if info.get("aud") != GCID or str(info.get("email_verified")).lower() != "true": return jsonify(error="Google sign-in failed."), 401
    with db() as c: login_or_create(c, "email", info["email"].lower(), info.get("name") or info["email"].split("@")[0], {}, email=info["email"].lower())
    return jsonify(done=True)