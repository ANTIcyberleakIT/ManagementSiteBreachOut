import os
REPO = os.environ.get('REPO') or os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import os, sys, tempfile
from datetime import timedelta
tmp = tempfile.mkdtemp(); os.environ["BREACHOUT_DB"] = os.path.join(tmp, "t3.db")
sys.path.insert(0, REPO)
import app as A
A.app.config["TESTING"] = True
failed = 0
def check(n, c, extra=""):
    global failed; print(("PASS " if c else "FAIL ") + n + ("" if c else " " + str(extra))); failed += (not c)
def mk(n):
    c = A.app.test_client(); c.post("/api/auth/register", json={"username": n, "password": "secret1"}); c.post("/api/auth/login", json={"username": n, "password": "secret1"}); return c
admin = A.app.test_client(); admin.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
p, gm = mk("alice"), mk("kevin")
uid = {u["username"]: u["id"] for u in admin.get("/api/admin/users").get_json()}
admin.put(f"/api/admin/users/{uid['kevin']}/role", json={"role": "gamemaster"})
tid = p.post("/api/teams", json={"name": "Red"}).get_json()["id"]
sid = gm.post("/api/gm/sessions", json={"team_id": tid, "minutes": 20}).get_json()["id"]
p.post("/api/challenges/1/start"); p.post("/api/challenges/2/start")
A.ENV_CMD = "false"                                   # opruimen mislukt (bv. Proxmox onbereikbaar)
r = gm.post(f"/api/gm/sessions/{sid}/end").get_json()
check("sessie eindigt ook als opruimen mislukt", r["ok"] and r["session"]["status"] == "ended")
check("mislukking zichtbaar: cleanup_failed = 2", r["session"]["cleanup_failed"] == 2, r["session"])
conn = A.connect()
check("omgevingen blijven (nog) niet-gestopt", conn.execute("SELECT COUNT(*) FROM environments WHERE status!='stopped'").fetchone()[0] == 2)
check("mislukte cleanups staan in log (ok=false)", conn.execute("SELECT COUNT(*) FROM action_log WHERE action='cleanup' AND ok=0").fetchone()[0] == 2)
r = gm.post(f"/api/gm/sessions/{sid}/retry-cleanup").get_json()
check("opnieuw proberen terwijl het nog faalt blijft falen", r["ok"] is False and r["failed"] == 2, r)
A.ENV_CMD = ""                                        # probleem opgelost
r = gm.post(f"/api/gm/sessions/{sid}/retry-cleanup").get_json()
check("opnieuw opruimen slaagt: 2 schoongemaakt", r["ok"] and r["cleaned"] == 2 and r["failed"] == 0, r)
check("alle omgevingen nu gestopt", conn.execute("SELECT COUNT(*) FROM environments WHERE status!='stopped'").fetchone()[0] == 0)
check("sessielijst: geen mislukking meer", gm.get("/api/gm/sessions").get_json()[0]["cleanup_failed"] == 0)
check("retry op actieve sessie geweigerd", gm.post(f"/api/gm/sessions/{gm.post('/api/gm/sessions', json={'team_id': tid, 'minutes': 20}).get_json()['id']}/retry-cleanup").status_code == 404)
check("retry terwijl nieuwe sessie loopt geweigerd", gm.post(f"/api/gm/sessions/{sid}/retry-cleanup").status_code == 400)
check("speler mag niet opnieuw opruimen", p.post(f"/api/gm/sessions/{sid}/retry-cleanup").status_code == 403)
print("\nGEFAAALD" if failed else "\nALLES GESLAAGD"); sys.exit(1 if failed else 0)
