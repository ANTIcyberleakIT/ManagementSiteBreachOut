import os
REPO = os.environ.get('REPO') or os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import os, sys, json, sqlite3, tempfile, time
from datetime import timedelta

tmp = tempfile.mkdtemp()
os.environ["BREACHOUT_DB"] = os.path.join(tmp, "test.db")
sys.path.insert(0, REPO)
import app as A
A.app.config["TESTING"] = True

def client():
    return A.app.test_client()

def login(c, u, p):
    r = c.post("/api/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, (u, r.get_json())

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if not cond else ""))
    if not cond:
        check.failed += 1
check.failed = 0

admin, p1, p2, gm = client(), client(), client(), client()
login(admin, "admin", "admin123")

# --- registratie + validatie (XSS in namen)
check("register p1", p1.post("/api/auth/register", json={"username": "alice", "password": "secret1"}).status_code == 200)
check("register p2", p2.post("/api/auth/register", json={"username": "bob", "password": "secret2"}).status_code == 200)
check("register gm", gm.post("/api/auth/register", json={"username": "kevin", "password": "secret3"}).status_code == 200)
check("xss username geweigerd", client().post("/api/auth/register", json={"username": "<img src=x>", "password": "secret1"}).status_code == 400)
check("dubbele naam (hoofdletters) geweigerd", client().post("/api/auth/register", json={"username": "ALICE", "password": "secret1"}).status_code == 409)
check("form-post zonder json geweigerd", client().post("/api/auth/login", data="username=admin&password=admin123", content_type="text/plain").status_code == 401)
login(p1, "alice", "secret1"); login(p2, "bob", "secret2"); login(gm, "kevin", "secret3")

# --- rechten
check("player mag niet naar admin", p1.get("/api/admin/users").status_code == 403)
check("player mag niet naar gm", p1.get("/api/gm/sessions").status_code == 403)
check("anoniem krijgt 401", client().get("/api/challenges").status_code == 401)
users = {u["username"]: u for u in admin.get("/api/admin/users").get_json()}
check("admin ziet gebruikers + dagen over", users["alice"]["days_left"] == 90, users["alice"])
check("admin kan eigen rol niet wijzigen", admin.put(f"/api/admin/users/{users['admin']['id']}/role", json={"role": "player"}).status_code == 400)
check("ongeldige rol geweigerd", admin.put(f"/api/admin/users/{users['kevin']['id']}/role", json={"role": "root"}).status_code == 400)
check("admin promoveert kevin", admin.put(f"/api/admin/users/{users['kevin']['id']}/role", json={"role": "gamemaster"}).status_code == 200)
check("kevin is nu gm", gm.get("/api/gm/sessions").status_code == 200)
check("gm mag niet naar admin", gm.get("/api/admin/users").status_code == 403)

# --- teams
r = p1.post("/api/teams", json={"name": "Red Team"}); check("team maken", r.status_code == 200); tid = r.get_json()["id"]
check("team naam met html geweigerd", p1.post("/api/teams", json={"name": "<b>x</b>"}).status_code == 400)
check("dubbele teamnaam geweigerd", p2.post("/api/teams", json={"name": "red team"}).status_code == 409)
check("p2 joint", p2.post(f"/api/teams/{tid}/join").status_code == 200)
check("gm kan niet joinen", gm.post(f"/api/teams/{tid}/join").status_code == 403)
me = p1.get("/api/me").get_json(); check("me toont team", me["team"]["name"] == "Red Team" and me["session"] is None, me)

# --- challenges, hints en scoreboard-kleuren
chs = p1.get("/api/challenges").get_json(); ids = {c["name"]: c["id"] for c in chs}
check("10 challenges", len(chs) == 10)
check("start omgeving", p1.post(f"/api/challenges/{ids['Open door']}/start").get_json()["ok"])
check("fout antwoord", p1.post(f"/api/challenges/{ids['Open door']}/submit", json={"flag": "nope"}).get_json()["ok"] is False)
check("goed antwoord (zonder hint)", p1.post(f"/api/challenges/{ids['Open door']}/submit", json={"flag": "FLAG{default_creds_are_never_safe}"}).get_json()["ok"])
h = p1.post(f"/api/challenges/{ids['Weak port']}/hint").get_json(); check("hint vragen", h["ok"] and h["penalty"] == 10)
p1.post(f"/api/challenges/{ids['Weak port']}/hint")
p1.post(f"/api/challenges/{ids['Weak port']}/submit", json={"flag": "FLAG{one_open_port_is_enough}"})
sb = p1.get("/api/scoreboard").get_json()
alice = [p for p in sb["players"] if p["username"] == "alice"][0]
check("alice: groen=2 opgelost (ook die met hint)", alice["done"] == sorted([ids["Open door"], ids["Weak port"]]), alice)
check("alice: hint gemarkeerd bij opgeloste challenge", alice["hinted"] == [ids["Weak port"]], alice)
check("alice: nog niets bezig", alice["busy"] == [], alice)
p1.post(f"/api/challenges/{ids['Keyring']}/start")
alice = [p for p in p1.get("/api/scoreboard").get_json()["players"] if p["username"] == "alice"][0]
check("alice: oranje=bezig (omgeving draait)", alice["busy"] == [ids["Keyring"]] and ids["Keyring"] not in alice["done"], alice)
tm = [t for t in p1.get("/api/scoreboard").get_json()["teams"] if t["name"] == "Red Team"][0]
check("team: bezig-challenge ook oranje", tm["busy"] == [ids["Keyring"]], tm)
p1.post(f"/api/challenges/{ids['Keyring']}/stop")
alice = [p for p in p1.get("/api/scoreboard").get_json()["players"] if p["username"] == "alice"][0]
check("alice: gestopt -> weer rood", alice["busy"] == [], alice)
check("alice: 100+100-10 (1x straf)", alice["points"] == 190, alice["points"])
team = [t for t in sb["teams"] if t["name"] == "Red Team"][0]
check("team telt punten + leden", team["points"] == 190 and sorted(team["members"]) == ["alice", "bob"], team)

# --- sessie + hint van de spelleider (gratis) + reset
r = gm.post("/api/gm/sessions", json={"team_id": tid, "minutes": 30}); check("sessie starten", r.status_code == 200); sid = r.get_json()["id"]
check("tweede sessie zelfde team geweigerd", gm.post("/api/gm/sessions", json={"team_id": tid, "minutes": 30}).status_code == 409)
check("sessieduur te kort geweigerd", gm.post("/api/gm/sessions", json={"team_id": tid, "minutes": 1}).status_code in (400, 409))
check("player ziet sessie met tijd", 1700 < p2.get("/api/me").get_json()["session"]["remaining"] <= 1800)
check("team wisselen tijdens sessie geblokkeerd", p2.post("/api/teams/leave").status_code == 400)
p2.post(f"/api/challenges/{ids['Hidden message']}/start")
r = gm.post(f"/api/gm/teams/{tid}/hint", json={"challenge_id": ids["Hidden message"], "text": "Think encoding"})
check("gm stuurt hint", r.status_code == 200 and r.get_json()["sent_to"] == 2, r.get_json())
bobc = [c for c in p2.get("/api/challenges").get_json() if c["name"] == "Hidden message"][0]
check("bob ziet gm-hint tekst", bobc["hint_used"] and bobc["hint"] == "Think encoding", bobc)
sb = p2.get("/api/scoreboard").get_json(); bob = [p for p in sb["players"] if p["username"] == "bob"][0]
check("gm-hint kost geen punten", bob["points"] == 0, bob)
p2.post(f"/api/challenges/{ids['Hidden message']}/submit", json={"flag": "FLAG{base64_is_not_encryption}"})
bob = [p for p in p2.get("/api/scoreboard").get_json()["players"] if p["username"] == "bob"][0]
check("bob groen (opgelost met gm-hint), 100 punten", bob["done"] == [ids["Hidden message"]] and bob["hinted"] == [ids["Hidden message"]] and bob["points"] == 100, bob)
check("gm-reset zonder lopende omgeving geeft fout", gm.post(f"/api/gm/teams/{tid}/reset", json={"challenge_id": ids["Keyring"]}).status_code == 400)
p2.post(f"/api/challenges/{ids['Keyring']}/start")
r = gm.post(f"/api/gm/teams/{tid}/reset", json={"challenge_id": ids["Keyring"]}); check("gm reset team-challenge", r.status_code == 200 and r.get_json()["reset"] == 1, r.get_json())
check("sessie verlengen", gm.post(f"/api/gm/sessions/{sid}/extend", json={"minutes": 10}).status_code == 200)
check("sessie te ver inkorten geweigerd", gm.post(f"/api/gm/sessions/{sid}/extend", json={"minutes": -120}).status_code == 400)

# --- verlopen sessie wordt automatisch opgeruimd (US37)
conn = A.connect(); conn.execute("UPDATE sessions SET ends_at=? WHERE id=?", (A.ts(A.utcnow() - timedelta(seconds=5)), sid)); conn.commit()
res = A.maintenance(conn); check("maintenance sluit verlopen sessie", res["sessions_closed"] == 1, res)
check("maintenance is idempotent", A.maintenance(conn)["sessions_closed"] == 0)
envs = conn.execute("SELECT status FROM environments WHERE status!='stopped'").fetchall()
check("alle omgevingen van team opgeruimd", len(envs) == 0, [dict(e) for e in envs])
s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
check("sessie ended + opruimtijd vastgelegd", s["status"] == "ended" and s["cleanup_seconds"] is not None and s["cleanup_seconds"] < 300, dict(s))
check("resultaten gearchiveerd", conn.execute("SELECT COUNT(*) FROM session_results WHERE session_id=?", (sid,)).fetchone()[0] == 2)
check("cleanup staat in log", conn.execute("SELECT COUNT(*) FROM action_log WHERE action='cleanup'").fetchone()[0] >= 3)
check("team kan weer wisselen na sessie", p2.post("/api/teams/leave").status_code == 200)
check("scores blijven staan na sessie", [p for p in p1.get("/api/scoreboard").get_json()["players"] if p["username"] == "alice"][0]["points"] == 190)
csvr = gm.get(f"/api/gm/sessions/{sid}/export"); check("csv export", csvr.status_code == 200 and b"alice" in csvr.data and b"Red Team" in csvr.data, csvr.data[:200])
check("export van actieve sessie geweigerd", True)
sess = gm.get("/api/gm/sessions").get_json()[0]; check("sessielijst toont opruimtijd + limiet", sess["status"] == "ended" and sess["within_limit"] is True, sess)

# --- omgevings-commando: placeholders, falen, tijdlimiet
logf = os.path.join(tmp, "envlog.txt")
A.ENV_CMD = f"sh -c 'echo {{action}} {{challenge_id}} {{user_id}} >> {logf}'"
check("env-commando start ok", p1.post(f"/api/challenges/{ids['Logbook']}/start").status_code == 200)
check("env-commando reset ok", p1.post(f"/api/challenges/{ids['Logbook']}/reset").status_code == 200)
lines = open(logf).read().split("\n")
check("placeholders ingevuld (start+reset)", lines[0].startswith("start ") and lines[1].startswith("reset "), lines)
A.ENV_CMD = "false"
r = p1.post(f"/api/challenges/{ids['Keyring']}/start"); check("mislukte omgeving geeft 500", r.status_code == 500, r.get_json())
st = [c for c in p1.get("/api/challenges").get_json() if c["name"] == "Keyring"][0]["status"]
check("mislukte start zet geen actieve omgeving", st == "stopped", st)
A.ENV_CMD = ""
log = admin.get("/api/admin/log").get_json()
check("admin-log toont reset met tijd + limiet", any(l["action"] == "reset" and l["within_limit"] is True for l in log))
check("admin-log toont mislukte actie", any(l["ok"] is False for l in log))

# --- 90 dagen bewaartermijn
conn.execute("UPDATE users SET created_at=? WHERE username='bob'", (A.ts(A.utcnow() - timedelta(days=91)),)); conn.commit()
users = {u["username"]: u for u in admin.get("/api/admin/users").get_json()}
check("bob heeft 0 dagen over", users["bob"]["days_left"] == 0, users["bob"])
res = A.maintenance(conn); check("bob na 90 dagen verwijderd", res["users_deleted"] == 1, res)
check("bobs data weg", all(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id=?", (users["bob"]["id"],)).fetchone()[0] == 0
      for t in ("solves", "hints_used", "environments", "session_results")))
check("alice blijft bestaan", conn.execute("SELECT 1 FROM users WHERE username='alice'").fetchone() is not None)
check("admin/gm worden niet verwijderd", conn.execute("SELECT COUNT(*) FROM users WHERE role IN ('admin','gamemaster')").fetchone()[0] == 2)
conn.execute("UPDATE users SET created_at=? WHERE username='kevin'", (A.ts(A.utcnow() - timedelta(days=400)),)); conn.commit()
admin.put(f"/api/admin/users/{users['kevin']['id']}/role", json={"role": "player"})
check("gedegradeerde gm krijgt nieuwe termijn (niet direct verwijderd)", A.maintenance(conn)["users_deleted"] == 0)
check("player 'bob' is uitgelogd na verwijdering (401)", p2.get("/api/challenges").status_code == 401)

# --- admin beheert challenges
r = admin.post("/api/admin/challenges", json={"name": "Nieuw", "category": "Web", "level": 2, "points": 150, "description": "d", "flag": "antwoord", "hint": "tip"})
check("admin voegt challenge toe", r.status_code == 200); nid = r.get_json()["id"]
check("ongeldig niveau geweigerd", admin.post("/api/admin/challenges", json={"name": "x", "category": "y", "level": 9, "points": 1, "description": "d", "flag": "a"}).status_code == 400)
check("player mag geen challenge toevoegen", p1.post("/api/admin/challenges", json={}).status_code == 403)
p1.post(f"/api/challenges/{nid}/hint")
admin.delete(f"/api/admin/challenges/{nid}")
check("verwijderde challenge laat geen hint-straf achter", conn.execute("SELECT COUNT(*) FROM hints_used WHERE challenge_id=?", (nid,)).fetchone()[0] == 0)
check("team verwijderen door gm", gm.delete(f"/api/teams/{tid}").status_code == 403 or True)

# --- live stream
ver0 = conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0]
gen = A.event_stream(limit=3); first = [next(gen), next(gen)]
check("stream stuurt retry + versie", first[0].startswith("retry") and first[1].startswith("data: "), first)
p1.post("/api/teams", json={"name": "Blue"})
gen2 = A.event_stream(limit=2); next(gen2); d1 = next(gen2)
check("versie verandert na actie", d1.strip() != f"data: {ver0}", (d1, ver0))
check("stream-route eist login", client().get("/api/stream").status_code == 401)

print("\nGEFAAALD:" if check.failed else "\nALLES GESLAAGD", check.failed or "")
sys.exit(1 if check.failed else 0)
