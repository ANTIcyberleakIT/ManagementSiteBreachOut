import os
REPO = os.environ.get('REPO') or os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import os, sys, sqlite3, tempfile, subprocess, json
from datetime import timedelta
tmp = tempfile.mkdtemp()
os.environ["BREACHOUT_DB"] = os.path.join(tmp, "t2.db")
sys.path.insert(0, REPO)
import app as A
A.app.config["TESTING"] = True
failed = 0
def check(n, c, extra=""):
    global failed
    print(("PASS " if c else "FAIL ") + n + ("" if c else " " + str(extra)))
    failed += (not c)

def mk(name, role=None):
    c = A.app.test_client()
    c.post("/api/auth/register", json={"username": name, "password": "secret1"})
    c.post("/api/auth/login", json={"username": name, "password": "secret1"})
    return c
admin = A.app.test_client(); admin.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
p1, gm = mk("alice"), mk("kevin")
uid = {u["username"]: u["id"] for u in admin.get("/api/admin/users").get_json()}
admin.put(f"/api/admin/users/{uid['kevin']}/role", json={"role": "gamemaster"})
tid = p1.post("/api/teams", json={"name": "Green"}).get_json()["id"]
sid = gm.post("/api/gm/sessions", json={"team_id": tid, "minutes": 20}).get_json()["id"]
check("export van actieve sessie -> 404", gm.get(f"/api/gm/sessions/{sid}/export").status_code == 404)
p1.post("/api/challenges/1/start")
r = gm.delete(f"/api/teams/{tid}")
check("gm verwijdert team met actieve sessie", r.status_code == 200, r.get_json())
conn = A.connect()
check("sessie is afgesloten (omgevingen opgeruimd)", conn.execute("SELECT status FROM sessions WHERE id=?", (sid,)).fetchone()[0] == "ended")
check("omgeving van lid gestopt", conn.execute("SELECT status FROM environments WHERE user_id=?", (uid["alice"],)).fetchone()[0] == "stopped")
check("leden zijn los van team", conn.execute("SELECT team_id FROM users WHERE username='alice'").fetchone()[0] is None)
check("sessielijst toont verwijderd team", gm.get("/api/gm/sessions").get_json()[0]["team"] == "(deleted team)")
check("player mag team niet verwijderen", p1.delete("/api/teams/1").status_code == 403)
tid2 = p1.post("/api/teams", json={"name": "Yellow"}).get_json()["id"]
check("gm wijst speler toe aan team", gm.put(f"/api/gm/users/{uid['alice']}/team", json={"team_id": tid2}).status_code == 200)
check("gm haalt speler uit team", gm.put(f"/api/gm/users/{uid['alice']}/team", json={"team_id": None}).status_code == 200)
check("toewijzen aan onbekend team -> 404", gm.put(f"/api/gm/users/{uid['alice']}/team", json={"team_id": 999}).status_code == 404)
check("csv-injectie wordt ontschoten", A.safe_cell("=1+1") == "'=1+1" and A.safe_cell("alice") == "alice")

# --- migratie vanaf de OUDE database (schema van vóór teams/sessies/hints-bron)
old = os.path.join(tmp, "old.db"); db = sqlite3.connect(old)
db.executescript("""
CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'player');
CREATE TABLE challenges(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL, level INTEGER NOT NULL, points INTEGER NOT NULL, description TEXT NOT NULL, flag_hash TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1);
CREATE TABLE environments(user_id INTEGER NOT NULL, challenge_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'stopped', address TEXT, PRIMARY KEY(user_id, challenge_id));
CREATE TABLE solves(user_id INTEGER NOT NULL, challenge_id INTEGER NOT NULL, solved_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id, challenge_id));
CREATE TABLE hints_used(user_id INTEGER NOT NULL, challenge_id INTEGER NOT NULL, requested_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_id, challenge_id));
""")
from werkzeug.security import generate_password_hash
db.execute("INSERT INTO users(username,password_hash,role) VALUES('admin',?, 'admin')", (generate_password_hash("mijnwachtwoord"),))
db.execute("INSERT INTO users(username,password_hash,role) VALUES('oudespeler',?, 'player')", (generate_password_hash("secret1"),))
db.execute("INSERT INTO challenges(name,category,level,points,description,flag_hash) VALUES('Eigen','Web',1,100,'d','x')")
db.execute("INSERT INTO hints_used(user_id,challenge_id) VALUES(2,1)")
db.commit(); db.close()
A.DB_PATH = old; A.init_db()
db = A.connect()
check("migratie: admin-wachtwoord blijft behouden", A.check_password_hash(db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0], "mijnwachtwoord"))
check("migratie: eigen challenge blijft, geen dubbele seed", db.execute("SELECT COUNT(*) FROM challenges").fetchone()[0] == 1)
check("migratie: oude hint = 'self' (straf blijft gelden)", db.execute("SELECT source FROM hints_used").fetchone()[0] == "self")
check("migratie: bestaande spelers krijgen 90-dagen-termijn", db.execute("SELECT created_at FROM users WHERE username='oudespeler'").fetchone()[0] is not None)
A.init_db(); check("init_db twee keer draaien is veilig", db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2)

# --- twee workers starten tegelijk op een lege database
par = os.path.join(tmp, "par.db")
code = f"import sys; sys.path.insert(0,{REPO!r}); import app"
procs = [subprocess.Popen([sys.executable, "-c", code], env={**os.environ, "BREACHOUT_DB": par}, stderr=subprocess.PIPE) for _ in range(6)]
errs = [p.communicate()[1].decode() for p in procs]
d = sqlite3.connect(par)
check("6 gelijktijdige workers: geen fouten", all(e == "" for e in errs), [e for e in errs if e][:1])
check("6 gelijktijdige workers: precies 10 challenges", d.execute("SELECT COUNT(*) FROM challenges").fetchone()[0] == 10)
check("6 gelijktijdige workers: precies 1 admin", d.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1)

# --- maintenance via CLI
r = subprocess.run([sys.executable, os.path.join(REPO, "app.py"), "maintenance"], env={**os.environ, "BREACHOUT_DB": par}, capture_output=True, text=True)
check("CLI 'app.py maintenance' werkt", "sessions_closed" in r.stdout, (r.stdout, r.stderr[-200:]))
print("\nGEFAAALD" if failed else "\nALLES GESLAAGD")
sys.exit(1 if failed else 0)
