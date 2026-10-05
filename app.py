import sqlite3, hashlib, os, functools
from flask import Flask, g, request, jsonify, session, render_template, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "breachout.db")
app = Flask(__name__)
app.secret_key = os.environ.get("BREACHOUT_SECRET", "dev-secret-change-me")

LEVELS = {1: "Beginner", 2: "Intermediate", 3: "Advanced"}

# ---------- database helpers ----------

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
        available INTEGER NOT NULL DEFAULT 1
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
    """)
    # seed one admin account if none exists yet
    if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone() is None:
        db.execute(
            "INSERT INTO users(username, password_hash, role) VALUES(?,?,?)",
            ("admin", generate_password_hash("admin123"), "admin"),
        )
    # seed the 10 default challenges if the table is empty
    if db.execute("SELECT COUNT(*) FROM challenges").fetchone()[0] == 0:
        seed = [
            ("Open door", "Authentication", 1, 100,
             "An old IronHold intranet is online. The admin panel still uses its default credentials — find the login page and get in.",
             "FLAG{default_creds_are_never_safe}"),
            ("Weak port", "Network & Firewall", 1, 100,
             "Scan the host and find the one service the firewall never locked down.",
             "FLAG{one_open_port_is_enough}"),
            ("Suspicious mail", "Phishing & Social Engineering", 1, 100,
             "Check the sender domain and headers to prove the email is spoofed.",
             "FLAG{spf_fail_gives_it_away}"),
            ("Hidden message", "Encryption & Data Protection", 1, 100,
             "An intercepted note looks like nonsense. Recognize the encoding and decode it.",
             "FLAG{base64_is_not_encryption}"),
            ("Person of interest", "Phishing & Social Engineering", 2, 200,
             "Using only public information, identify the employee and recover their security answer.",
             "FLAG{osint_finds_everything}"),
            ("Digital traces", "Forensics & Log Analysis", 2, 200,
             "Examine the disk image and reconstruct what the attacker deleted.",
             "FLAG{deleted_is_not_gone}"),
            ("Keyring", "Encryption & Data Protection", 2, 200,
             "A backup archive contains a private SSH key. Crack its passphrase.",
             "FLAG{weak_passphrase_weak_key}"),
            ("Session hijack", "Authentication", 3, 300,
             "Steal and replay a session cookie to log in as another user.",
             "FLAG{cookies_need_httponly}"),
            ("Silent sniffer", "Network & Firewall", 3, 300,
             "ARP-spoof between two servers and decrypt the captured traffic.",
             "FLAG{unencrypted_traffic_talks}"),
            ("Logbook", "Forensics & Log Analysis", 3, 300,
             "Correlate timestamps in the server logs to trace the attacker's path.",
             "FLAG{logs_never_lie}"),
        ]
        db.executemany(
            "INSERT INTO challenges(name, category, level, points, description, flag_hash, available) "
            "VALUES(?,?,?,?,?,?,1)",
            [(n, c, l, p, d, hashlib.sha256(f.encode()).hexdigest()) for n, c, l, p, d, f in seed],
        )
    db.commit()
    db.close()

# ---------- auth helpers ----------

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

def admin_required(f):
    @functools.wraps(f)
    def wrap(*a, **kw):
        u = current_user()
        if not u or u["role"] != "admin":
            return jsonify(error="admin only"), 403
        return f(*a, **kw)
    return wrap

# ---------- pages ----------

@app.route("/")
def index():
    return render_template("index.html")

# ---------- auth API ----------

@app.post("/api/auth/register")
def register():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify(error="username and password are required"), 400
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        return jsonify(error="username already exists"), 409
    db.execute(
        "INSERT INTO users(username, password_hash, role) VALUES(?,?,'player')",
        (username, generate_password_hash(password)),
    )
    db.commit()
    return jsonify(ok=True)

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

# ---------- challenge API (players) ----------

def challenge_status(db, user_id, ch):
    solved = db.execute(
        "SELECT 1 FROM solves WHERE user_id=? AND challenge_id=?", (user_id, ch["id"])
    ).fetchone()
    if solved:
        return "solved"
    env = db.execute(
        "SELECT status FROM environments WHERE user_id=? AND challenge_id=?", (user_id, ch["id"])
    ).fetchone()
    return env["status"] if env else "stopped"

@app.get("/api/challenges")
@login_required
def list_challenges():
    db = get_db()
    user = current_user()
    rows = db.execute("SELECT * FROM challenges ORDER BY level, id").fetchall()
    out = []
    for ch in rows:
        out.append({
            "id": ch["id"], "name": ch["name"], "category": ch["category"],
            "level": ch["level"], "level_name": LEVELS[ch["level"]],
            "points": ch["points"], "description": ch["description"],
            "available": bool(ch["available"]),
            "status": challenge_status(db, user["id"], ch),
        })
    return jsonify(out)

@app.post("/api/challenges/<int:cid>/start")
@login_required
def start_challenge(cid):
    db = get_db()
    user = current_user()
    ch = db.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch or not ch["available"]:
        return jsonify(error="challenge not available"), 400
    address = f"10.10.{cid}.5"
    db.execute(
        "INSERT INTO environments(user_id, challenge_id, status, address) VALUES(?,?, 'active', ?) "
        "ON CONFLICT(user_id, challenge_id) DO UPDATE SET status='active', address=excluded.address",
        (user["id"], cid, address),
    )
    db.commit()
    # NOTE: this is where you call out to Terraform/Ansible to really build the VM,
    # e.g. subprocess.run(["terraform", "apply", "-auto-approve"], cwd=f"infra/challenge-{cid}")
    return jsonify(ok=True, address=address, user="student", password="Ijs8-kaBoom")

@app.post("/api/challenges/<int:cid>/reset")
@login_required
def reset_challenge(cid):
    return start_challenge(cid)

@app.post("/api/challenges/<int:cid>/stop")
@login_required
def stop_challenge(cid):
    db = get_db()
    user = current_user()
    db.execute(
        "UPDATE environments SET status='stopped' WHERE user_id=? AND challenge_id=?",
        (user["id"], cid),
    )
    db.commit()
    # NOTE: this is where you'd call `terraform destroy` for this challenge/user
    return jsonify(ok=True)

@app.post("/api/challenges/<int:cid>/submit")
@login_required
def submit_flag(cid):
    db = get_db()
    user = current_user()
    data = request.get_json(force=True)
    flag = (data.get("flag") or "").strip()
    ch = db.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch:
        return jsonify(error="unknown challenge"), 404
    if hashlib.sha256(flag.encode()).hexdigest() != ch["flag_hash"]:
        return jsonify(ok=False, error="incorrect flag"), 200
    db.execute(
        "INSERT OR IGNORE INTO solves(user_id, challenge_id) VALUES(?,?)", (user["id"], cid)
    )
    db.execute(
        "UPDATE environments SET status='solved' WHERE user_id=? AND challenge_id=?",
        (user["id"], cid),
    )
    db.commit()
    return jsonify(ok=True, points=ch["points"])

# ---------- dashboard ----------

@app.get("/api/dashboard")
@login_required
def dashboard():
    db = get_db()
    users = db.execute("SELECT id, username FROM users WHERE role='player'").fetchall()
    challenges = {c["id"]: c for c in db.execute("SELECT * FROM challenges").fetchall()}
    board = []
    for u in users:
        solved_ids = [r["challenge_id"] for r in db.execute(
            "SELECT challenge_id FROM solves WHERE user_id=?", (u["id"],)).fetchall()]
        points = sum(challenges[i]["points"] for i in solved_ids if i in challenges)
        active = db.execute(
            "SELECT challenge_id FROM environments WHERE user_id=? AND status='active'", (u["id"],)
        ).fetchone()
        board.append({
            "username": u["username"],
            "points": points,
            "solved": solved_ids,
            "current": (active["challenge_id"] if active else None),
        })
    board.sort(key=lambda r: -r["points"])
    return jsonify(board)

# ---------- admin: manage challenges ----------

@app.get("/api/admin/challenges")
@admin_required
def admin_list_challenges():
    rows = get_db().execute("SELECT * FROM challenges ORDER BY level, id").fetchall()
    return jsonify([{
        "id": r["id"], "name": r["name"], "category": r["category"],
        "level": r["level"], "level_name": LEVELS[r["level"]],
        "points": r["points"], "description": r["description"],
        "available": bool(r["available"]),
    } for r in rows])

@app.post("/api/admin/challenges")
@admin_required
def admin_add_challenge():
    data = request.get_json(force=True)
    required = ["name", "category", "level", "points", "description", "flag"]
    if any(not str(data.get(k, "")).strip() for k in required):
        return jsonify(error=f"all fields are required: {', '.join(required)}"), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO challenges(name, category, level, points, description, flag_hash, available) "
        "VALUES(?,?,?,?,?,?,1)",
        (data["name"], data["category"], int(data["level"]), int(data["points"]),
         data["description"], hashlib.sha256(data["flag"].strip().encode()).hexdigest()),
    )
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid)

@app.put("/api/admin/challenges/<int:cid>")
@admin_required
def admin_edit_challenge(cid):
    data = request.get_json(force=True)
    db = get_db()
    ch = db.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch:
        return jsonify(error="not found"), 404
    flag_hash = ch["flag_hash"]
    if data.get("flag"):
        flag_hash = hashlib.sha256(data["flag"].strip().encode()).hexdigest()
    db.execute(
        "UPDATE challenges SET name=?, category=?, level=?, points=?, description=?, flag_hash=? WHERE id=?",
        (data.get("name", ch["name"]), data.get("category", ch["category"]),
         int(data.get("level", ch["level"])), int(data.get("points", ch["points"])),
         data.get("description", ch["description"]), flag_hash, cid),
    )
    db.commit()
    return jsonify(ok=True)

@app.delete("/api/admin/challenges/<int:cid>")
@admin_required
def admin_delete_challenge(cid):
    db = get_db()
    db.execute("DELETE FROM challenges WHERE id=?", (cid,))
    db.execute("DELETE FROM environments WHERE challenge_id=?", (cid,))
    db.execute("DELETE FROM solves WHERE challenge_id=?", (cid,))
    db.commit()
    return jsonify(ok=True)

@app.post("/api/admin/challenges/<int:cid>/toggle")
@admin_required
def admin_toggle_challenge(cid):
    db = get_db()
    db.execute("UPDATE challenges SET available = 1 - available WHERE id=?", (cid,))
    db.commit()
    return jsonify(ok=True)

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=True)
else:
    init_db()
