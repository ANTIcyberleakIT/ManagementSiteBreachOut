import sqlite3, hashlib, os, functools, csv, io
from flask import Flask, g, request, jsonify, session, render_template, Response
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "breachout.db")
app = Flask(__name__)
app.secret_key = os.environ.get("BREACHOUT_SECRET", "dev-secret-change-me")

LEVELS = {1: "Beginner", 2: "Intermediate", 3: "Advanced"}
HINT_PENALTY = 10

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'player'
    );
    CREATE TABLE IF NOT EXISTS challenges(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        level INTEGER NOT NULL,
        points INTEGER NOT NULL,
        description TEXT NOT NULL,
        flag_hash TEXT NOT NULL,
        available INTEGER NOT NULL DEFAULT 1,
        hint TEXT
    );
    CREATE TABLE IF NOT EXISTS hints_used(
        user_id INTEGER NOT NULL,
        challenge_id INTEGER NOT NULL,
        requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, challenge_id)
    );
    CREATE TABLE IF NOT EXISTS environments(
        user_id INTEGER NOT NULL,
        challenge_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'stopped',
        address TEXT,
        PRIMARY KEY(user_id, challenge_id)
    );
    CREATE TABLE IF NOT EXISTS solves(
        user_id INTEGER NOT NULL,
        challenge_id INTEGER NOT NULL,
        solved_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, challenge_id)
    );
    CREATE TABLE IF NOT EXISTS session_control(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 0,
        time_remaining INTEGER NOT NULL DEFAULT 3600,
        broadcast_msg TEXT
    );
    """)
    if db.execute("SELECT COUNT(*) FROM session_control").fetchone()[0] == 0:
        db.execute("INSERT INTO session_control(title, is_active, time_remaining) VALUES('Standaard Trainingssessie', 0, 3600)")
    
    if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone() is None:
        db.execute("INSERT INTO users(username, password_hash, role) VALUES(?,?,?)", ("admin", generate_password_hash("admin123"), "admin"))
    if db.execute("SELECT 1 FROM users WHERE role='spelleider'").fetchone() is None:
        db.execute("INSERT INTO users(username, password_hash, role) VALUES(?,?,?)", ("spelleider", generate_password_hash("spelleider123"), "spelleider"))

    db.commit()
    db.close()

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def login_required(f):
    @functools.wraps(f)
    def wrap(*a, **kw):
        if not current_user():
            return jsonify(error="not logged in"), 401
        return f(*a, **kw)
    return wrap

def role_required(*roles):
    def decorator(f):
        @functools.wraps(f)
        def wrap(*a, **kw):
            u = current_user()
            if not u or u["role"] not in roles:
                return jsonify(error="insufficient permissions"), 403
            return f(*a, **kw)
        return wrap
    return decorator

@app.get("/health")
def health_check():
    return jsonify(status="ok", database="connected"), 200

@app.route("/")
def index():
    return render_template("index.html")

@app.post("/api/auth/login")
def login():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify(error="invalid username or password"), 401
    session["user_id"] = user["id"]
    return jsonify(ok=True, username=user["username"], role=user["role"])

@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(ok=True)

@app.get("/api/me")
def me():
    u = current_user()
    if not u:
        return jsonify(error="not logged in"), 401
    return jsonify(username=u["username"], role=u["role"])

@app.get("/api/challenges")
@login_required
def list_challenges():
    db = get_db()
    user = current_user()
    rows = db.execute("SELECT * FROM challenges ORDER BY level, id").fetchall()
    out = []
    for ch in rows:
        solved = db.execute("SELECT 1 FROM solves WHERE user_id=? AND challenge_id=?", (user["id"], ch["id"])).fetchone()
        env = db.execute("SELECT status FROM environments WHERE user_id=? AND challenge_id=?", (user["id"], ch["id"])).fetchone()
        status = "solved" if solved else (env["status"] if env else "stopped")
        out.append({
            "id": ch["id"], "name": ch["name"], "category": ch["category"],
            "level": ch["level"], "level_name": LEVELS.get(ch["level"], "Unknown"),
            "points": ch["points"], "description": ch["description"],
            "available": bool(ch["available"]), "status": status,
            "has_hint": bool(ch["hint"])
        })
    return jsonify(out)

@app.post("/api/challenges/<int:cid>/start")
@login_required
def start_challenge(cid):
    db = get_db()
    user = current_user()
    address = f"10.105.25.{100 + cid}"
    db.execute(
        "INSERT INTO environments(user_id, challenge_id, status, address) VALUES(?,?, 'active', ?) "
        "ON CONFLICT(user_id, challenge_id) DO UPDATE SET status='active', address=excluded.address",
        (user["id"], cid, address)
    )
    db.commit()
    return jsonify(ok=True, address=address, user="student", password="Ijs8-kaBoom")

@app.post("/api/challenges/<int:cid>/stop")
@login_required
def stop_challenge(cid):
    db = get_db()
    user = current_user()
    db.execute("UPDATE environments SET status='stopped' WHERE user_id=? AND challenge_id=?", (user["id"], cid))
    db.commit()
    return jsonify(ok=True)

@app.post("/api/challenges/<int:cid>/submit")
@login_required
def submit_flag(cid):
    db = get_db()
    user = current_user()
    data = request.get_json(force=True)
    flag = (data.get("flag") or "").strip()
    ch = db.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch or hashlib.sha256(flag.encode()).hexdigest() != ch["flag_hash"]:
        return jsonify(ok=False, error="Incorrect flag"), 200
    db.execute("INSERT OR IGNORE INTO solves(user_id, challenge_id) VALUES(?,?)", (user["id"], cid))
    db.execute("UPDATE environments SET status='solved' WHERE user_id=? AND challenge_id=?", (user["id"], cid))
    db.commit()
    return jsonify(ok=True, points=ch["points"])

@app.get("/api/dashboard")
@login_required
def dashboard():
    db = get_db()
    users = db.execute("SELECT id, username FROM users WHERE role='player'").fetchall()
    challenges = {c["id"]: c for c in db.execute("SELECT * FROM challenges").fetchall()}
    board = []
    for u in users:
        solved_ids = [r["challenge_id"] for r in db.execute("SELECT challenge_id FROM solves WHERE user_id=?", (u["id"],)).fetchall()]
        points = sum(challenges[i]["points"] for i in solved_ids if i in challenges)
        active = db.execute("SELECT challenge_id FROM environments WHERE user_id=? AND status='active'", (u["id"],)).fetchone()
        board.append({
            "username": u["username"], "points": points,
            "solved": solved_ids, "current": (active["challenge_id"] if active else None)
        })
    board.sort(key=lambda r: -r["points"])
    return jsonify(board)

@app.get("/api/spelleider/session")
@role_required("spelleider", "admin")
def get_session_info():
    sess = get_db().execute("SELECT * FROM session_control WHERE id=1").fetchone()
    return jsonify(dict(sess))

@app.post("/api/spelleider/session/toggle")
@role_required("spelleider", "admin")
def toggle_session():
    db = get_db()
    db.execute("UPDATE session_control SET is_active = 1 - is_active WHERE id=1")
    db.commit()
    return jsonify(ok=True)

@app.post("/api/spelleider/broadcast")
@role_required("spelleider", "admin")
def broadcast_msg():
    data = request.get_json(force=True)
    msg = data.get("message", "")
    db = get_db()
    db.execute("UPDATE session_control SET broadcast_msg=? WHERE id=1", (msg,))
    db.commit()
    return jsonify(ok=True)

@app.get("/api/spelleider/export")
@role_required("spelleider", "admin")
def export_results():
    db = get_db()
    users = db.execute("SELECT id, username FROM users WHERE role='player'").fetchall()
    challenges = {c["id"]: c for c in db.execute("SELECT * FROM challenges").fetchall()}
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Speler", "Aantal Opgelost", "Totale Punten"])
    
    for u in users:
        solved_ids = [r["challenge_id"] for r in db.execute("SELECT challenge_id FROM solves WHERE user_id=?", (u["id"],)).fetchall()]
        points = sum(challenges[i]["points"] for i in solved_ids if i in challenges)
        writer.writerow([u["username"], len(solved_ids), points])
        
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=breachout_resultaten.csv"}
    )

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=True)
else:
    init_db()
