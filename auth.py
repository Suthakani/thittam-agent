"""Accounts with email OTP verification. Passwords and OTPs are stored hashed.
Email: set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS (and MAIL_FROM). Without SMTP the code is printed in the server console (dev mode)."""
import json, os, re, secrets, smtplib, sqlite3, time
from email.message import EmailMessage
from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__)); DB = os.path.join(BASE, "thittam.db")
bp = Blueprint("auth", __name__)
PROFILE_KEYS = {"name", "age", "gender", "occupation", "income_lakh", "income", "govt_school", "region"}

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, pw TEXT, profile TEXT DEFAULT '{}', verified INTEGER DEFAULT 0, otp TEXT, otp_exp REAL, otp_tries INTEGER DEFAULT 0)")
    for col in ("verified INTEGER DEFAULT 0", "otp TEXT", "otp_exp REAL", "otp_tries INTEGER DEFAULT 0"):
        try: c.execute("ALTER TABLE users ADD COLUMN " + col)
        except sqlite3.OperationalError: pass
    return c

def secret():
    if os.environ.get("SECRET_KEY"): return os.environ["SECRET_KEY"]
    p = os.path.join(BASE, ".secret_key")
    if not os.path.exists(p): open(p, "w").write(secrets.token_hex(32))
    return open(p).read().strip()

def smtp_ready(): return bool(os.environ.get("SMTP_HOST"))

def send_code(email, name, code):
    if not smtp_ready():
        print(f"\n=== DEV MODE: verification code for {email} is {code} ===\n", flush=True); return
    m = EmailMessage(); m["Subject"] = "Your Thittam verification code"; m["To"] = email
    m["From"] = os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER")
    m.set_content(f"Hello {name},\n\nYour Thittam verification code is {code}. It expires in 10 minutes.\nIf you did not sign up, ignore this email.")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls(); s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.send_message(m)

def issue_otp(c, uid, email, name):
    code = f"{secrets.randbelow(10**6):06d}"
    c.execute("UPDATE users SET otp=?, otp_exp=?, otp_tries=0 WHERE id=?", (generate_password_hash(code), time.time() + 600, uid))
    c.commit(); send_code(email, name, code)

def current_user():
    uid = session.get("uid")
    if not uid: return None
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE id=? AND verified=1", (uid,)).fetchone()
    return {"id": r["id"], "name": r["name"], "email": r["email"], "profile": json.loads(r["profile"])} if r else None

def clean_profile(j): return {k: v for k, v in (j or {}).items() if k in PROFILE_KEYS and v not in (None, "")}

@bp.post("/api/register")
def register():
    j = request.json or {}; name = (j.get("name") or "").strip()[:60]; email = (j.get("email") or "").strip().lower(); pw = j.get("password") or ""
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(pw) < 8:
        return jsonify(error="Enter a name, a valid email and a password of at least 8 characters."), 400
    prof = clean_profile(j.get("profile")); prof["name"] = name
    c = db()
    old = c.execute("SELECT id, verified FROM users WHERE email=?", (email,)).fetchone()
    if old and old["verified"]: return jsonify(error="This email is already registered. Please log in."), 409
    if old: c.execute("DELETE FROM users WHERE id=?", (old["id"],))
    cur = c.execute("INSERT INTO users(name,email,pw,profile) VALUES(?,?,?,?)", (name, email, generate_password_hash(pw), json.dumps(prof)))
    session.clear(); session["pending"] = cur.lastrowid; issue_otp(c, cur.lastrowid, email, name)
    return jsonify(needs_verify=True, email=email, dev=not smtp_ready())

@bp.post("/api/login")
def login():
    j = request.json or {}; c = db()
    r = c.execute("SELECT * FROM users WHERE email=?", ((j.get("email") or "").strip().lower(),)).fetchone()
    if not r or not check_password_hash(r["pw"], j.get("password") or ""): return jsonify(error="Wrong email or password."), 401
    session.clear()
    if not r["verified"]:
        session["pending"] = r["id"]; issue_otp(c, r["id"], r["email"], r["name"])
        return jsonify(needs_verify=True, email=r["email"], dev=not smtp_ready())
    session["uid"] = r["id"]; return jsonify(user=current_user())

@bp.post("/api/verify")
def verify():
    uid = session.get("pending")
    if not uid: return jsonify(error="Please register or log in first."), 401
    c = db(); r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not r or not r["otp"] or time.time() > (r["otp_exp"] or 0): return jsonify(error="Code expired. Tap 'Resend code'."), 400
    if r["otp_tries"] >= 5: return jsonify(error="Too many attempts. Tap 'Resend code'."), 429
    c.execute("UPDATE users SET otp_tries=otp_tries+1 WHERE id=?", (uid,)); c.commit()
    if not check_password_hash(r["otp"], str((request.json or {}).get("code") or "").strip()): return jsonify(error="Wrong code."), 400
    c.execute("UPDATE users SET verified=1, otp=NULL WHERE id=?", (uid,)); c.commit()
    session.pop("pending", None); session["uid"] = uid
    return jsonify(user=current_user())

@bp.post("/api/resend")
def resend():
    uid = session.get("pending")
    if not uid: return jsonify(error="Please register or log in first."), 401
    c = db(); r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    issue_otp(c, uid, r["email"], r["name"]); return jsonify(ok=True, dev=not smtp_ready())

@bp.post("/api/logout")
def logout():
    session.clear(); return jsonify(ok=True)

@bp.get("/api/me")
def me(): return jsonify(user=current_user())

@bp.put("/api/profile")
def put_profile():
    u = current_user()
    if not u: return jsonify(error="Please log in."), 401
    p = clean_profile(request.json)
    with db() as c: c.execute("UPDATE users SET profile=?, name=? WHERE id=?", (json.dumps(p), p.get("name") or u["name"], u["id"]))
    return jsonify(user=current_user())