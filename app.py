"""BreachOut backend (Flask + SQLite).

Rollen:   player (deelnemer) · gamemaster (spelleider) · admin (beheerder)
Nieuw:    teams, sessies met automatisch opruimen (US37), reset in één actie met
          tijdmeting (US24), hints sturen door de spelleider (US32), live scoreboard
          (Server-Sent Events), rechtenbeheer door de admin en automatische
          verwijdering van deelnemers na 90 dagen.
"""
import sqlite3, hashlib, os, sys, re, csv, io, json, time, shlex, subprocess, threading, functools
from datetime import datetime, timedelta, timezone
from flask import Flask, g, request, jsonify, session, render_template, Response
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("BREACHOUT_DB", os.path.join(BASE_DIR, "breachout.db"))

app = Flask(__name__)
app.secret_key = os.environ.get("BREACHOUT_SECRET", "dev-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("BREACHOUT_SECURE_COOKIES") == "1",
)

LEVELS = {1: "Beginner", 2: "Intermediate", 3: "Advanced"}
ROLES = ("player", "gamemaster", "admin")
HINT_PENALTY = 10               # punten aftrek per hint die een speler zelf vraagt
RETENTION_DAYS = 90             # deelnemers worden na 90 dagen automatisch verwijderd
CLEANUP_LIMIT_SECONDS = 300     # doel uit US24/US37: opruimen/resetten binnen 5 minuten
ENV_CMD = os.environ.get("BREACHOUT_ENV_CMD", "").strip()
ENV_TIMEOUT = int(os.environ.get("BREACHOUT_ENV_TIMEOUT", "600"))

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
TEAM_RE = re.compile(r"^[A-Za-z0-9 _.-]{2,40}$")

# ---------------------------------------------------------------- tijd

FMT = "%Y-%m-%d %H:%M:%S"

def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)

def ts(dt=None):
    return (dt or utcnow()).strftime(FMT)

def parse_ts(s):
    return datetime.strptime(s, FMT)

# ---------------------------------------------------------------- database

def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    return db

def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'player',
        created_at TEXT,
        team_id INTEGER)""",
    """CREATE TABLE IF NOT EXISTS teams(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        created_by INTEGER,
        created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS challenges(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        level INTEGER NOT NULL,
        points INTEGER NOT NULL,
        description TEXT NOT NULL,
        flag_hash TEXT NOT NULL,
        available INTEGER NOT NULL DEFAULT 1,
        hint TEXT)""",
    """CREATE TABLE IF NOT EXISTS hints_used(
        user_id INTEGER NOT NULL,
        challenge_id INTEGER NOT NULL,
        requested_at TEXT DEFAULT CURRENT_TIMESTAMP,
        source TEXT DEFAULT 'self',
        custom_text TEXT,
        PRIMARY KEY(user_id, challenge_id))""",
    """CREATE TABLE IF NOT EXISTS environments(
        user_id INTEGER NOT NULL,
        challenge_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'stopped',
        address TEXT,
        PRIMARY KEY(user_id, challenge_id))""",
    """CREATE TABLE IF NOT EXISTS solves(
        user_id INTEGER NOT NULL,
        challenge_id INTEGER NOT NULL,
        solved_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, challenge_id))""",
    """CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        team_id INTEGER NOT NULL,
        started_by INTEGER,
        started_at TEXT NOT NULL,
        ends_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        ended_at TEXT,
        cleanup_seconds REAL,
        cleanup_failed INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS session_results(
        session_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        points INTEGER NOT NULL,
        solved TEXT NOT NULL,
        hints TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS action_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        action TEXT NOT NULL,
        challenge_id INTEGER,
        user_id INTEGER,
        actor_id INTEGER,
        duration REAL,
        ok INTEGER,
        within_limit INTEGER)""",
    """CREATE TABLE IF NOT EXISTS meta(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL)""",
]

# kolommen die bij oudere databases nog ontbreken
MIGRATIONS = [
    ("users", "created_at", "TEXT"),
    ("users", "team_id", "INTEGER"),
    ("challenges", "hint", "TEXT"),
    ("hints_used", "source", "TEXT DEFAULT 'self'"),
    ("hints_used", "custom_text", "TEXT"),
    ("sessions", "cleanup_failed", "INTEGER DEFAULT 0"),
]

SEED = [
    ("Open door", "Authentication", 1, 100,
     "An old IronHold intranet is online. The admin panel still uses its default credentials — find the login page and get in.",
     "FLAG{default_creds_are_never_safe}",
     "Scan the web server with nmap, then try default combinations like admin/admin on the login page."),
    ("Weak port", "Network & Firewall", 1, 100,
     "Scan the host and find the one service the firewall never locked down.",
     "FLAG{one_open_port_is_enough}",
     "Run nmap -sV and connect to the unlocked service with nc to see what it gives up."),
    ("Suspicious mail", "Phishing & Social Engineering", 1, 100,
     "Check the sender domain and headers to prove the email is spoofed.",
     "FLAG{spf_fail_gives_it_away}",
     "Look at the SPF, DKIM and Return-Path headers, and compare the real domain with the displayed one."),
    ("Hidden message", "Encryption & Data Protection", 1, 100,
     "An intercepted note looks like nonsense. Recognize the encoding and decode it.",
     "FLAG{base64_is_not_encryption}",
     "Try CyberChef or base64 -d; if it's a Caesar cipher just try all 25 shifts."),
    ("Person of interest", "Phishing & Social Engineering", 2, 200,
     "Using only public information, identify the employee and recover their security answer.",
     "FLAG{osint_finds_everything}",
     "Search by name on LinkedIn/GitHub/social media for a date of birth or pet name."),
    ("Digital traces", "Forensics & Log Analysis", 2, 200,
     "Examine the disk image and reconstruct what the attacker deleted.",
     "FLAG{deleted_is_not_gone}",
     "Open the image in autopsy or sleuthkit and look specifically at recently deleted files."),
    ("Keyring", "Encryption & Data Protection", 2, 200,
     "A backup archive contains a private SSH key. Crack its passphrase.",
     "FLAG{weak_passphrase_weak_key}",
     "Use ssh2john on the id_rsa file, then crack the hash with john and a wordlist."),
    ("Session hijack", "Authentication", 3, 300,
     "Steal and replay a session cookie to log in as another user.",
     "FLAG{cookies_need_httponly}",
     "Look for an XSS vector or a cookie missing the HttpOnly flag, then reuse it with curl -b."),
    ("Silent sniffer", "Network & Firewall", 3, 300,
     "ARP-spoof between two servers and decrypt the captured traffic.",
     "FLAG{unencrypted_traffic_talks}",
     "Use ettercap or bettercap for the ARP spoof, then capture with tcpdump/Wireshark."),
    ("Logbook", "Forensics & Log Analysis", 3, 300,
     "Correlate timestamps in the server logs to trace the attacker's path.",
     "FLAG{logs_never_lie}",
     "grep for repeated failed requests or odd user-agents, then follow the timestamps."),
]

def sha(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()

def init_db():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.isolation_level = None            # we sturen BEGIN/COMMIT zelf aan
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("BEGIN IMMEDIATE")        # twee gunicorn-workers kunnen elkaar niet in de weg zitten
    try:
        for stmt in SCHEMA:
            db.execute(stmt)
        for table, col, coldef in MIGRATIONS:
            cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")
        db.execute("UPDATE users SET created_at=? WHERE created_at IS NULL", (ts(),))
        db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('version','1')")
        if db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone() is None:
            db.execute(
                "INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,?,?)",
                ("admin", generate_password_hash("admin123"), "admin", ts()))
        if db.execute("SELECT COUNT(*) FROM challenges").fetchone()[0] == 0:
            db.executemany(
                "INSERT INTO challenges(name, category, level, points, description, flag_hash, available, hint) "
                "VALUES(?,?,?,?,?,?,1,?)",
                [(n, c, l, p, d, sha(f), h) for n, c, l, p, d, f, h in SEED])
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    finally:
        db.close()

def bump(db):
    """Verhoog de versieteller: alle open schermen (SSE) weten dan dat ze moeten verversen."""
    db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='version'")

def body():
    return request.get_json(silent=True) or {}

# ---------------------------------------------------------------- auth

def current_user():
    if "_user" in g:
        return g._user
    uid = session.get("user_id")
    user = None
    if uid:
        user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    g._user = user
    return user

def roles_required(*roles):
    def deco(f):
        @functools.wraps(f)
        def wrap(*a, **kw):
            u = current_user()
            if not u:
                return jsonify(error="not logged in"), 401
            if u["role"] not in roles:
                return jsonify(error="not allowed"), 403
            return f(*a, **kw)
        return wrap
    return deco

login_required = roles_required(*ROLES)
gm_required = roles_required("gamemaster", "admin")
admin_required = roles_required("admin")

# ---------------------------------------------------------------- omgevingen (Terraform/Ansible-haak)

def run_env_action(action, challenge_id, user_id):
    """Voer een omgevingsactie uit en meet de tijd.

    action: start | reset | stop | cleanup
    Is BREACHOUT_ENV_CMD niet gezet, dan wordt de actie gesimuleerd (direct klaar).
    Voorbeeld:
      BREACHOUT_ENV_CMD="ansible-playbook /pad/ansible/challenge.yml -e action={action} -e challenge_id={challenge_id} -e user_id={user_id}"
    Er worden alleen getallen/vaste woorden ingevuld, nooit gebruikersinvoer, en er wordt geen shell gebruikt.
    """
    start = time.monotonic()
    ok = True
    if ENV_CMD:
        args = [a.format(action=action, challenge_id=int(challenge_id), user_id=int(user_id))
                for a in shlex.split(ENV_CMD)]
        try:
            ok = subprocess.run(args, capture_output=True, timeout=ENV_TIMEOUT).returncode == 0
        except Exception:
            ok = False
    return ok, time.monotonic() - start

def log_action(db, action, challenge_id, user_id, actor_id, duration, ok):
    limited = action in ("reset", "cleanup")
    within = int(duration <= CLEANUP_LIMIT_SECONDS) if limited else None
    db.execute(
        "INSERT INTO action_log(ts, action, challenge_id, user_id, actor_id, duration, ok, within_limit) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (ts(), action, challenge_id, user_id, actor_id, round(duration, 2), int(bool(ok)), within))

def set_env(db, user_id, cid, status, address=None):
    db.execute(
        "INSERT INTO environments(user_id, challenge_id, status, address) VALUES(?,?,?,?) "
        "ON CONFLICT(user_id, challenge_id) DO UPDATE SET status=excluded.status, "
        "address=COALESCE(excluded.address, environments.address)",
        (user_id, cid, status, address))

# ---------------------------------------------------------------- scores

def user_progress(db, uid, ch_points):
    solved = [r[0] for r in db.execute("SELECT challenge_id FROM solves WHERE user_id=?", (uid,))]
    hints = db.execute("SELECT challenge_id, source FROM hints_used WHERE user_id=?", (uid,)).fetchall()
    hinted = [h["challenge_id"] for h in hints]
    paid = sum(1 for h in hints if h["source"] == "self")   # hints van de spelleider zijn gratis
    points = sum(ch_points.get(i, 0) for i in solved) - paid * HINT_PENALTY
    return solved, hinted, points

def active_session(db, team_id):
    if not team_id:
        return None
    return db.execute("SELECT * FROM sessions WHERE team_id=? AND status='active' ORDER BY id DESC LIMIT 1",
                      (team_id,)).fetchone()

def remaining(sess):
    return max(0, int((parse_ts(sess["ends_at"]) - utcnow()).total_seconds()))

# ---------------------------------------------------------------- sessies opruimen (US37)

def finalize_session(db, sid):
    """Sluit een sessie af: resultaten archiveren, alle omgevingen van het team opruimen."""
    cur = db.execute("UPDATE sessions SET status='ending' WHERE id=? AND status='active'", (sid,))
    db.commit()
    if cur.rowcount == 0:
        return False                      # een andere worker was ons voor
    sess = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    pts = {c["id"]: c["points"] for c in db.execute("SELECT id, points FROM challenges")}
    members = db.execute("SELECT id, username FROM users WHERE team_id=? AND role='player'",
                         (sess["team_id"],)).fetchall()
    t0 = time.monotonic()
    failed = 0
    for m in members:
        solved, hinted, points = user_progress(db, m["id"], pts)
        db.execute("INSERT INTO session_results(session_id, user_id, username, points, solved, hints) "
                   "VALUES(?,?,?,?,?,?)",
                   (sid, m["id"], m["username"], points, json.dumps(solved), json.dumps(hinted)))
        envs = db.execute("SELECT challenge_id FROM environments WHERE user_id=? AND status!='stopped'",
                          (m["id"],)).fetchall()
        for env in envs:
            ok, dur = run_env_action("cleanup", env["challenge_id"], m["id"])
            log_action(db, "cleanup", env["challenge_id"], m["id"], sess["started_by"], dur, ok)
            if ok:
                db.execute("UPDATE environments SET status='stopped' WHERE user_id=? AND challenge_id=?",
                           (m["id"], env["challenge_id"]))
            else:
                failed += 1
    total = time.monotonic() - t0
    db.execute("UPDATE sessions SET status='ended', ended_at=?, cleanup_seconds=?, cleanup_failed=? WHERE id=?",
               (ts(), round(total, 2), failed, sid))
    bump(db)
    db.commit()
    return True

def purge_user(db, uid):
    """Verwijder een gebruiker en alles wat aan die gebruiker hangt (AVG)."""
    for env in db.execute("SELECT challenge_id FROM environments WHERE user_id=? AND status!='stopped'",
                          (uid,)).fetchall():
        ok, dur = run_env_action("stop", env["challenge_id"], uid)
        log_action(db, "stop", env["challenge_id"], uid, None, dur, ok)
    for table in ("solves", "hints_used", "environments", "session_results"):
        db.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
    db.execute("DELETE FROM users WHERE id=?", (uid,))

def maintenance(db):
    """Verlopen sessies opruimen en deelnemers na 90 dagen verwijderen. Mag vaak draaien."""
    closed = 0
    for s in db.execute("SELECT id FROM sessions WHERE status='active' AND ends_at<=?", (ts(),)).fetchall():
        if finalize_session(db, s["id"]):
            closed += 1
    cutoff = ts(utcnow() - timedelta(days=RETENTION_DAYS))
    old = db.execute("SELECT id FROM users WHERE role='player' AND created_at<?", (cutoff,)).fetchall()
    for u in old:
        purge_user(db, u["id"])
    if old:
        bump(db)
    db.commit()
    return {"sessions_closed": closed, "users_deleted": len(old)}

_maint_lock = threading.Lock()
_last_maint = 0.0

def _maint_job():
    if not _maint_lock.acquire(blocking=False):
        return
    try:
        db = connect()
        try:
            maintenance(db)
        finally:
            db.close()
    except Exception:
        app.logger.exception("maintenance failed")
    finally:
        _maint_lock.release()

@app.before_request
def _maintenance_hook():
    global _last_maint
    if app.config.get("TESTING") or request.path.startswith("/static"):
        return
    if time.time() - _last_maint >= 20:
        _last_maint = time.time()
        threading.Thread(target=_maint_job, daemon=True).start()

# ---------------------------------------------------------------- pagina

@app.route("/")
def index():
    css = os.path.join(BASE_DIR, "static", "style.css")
    v = int(os.path.getmtime(css)) if os.path.exists(css) else 0
    return render_template("index.html", css_v=v)

# ---------------------------------------------------------------- accounts

@app.post("/api/auth/register")
def register():
    d = body()
    username = (d.get("username") or "").strip()
    password = d.get("password") or ""
    if not NAME_RE.match(username):
        return jsonify(error="username: 3-30 characters (letters, numbers, . _ -)"), 400
    if len(password) < 6:
        return jsonify(error="password must be at least 6 characters"), 400
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)", (username,)).fetchone():
        return jsonify(error="username already exists"), 409
    db.execute("INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,'player',?)",
               (username, generate_password_hash(password), ts()))
    bump(db)
    db.commit()
    return jsonify(ok=True)

@app.post("/api/auth/login")
def login():
    d = body()
    username = (d.get("username") or "").strip()
    user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], d.get("password") or ""):
        return jsonify(error="invalid username or password"), 401
    session.clear()
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
    db = get_db()
    team = None
    sess = None
    if u["team_id"]:
        t = db.execute("SELECT id, name FROM teams WHERE id=?", (u["team_id"],)).fetchone()
        if t:
            team = {"id": t["id"], "name": t["name"]}
            s = active_session(db, t["id"])
            if s:
                sess = {"id": s["id"], "remaining": remaining(s)}
    return jsonify(username=u["username"], role=u["role"], team=team, session=sess)

# ---------------------------------------------------------------- live updates (SSE)

def event_stream(limit=None):
    """Stuurt een bericht zodra er iets verandert. Elke browser houdt één verbinding open."""
    yield "retry: 3000\n\n"
    last, quiet, tick = None, 0, 0
    while limit is None or tick < limit:
        tick += 1
        if tick % 5 == 0 and not app.config.get("TESTING"):
            _maint_job()
        db = connect()
        try:
            v = db.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0]
        finally:
            db.close()
        if v != last:
            last, quiet = v, 0
            yield f"data: {v}\n\n"
        else:
            quiet += 1
            if quiet >= 15:
                quiet = 0
                yield ": keepalive\n\n"
        if limit is None:
            time.sleep(1)

@app.get("/api/stream")
@login_required
def stream():
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ---------------------------------------------------------------- challenges (spelers)

def challenge_status(db, user_id, cid):
    if db.execute("SELECT 1 FROM solves WHERE user_id=? AND challenge_id=?", (user_id, cid)).fetchone():
        return "solved"
    env = db.execute("SELECT status FROM environments WHERE user_id=? AND challenge_id=?",
                     (user_id, cid)).fetchone()
    return env["status"] if env and env["status"] in ("active", "stopped") else "stopped"

@app.get("/api/challenges")
@login_required
def list_challenges():
    db = get_db()
    user = current_user()
    hints = {h["challenge_id"]: h for h in
             db.execute("SELECT * FROM hints_used WHERE user_id=?", (user["id"],)).fetchall()}
    envs = {e["challenge_id"]: e for e in
            db.execute("SELECT * FROM environments WHERE user_id=? AND status='active'", (user["id"],)).fetchall()}
    out = []
    for ch in db.execute("SELECT * FROM challenges ORDER BY level, id").fetchall():
        h = hints.get(ch["id"])
        env = envs.get(ch["id"])
        out.append({
            "id": ch["id"], "name": ch["name"], "category": ch["category"],
            "level": ch["level"], "level_name": LEVELS.get(ch["level"], "?"),
            "points": ch["points"], "description": ch["description"],
            "available": bool(ch["available"]),
            "status": challenge_status(db, user["id"], ch["id"]),
            "has_hint": bool(ch["hint"]),
            "hint_used": h is not None,
            "hint": ((h["custom_text"] or ch["hint"]) if h else None),
            "hint_penalty": HINT_PENALTY,
            "connection": ({"address": env["address"], "user": "student", "password": "Ijs8-kaBoom"}
                           if env else None),
        })
    return jsonify(out)

def env_up(cid, action):
    db = get_db()
    user = current_user()
    ch = db.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch or not ch["available"]:
        return jsonify(error="challenge not available"), 400
    ok, dur = run_env_action(action, cid, user["id"])
    log_action(db, action, cid, user["id"], user["id"], dur, ok)
    if not ok:
        db.commit()
        return jsonify(error="the environment could not be built, tell your game master"), 500
    address = f"10.10.{cid}.5"
    set_env(db, user["id"], cid, "active", address)
    bump(db)
    db.commit()
    return jsonify(ok=True, address=address, user="student", password="Ijs8-kaBoom",
                   seconds=round(dur, 1), within_limit=dur <= CLEANUP_LIMIT_SECONDS)

@app.post("/api/challenges/<int:cid>/start")
@login_required
def start_challenge(cid):
    return env_up(cid, "start")

@app.post("/api/challenges/<int:cid>/reset")
@login_required
def reset_challenge(cid):
    return env_up(cid, "reset")      # US24: één actie, tijd wordt gemeten en gelogd

@app.post("/api/challenges/<int:cid>/stop")
@login_required
def stop_challenge(cid):
    db = get_db()
    user = current_user()
    ok, dur = run_env_action("stop", cid, user["id"])
    log_action(db, "stop", cid, user["id"], user["id"], dur, ok)
    if ok:
        set_env(db, user["id"], cid, "stopped")
    bump(db)
    db.commit()
    return jsonify(ok=ok) if ok else (jsonify(error="could not stop the environment"), 500)

@app.post("/api/challenges/<int:cid>/submit")
@login_required
def submit_answer(cid):
    db = get_db()
    user = current_user()
    answer = (body().get("flag") or "").strip()
    ch = db.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch:
        return jsonify(error="unknown challenge"), 404
    if sha(answer) != ch["flag_hash"]:
        return jsonify(ok=False, error="incorrect answer")
    db.execute("INSERT OR IGNORE INTO solves(user_id, challenge_id) VALUES(?,?)", (user["id"], cid))
    bump(db)
    db.commit()
    return jsonify(ok=True, points=ch["points"])

@app.post("/api/challenges/<int:cid>/hint")
@login_required
def request_hint(cid):
    db = get_db()
    user = current_user()
    ch = db.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch:
        return jsonify(error="unknown challenge"), 404
    row = db.execute("SELECT * FROM hints_used WHERE user_id=? AND challenge_id=?", (user["id"], cid)).fetchone()
    if not row:
        if not ch["hint"]:
            return jsonify(error="no hint available for this challenge"), 400
        db.execute("INSERT INTO hints_used(user_id, challenge_id, source) VALUES(?,?,'self')", (user["id"], cid))
        bump(db)
        db.commit()
    text = (row["custom_text"] if row else None) or ch["hint"]
    return jsonify(ok=True, hint=text, penalty=HINT_PENALTY)

# ---------------------------------------------------------------- scoreboard

@app.get("/api/scoreboard")
@login_required
def scoreboard():
    db = get_db()
    pts = {c["id"]: c["points"] for c in db.execute("SELECT id, points FROM challenges")}
    rows = db.execute("SELECT u.id, u.username, u.team_id, t.name AS team FROM users u "
                      "LEFT JOIN teams t ON t.id=u.team_id WHERE u.role='player'").fetchall()
    players, by_team = [], {}
    for u in rows:
        solved, hinted, points = user_progress(db, u["id"], pts)
        # klaar = opgelost (groen); bezig = omgeving draait maar nog niet opgelost (oranje); rest = rood
        active = [r["challenge_id"] for r in db.execute(
            "SELECT challenge_id FROM environments WHERE user_id=? AND status='active' ORDER BY rowid", (u["id"],))]
        done = sorted(set(solved))
        busy = sorted(set(active) - set(solved))
        p = {"username": u["username"], "team": u["team"], "points": points, "done": done,
             "busy": busy, "hinted": sorted(set(hinted) & set(solved)), "hints_used": len(hinted),
             "current": active[-1] if active else None}
        players.append(p)
        by_team.setdefault(u["team_id"], []).append(p)
    teams = []
    for t in db.execute("SELECT * FROM teams ORDER BY name").fetchall():
        members = by_team.get(t["id"], [])
        done = set().union(*[set(m["done"]) for m in members]) if members else set()
        busy = (set().union(*[set(m["busy"]) for m in members]) if members else set()) - done
        s = active_session(db, t["id"])
        teams.append({"id": t["id"], "name": t["name"], "members": [m["username"] for m in members],
                      "points": sum(m["points"] for m in members),
                      "done": sorted(done), "busy": sorted(busy),
                      "remaining": remaining(s) if s else None})
    players.sort(key=lambda p: (-p["points"], p["username"].lower()))
    teams.sort(key=lambda t: (-t["points"], t["name"].lower()))
    return jsonify(players=players, teams=teams)

# ---------------------------------------------------------------- teams

def team_locked(db, team_id):
    return bool(team_id and active_session(db, team_id))

@app.get("/api/teams")
@login_required
def list_teams():
    db = get_db()
    out = []
    for t in db.execute("SELECT * FROM teams ORDER BY name").fetchall():
        members = [r["username"] for r in db.execute(
            "SELECT username FROM users WHERE team_id=? AND role='player' ORDER BY username", (t["id"],))]
        s = active_session(db, t["id"])
        out.append({"id": t["id"], "name": t["name"], "members": members,
                    "remaining": remaining(s) if s else None})
    return jsonify(out)

@app.post("/api/teams")
@login_required
def create_team():
    db = get_db()
    user = current_user()
    name = (body().get("name") or "").strip()
    if not TEAM_RE.match(name):
        return jsonify(error="team name: 2-40 characters (letters, numbers, space, . _ -)"), 400
    if db.execute("SELECT 1 FROM teams WHERE lower(name)=lower(?)", (name,)).fetchone():
        return jsonify(error="a team with this name already exists"), 409
    if user["role"] == "player" and team_locked(db, user["team_id"]):
        return jsonify(error="you cannot change team during an active session"), 400
    cur = db.execute("INSERT INTO teams(name, created_by, created_at) VALUES(?,?,?)", (name, user["id"], ts()))
    if user["role"] == "player":
        db.execute("UPDATE users SET team_id=? WHERE id=?", (cur.lastrowid, user["id"]))
    bump(db)
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid)

@app.post("/api/teams/<int:tid>/join")
@roles_required("player")
def join_team(tid):
    db = get_db()
    user = current_user()
    if not db.execute("SELECT 1 FROM teams WHERE id=?", (tid,)).fetchone():
        return jsonify(error="team not found"), 404
    if team_locked(db, user["team_id"]) or team_locked(db, tid):
        return jsonify(error="you cannot change team during an active session"), 400
    db.execute("UPDATE users SET team_id=? WHERE id=?", (tid, user["id"]))
    bump(db)
    db.commit()
    return jsonify(ok=True)

@app.post("/api/teams/leave")
@roles_required("player")
def leave_team():
    db = get_db()
    user = current_user()
    if team_locked(db, user["team_id"]):
        return jsonify(error="you cannot change team during an active session"), 400
    db.execute("UPDATE users SET team_id=NULL WHERE id=?", (user["id"],))
    bump(db)
    db.commit()
    return jsonify(ok=True)

@app.delete("/api/teams/<int:tid>")
@gm_required
def delete_team(tid):
    db = get_db()
    s = active_session(db, tid)
    if s:
        finalize_session(db, s["id"])
    db.execute("UPDATE users SET team_id=NULL WHERE team_id=?", (tid,))
    db.execute("DELETE FROM teams WHERE id=?", (tid,))
    bump(db)
    db.commit()
    return jsonify(ok=True)

# ---------------------------------------------------------------- spelleider

@app.get("/api/gm/players")
@gm_required
def gm_players():
    rows = get_db().execute("SELECT u.id, u.username, u.team_id, t.name AS team FROM users u "
                            "LEFT JOIN teams t ON t.id=u.team_id WHERE u.role='player' "
                            "ORDER BY u.username COLLATE NOCASE").fetchall()
    return jsonify([dict(r) for r in rows])

@app.put("/api/gm/users/<int:uid>/team")
@gm_required
def gm_assign_team(uid):
    db = get_db()
    tid = body().get("team_id")
    u = db.execute("SELECT * FROM users WHERE id=? AND role='player'", (uid,)).fetchone()
    if not u:
        return jsonify(error="player not found"), 404
    if tid in (None, ""):
        tid = None
    else:
        tid = int(tid)
        if not db.execute("SELECT 1 FROM teams WHERE id=?", (tid,)).fetchone():
            return jsonify(error="team not found"), 404
    db.execute("UPDATE users SET team_id=? WHERE id=?", (tid, uid))
    bump(db)
    db.commit()
    return jsonify(ok=True)

def session_row(r):
    return {"id": r["id"], "team": r["team"] or "(deleted team)", "team_id": r["team_id"],
            "status": r["status"], "started_at": r["started_at"], "ends_at": r["ends_at"],
            "ended_at": r["ended_at"], "cleanup_seconds": r["cleanup_seconds"],
            "cleanup_failed": r["cleanup_failed"] or 0,
            "within_limit": (r["cleanup_seconds"] <= CLEANUP_LIMIT_SECONDS) if r["cleanup_seconds"] is not None else None,
            "remaining": max(0, int((parse_ts(r["ends_at"]) - utcnow()).total_seconds())) if r["status"] == "active" else None}

@app.get("/api/gm/sessions")
@gm_required
def gm_sessions():
    rows = get_db().execute("SELECT s.*, t.name AS team FROM sessions s LEFT JOIN teams t ON t.id=s.team_id "
                            "ORDER BY s.id DESC LIMIT 40").fetchall()
    return jsonify([session_row(r) for r in rows])

@app.post("/api/gm/sessions")
@gm_required
def gm_start_session():
    db = get_db()
    user = current_user()
    d = body()
    try:
        tid = int(d.get("team_id"))
        minutes = int(d.get("minutes", 60))
    except (TypeError, ValueError):
        return jsonify(error="choose a team and a duration in minutes"), 400
    if not 5 <= minutes <= 480:
        return jsonify(error="duration must be between 5 and 480 minutes"), 400
    if not db.execute("SELECT 1 FROM teams WHERE id=?", (tid,)).fetchone():
        return jsonify(error="team not found"), 404
    if active_session(db, tid):
        return jsonify(error="this team already has an active session"), 409
    now = utcnow()
    cur = db.execute("INSERT INTO sessions(team_id, started_by, started_at, ends_at) VALUES(?,?,?,?)",
                     (tid, user["id"], ts(now), ts(now + timedelta(minutes=minutes))))
    bump(db)
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid)

@app.post("/api/gm/sessions/<int:sid>/end")
@gm_required
def gm_end_session(sid):
    db = get_db()
    s = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        return jsonify(error="session not found"), 404
    if s["status"] != "active":
        return jsonify(error="session is already ended"), 400
    finalize_session(db, sid)
    s = db.execute("SELECT s.*, t.name AS team FROM sessions s LEFT JOIN teams t ON t.id=s.team_id WHERE s.id=?",
                   (sid,)).fetchone()
    return jsonify(ok=True, session=session_row(s))

@app.post("/api/gm/sessions/<int:sid>/retry-cleanup")
@gm_required
def gm_retry_cleanup(sid):
    """US37: opruimen opnieuw proberen voor omgevingen die na afloop van een sessie nog niet schoon zijn."""
    db = get_db()
    actor = current_user()
    s = db.execute("SELECT * FROM sessions WHERE id=? AND status='ended'", (sid,)).fetchone()
    if not s:
        return jsonify(error="session not found or not ended yet"), 404
    if active_session(db, s["team_id"]):
        return jsonify(error="this team has a new session running, end that one first"), 400
    failed, cleaned = 0, 0
    for m in team_members(db, s["team_id"]):
        for env in db.execute("SELECT challenge_id FROM environments WHERE user_id=? AND status!='stopped'",
                              (m["id"],)).fetchall():
            ok, dur = run_env_action("cleanup", env["challenge_id"], m["id"])
            log_action(db, "cleanup", env["challenge_id"], m["id"], actor["id"], dur, ok)
            if ok:
                set_env(db, m["id"], env["challenge_id"], "stopped")
                cleaned += 1
            else:
                failed += 1
    db.execute("UPDATE sessions SET cleanup_failed=? WHERE id=?", (failed, sid))
    bump(db)
    db.commit()
    return jsonify(ok=failed == 0, cleaned=cleaned, failed=failed)

@app.post("/api/gm/sessions/<int:sid>/extend")
@gm_required
def gm_extend_session(sid):
    db = get_db()
    s = db.execute("SELECT * FROM sessions WHERE id=? AND status='active'", (sid,)).fetchone()
    if not s:
        return jsonify(error="no active session found"), 404
    try:
        minutes = int(body().get("minutes"))
    except (TypeError, ValueError):
        return jsonify(error="minutes required"), 400
    new_end = parse_ts(s["ends_at"]) + timedelta(minutes=minutes)
    if not -120 <= minutes <= 240 or new_end < utcnow() + timedelta(seconds=60):
        return jsonify(error="the session must keep at least 1 minute left (max +240 / -120 minutes at a time)"), 400
    db.execute("UPDATE sessions SET ends_at=? WHERE id=?", (ts(new_end), sid))
    bump(db)
    db.commit()
    return jsonify(ok=True)

def safe_cell(v):
    v = str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v

@app.get("/api/gm/sessions/<int:sid>/export")
@gm_required
def gm_export_session(sid):
    db = get_db()
    s = db.execute("SELECT s.*, t.name AS team FROM sessions s LEFT JOIN teams t ON t.id=s.team_id WHERE s.id=?",
                   (sid,)).fetchone()
    if not s or s["status"] != "ended":
        return jsonify(error="results are available once the session has ended"), 404
    names = {c["id"]: c["name"] for c in db.execute("SELECT id, name FROM challenges")}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["session", "team", "player", "points", "solved challenges", "hints used"])
    for r in db.execute("SELECT * FROM session_results WHERE session_id=? ORDER BY points DESC", (sid,)):
        solved = ", ".join(names.get(i, f"#{i}") for i in json.loads(r["solved"]))
        hints = ", ".join(names.get(i, f"#{i}") for i in json.loads(r["hints"]))
        w.writerow([sid, safe_cell(s["team"] or ""), safe_cell(r["username"]), r["points"],
                    safe_cell(solved), safe_cell(hints)])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=session-{sid}.csv"})

def team_members(db, tid):
    return db.execute("SELECT id, username FROM users WHERE team_id=? AND role='player'", (tid,)).fetchall()

@app.post("/api/gm/teams/<int:tid>/hint")
@gm_required
def gm_team_hint(tid):
    """US32: stuur een hint naar alle leden van een team. Dit kost de spelers geen punten."""
    db = get_db()
    d = body()
    try:
        cid = int(d.get("challenge_id"))
    except (TypeError, ValueError):
        return jsonify(error="choose a challenge"), 400
    text = (d.get("text") or "").strip()[:500] or None
    ch = db.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch:
        return jsonify(error="challenge not found"), 404
    if not text and not ch["hint"]:
        return jsonify(error="this challenge has no stored hint, type a hint text"), 400
    members = team_members(db, tid)
    if not members:
        return jsonify(error="this team has no members"), 400
    for m in members:
        db.execute("INSERT INTO hints_used(user_id, challenge_id, source, custom_text) VALUES(?,?,'gm',?) "
                   "ON CONFLICT(user_id, challenge_id) DO UPDATE SET custom_text=excluded.custom_text",
                   (m["id"], cid, text))
    bump(db)
    db.commit()
    return jsonify(ok=True, sent_to=len(members))

@app.post("/api/gm/teams/<int:tid>/reset")
@gm_required
def gm_team_reset(tid):
    """US32/US24: zet een vastgelopen challenge terug voor het hele team (tijd wordt gelogd)."""
    db = get_db()
    actor = current_user()
    try:
        cid = int(body().get("challenge_id"))
    except (TypeError, ValueError):
        return jsonify(error="choose a challenge"), 400
    if not db.execute("SELECT 1 FROM challenges WHERE id=?", (cid,)).fetchone():
        return jsonify(error="challenge not found"), 404
    done, failed, total = 0, 0, 0.0
    for m in team_members(db, tid):
        env = db.execute("SELECT status FROM environments WHERE user_id=? AND challenge_id=? AND status!='stopped'",
                         (m["id"], cid)).fetchone()
        if not env:
            continue
        ok, dur = run_env_action("reset", cid, m["id"])
        log_action(db, "reset", cid, m["id"], actor["id"], dur, ok)
        total += dur
        if ok:
            set_env(db, m["id"], cid, "active")
            done += 1
        else:
            failed += 1
    if not done and not failed:
        return jsonify(error="no member of this team has this challenge running"), 400
    bump(db)
    db.commit()
    return jsonify(ok=failed == 0, reset=done, failed=failed, seconds=round(total, 1))

# ---------------------------------------------------------------- admin: challenges

@app.get("/api/admin/challenges")
@admin_required
def admin_list_challenges():
    rows = get_db().execute("SELECT * FROM challenges ORDER BY level, id").fetchall()
    return jsonify([{"id": r["id"], "name": r["name"], "category": r["category"], "level": r["level"],
                     "level_name": LEVELS.get(r["level"], "?"), "points": r["points"],
                     "description": r["description"], "available": bool(r["available"]),
                     "hint": r["hint"]} for r in rows])

@app.post("/api/admin/challenges")
@admin_required
def admin_add_challenge():
    d = body()
    required = ["name", "category", "level", "points", "description", "flag"]
    if any(not str(d.get(k, "")).strip() for k in required):
        return jsonify(error=f"all fields are required: {', '.join(required)}"), 400
    try:
        level, points = int(d["level"]), int(d["points"])
    except (TypeError, ValueError):
        return jsonify(error="level and points must be numbers"), 400
    if level not in LEVELS or points < 0:
        return jsonify(error="level must be 1, 2 or 3"), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO challenges(name, category, level, points, description, flag_hash, available, hint) "
        "VALUES(?,?,?,?,?,?,1,?)",
        (d["name"].strip(), d["category"].strip(), level, points, d["description"].strip(),
         sha(d["flag"]), (d.get("hint") or "").strip() or None))
    bump(db)
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid)

@app.put("/api/admin/challenges/<int:cid>")
@admin_required
def admin_edit_challenge(cid):
    d = body()
    db = get_db()
    ch = db.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
    if not ch:
        return jsonify(error="not found"), 404
    flag_hash = sha(d["flag"]) if d.get("flag") else ch["flag_hash"]
    try:
        level, points = int(d.get("level", ch["level"])), int(d.get("points", ch["points"]))
    except (TypeError, ValueError):
        return jsonify(error="level and points must be numbers"), 400
    db.execute("UPDATE challenges SET name=?, category=?, level=?, points=?, description=?, flag_hash=?, hint=? WHERE id=?",
               (d.get("name", ch["name"]), d.get("category", ch["category"]), level, points,
                d.get("description", ch["description"]), flag_hash, d.get("hint", ch["hint"]), cid))
    bump(db)
    db.commit()
    return jsonify(ok=True)

@app.delete("/api/admin/challenges/<int:cid>")
@admin_required
def admin_delete_challenge(cid):
    db = get_db()
    for table in ("environments", "solves", "hints_used"):
        db.execute(f"DELETE FROM {table} WHERE challenge_id=?", (cid,))
    db.execute("DELETE FROM challenges WHERE id=?", (cid,))
    bump(db)
    db.commit()
    return jsonify(ok=True)

@app.post("/api/admin/challenges/<int:cid>/toggle")
@admin_required
def admin_toggle_challenge(cid):
    db = get_db()
    db.execute("UPDATE challenges SET available = 1 - available WHERE id=?", (cid,))
    bump(db)
    db.commit()
    return jsonify(ok=True)

# ---------------------------------------------------------------- admin: gebruikers & log

@app.get("/api/admin/users")
@admin_required
def admin_users():
    rows = get_db().execute("SELECT u.*, t.name AS team FROM users u LEFT JOIN teams t ON t.id=u.team_id "
                            "ORDER BY u.role, u.username COLLATE NOCASE").fetchall()
    out = []
    for r in rows:
        days_left = None
        if r["role"] == "player" and r["created_at"]:
            age = (utcnow() - parse_ts(r["created_at"])).days
            days_left = max(0, RETENTION_DAYS - age)
        out.append({"id": r["id"], "username": r["username"], "role": r["role"], "team": r["team"],
                    "created_at": r["created_at"], "days_left": days_left})
    return jsonify(out)

@app.put("/api/admin/users/<int:uid>/role")
@admin_required
def admin_set_role(uid):
    db = get_db()
    role = body().get("role")
    if role not in ROLES:
        return jsonify(error="role must be player, gamemaster or admin"), 400
    if uid == current_user()["id"]:
        return jsonify(error="you cannot change your own role"), 400
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return jsonify(error="user not found"), 404
    if role == "player":
        # nieuwe deelnemer-termijn, anders zou een oud account direct door de 90-dagen-regel verdwijnen
        db.execute("UPDATE users SET role=?, created_at=? WHERE id=?", (role, ts(), uid))
    else:
        db.execute("UPDATE users SET role=?, team_id=NULL WHERE id=?", (role, uid))
    bump(db)
    db.commit()
    return jsonify(ok=True)

@app.delete("/api/admin/users/<int:uid>")
@admin_required
def admin_delete_user(uid):
    db = get_db()
    if uid == current_user()["id"]:
        return jsonify(error="you cannot delete yourself"), 400
    if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        return jsonify(error="user not found"), 404
    purge_user(db, uid)
    bump(db)
    db.commit()
    return jsonify(ok=True)

@app.get("/api/admin/log")
@admin_required
def admin_log():
    rows = get_db().execute(
        "SELECT l.*, c.name AS challenge, u.username FROM action_log l "
        "LEFT JOIN challenges c ON c.id=l.challenge_id LEFT JOIN users u ON u.id=l.user_id "
        "ORDER BY l.id DESC LIMIT 60").fetchall()
    return jsonify([{"ts": r["ts"], "action": r["action"], "challenge": r["challenge"] or "-",
                     "username": r["username"] or "-", "duration": r["duration"], "ok": bool(r["ok"]),
                     "within_limit": None if r["within_limit"] is None else bool(r["within_limit"])}
                    for r in rows])

# ---------------------------------------------------------------- start

init_db()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "maintenance":
        conn = connect()
        print(maintenance(conn))
        conn.close()
    else:
        app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
