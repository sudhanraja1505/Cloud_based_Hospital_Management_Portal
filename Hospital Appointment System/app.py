"""Hospital Appointment System - Flask + SQLite backend.
Run:  python -m pip install -r requirements.txt  then  python app.py
Cloud: use PostgreSQL (RDS) instead of SQLite and run behind gunicorn (Linux) or waitress (Windows)."""
import os, sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
import jwt
from flask import Flask, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
SECRET = os.environ.get("SECRET_KEY", "change-me-in-production")
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE, "hospital.db"))
SLOT_MINUTES = 30

app = Flask(__name__, static_folder="static", static_url_path="")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('patient','doctor')),
  specialization TEXT, start_hour INTEGER DEFAULT 9, end_hour INTEGER DEFAULT 17);
CREATE TABLE IF NOT EXISTS appointments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doctor_id INTEGER NOT NULL REFERENCES users(id),
  patient_id INTEGER NOT NULL REFERENCES users(id),
  date TEXT NOT NULL, time TEXT NOT NULL, reason TEXT,
  status TEXT NOT NULL DEFAULT 'booked' CHECK(status IN ('booked','completed','cancelled')),
  notes TEXT, created_at TEXT NOT NULL);
-- one active booking per doctor per slot: blocks double booking even under concurrent requests
CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot
  ON appointments(doctor_id, date, time) WHERE status IN ('booked','completed');
"""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

with sqlite3.connect(DB_PATH) as c:
    c.executescript(SCHEMA)

def make_token(u):
    return jwt.encode({"sub": str(u["id"]), "role": u["role"], "name": u["name"],
                       "exp": datetime.now(timezone.utc) + timedelta(hours=12)}, SECRET, algorithm="HS256")

def auth(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            h = request.headers.get("Authorization", "")
            try:
                p = jwt.decode(h.removeprefix("Bearer "), SECRET, algorithms=["HS256"])
            except jwt.PyJWTError:
                return jsonify(error="Please sign in again."), 401
            if roles and p["role"] not in roles:
                return jsonify(error="You don't have access to this."), 403
            g.user = {"id": int(p["sub"]), "role": p["role"], "name": p["name"]}
            return fn(*a, **kw)
        return wrapper
    return deco

# ---- accounts
@app.post("/api/register")
def register():
    d = request.get_json(force=True)
    if not all(d.get(k) for k in ("name", "email", "password", "role")) or d["role"] not in ("patient", "doctor"):
        return jsonify(error="Fill in name, email, password and role."), 400
    if len(d["password"]) < 6:
        return jsonify(error="Password must be at least 6 characters."), 400
    if d["role"] == "doctor" and not d.get("specialization"):
        return jsonify(error="Doctors need a specialization."), 400
    try:
        cur = db().execute("INSERT INTO users(name,email,password,role,specialization) VALUES(?,?,?,?,?)",
                           (d["name"].strip(), d["email"].lower().strip(), generate_password_hash(d["password"]),
                            d["role"], (d.get("specialization") or "").strip() or None))
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(error="That email is already registered."), 409
    u = {"id": cur.lastrowid, "role": d["role"], "name": d["name"].strip()}
    return jsonify(token=make_token(u), role=u["role"], name=u["name"])

@app.post("/api/login")
def login():
    d = request.get_json(force=True)
    u = db().execute("SELECT * FROM users WHERE email=?", (d.get("email", "").lower().strip(),)).fetchone()
    if not u or not check_password_hash(u["password"], d.get("password", "")):
        return jsonify(error="Email or password is incorrect."), 401
    return jsonify(token=make_token(u), role=u["role"], name=u["name"])

# ---- doctors and slots
@app.get("/api/doctors")
@auth()
def doctors():
    q = f"%{request.args.get('q', '').strip()}%"
    rows = db().execute("""SELECT id, name, specialization, start_hour, end_hour FROM users
                           WHERE role='doctor' AND (name LIKE ? OR specialization LIKE ?) ORDER BY name""", (q, q))
    return jsonify([dict(r) for r in rows])

def parse_date(s):
    try: return datetime.strptime(s, "%Y-%m-%d")
    except (TypeError, ValueError): return None

def all_slots(doc, day):
    t = day.replace(hour=doc["start_hour"])
    end = day.replace(hour=doc["end_hour"])
    while t < end:
        yield t
        t += timedelta(minutes=SLOT_MINUTES)

@app.get("/api/doctors/<int:did>/slots")
@auth()
def slots(did):
    doc = db().execute("SELECT * FROM users WHERE id=? AND role='doctor'", (did,)).fetchone()
    day = parse_date(request.args.get("date"))
    if not doc or not day: return jsonify(error="Choose a doctor and a valid date."), 400
    taken = {r["time"] for r in db().execute(
        "SELECT time FROM appointments WHERE doctor_id=? AND date=? AND status IN ('booked','completed')",
        (did, request.args["date"]))}
    now = datetime.now()
    return jsonify([{"time": t.strftime("%H:%M"), "free": t > now and t.strftime("%H:%M") not in taken}
                    for t in all_slots(doc, day)])

@app.put("/api/hours")
@auth("doctor")
def set_hours():
    d = request.get_json(force=True)
    try: s, e = int(d["start_hour"]), int(d["end_hour"])
    except (KeyError, TypeError, ValueError): return jsonify(error="Enter start and end hours."), 400
    if not (0 <= s < e <= 24): return jsonify(error="Start must be before end (0-24)."), 400
    db().execute("UPDATE users SET start_hour=?, end_hour=? WHERE id=?", (s, e, g.user["id"]))
    db().commit()
    return jsonify(ok=True)

# ---- appointments
@app.post("/api/appointments")
@auth("patient")
def book():
    d = request.get_json(force=True)
    doc = db().execute("SELECT * FROM users WHERE id=? AND role='doctor'", (d.get("doctor_id"),)).fetchone()
    day = parse_date(d.get("date"))
    if not doc or not day: return jsonify(error="Choose a doctor and a valid date."), 400
    valid = {t.strftime("%H:%M"): t for t in all_slots(doc, day)}
    t = valid.get(d.get("time"))
    if not t: return jsonify(error="That time isn't one of the doctor's slots."), 400
    if t <= datetime.now(): return jsonify(error="That time has already passed."), 400
    try:
        db().execute("""INSERT INTO appointments(doctor_id,patient_id,date,time,reason,created_at)
                        VALUES(?,?,?,?,?,?)""", (doc["id"], g.user["id"], d["date"], d["time"],
                       (d.get("reason") or "").strip(), datetime.now(timezone.utc).isoformat()))
        db().commit()
    except sqlite3.IntegrityError:
        return jsonify(error="Someone just booked that slot. Pick another time."), 409
    return jsonify(ok=True), 201

@app.get("/api/appointments")
@auth()
def my_appointments():
    if g.user["role"] == "patient":
        rows = db().execute("""SELECT a.*, u.name AS doctor, u.specialization FROM appointments a
            JOIN users u ON u.id=a.doctor_id WHERE a.patient_id=? ORDER BY a.date DESC, a.time DESC""", (g.user["id"],))
    else:
        rows = db().execute("""SELECT a.*, u.name AS patient FROM appointments a
            JOIN users u ON u.id=a.patient_id WHERE a.doctor_id=? ORDER BY a.date, a.time""", (g.user["id"],))
    return jsonify([dict(r) for r in rows])

def own_appointment(aid):
    col = "patient_id" if g.user["role"] == "patient" else "doctor_id"
    return db().execute(f"SELECT * FROM appointments WHERE id=? AND {col}=?", (aid, g.user["id"])).fetchone()

@app.post("/api/appointments/<int:aid>/cancel")
@auth()
def cancel(aid):
    a = own_appointment(aid)
    if not a: return jsonify(error="Appointment not found."), 404
    if a["status"] != "booked": return jsonify(error="Only booked appointments can be cancelled."), 400
    db().execute("UPDATE appointments SET status='cancelled' WHERE id=?", (aid,))
    db().commit()
    return jsonify(ok=True)

@app.post("/api/appointments/<int:aid>/complete")
@auth("doctor")
def complete(aid):
    a = own_appointment(aid)
    if not a: return jsonify(error="Appointment not found."), 404
    if a["status"] != "booked": return jsonify(error="Only booked appointments can be completed."), 400
    notes = (request.get_json(force=True).get("notes") or "").strip()
    db().execute("UPDATE appointments SET status='completed', notes=? WHERE id=?", (notes, aid))
    db().commit()
    return jsonify(ok=True)

@app.get("/")
def index(): return app.send_static_file("index.html")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
