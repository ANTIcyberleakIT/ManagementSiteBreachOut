const { JSDOM, VirtualConsole } = require("jsdom");
const BASE = process.env.BASE || "http://127.0.0.1:8000";
let failed = 0, errors = [];
const check = (n, c, x) => { console.log((c ? "PASS " : "FAIL ") + n + (c ? "" : " " + (x ?? ""))); if (!c) failed++; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function openPage() {
  const jar = {};
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => errors.push("jsdomError: " + e.message));
  vc.on("error", e => errors.push("console.error: " + e));
  const sse = { handlers: null };
  const dom = await JSDOM.fromURL(BASE + "/", {
    runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.confirm = () => true;
      w.fetch = async (url, o = {}) => {
        const h = { ...(o.headers || {}) };
        const c = Object.entries(jar).map(([k, v]) => k + "=" + v).join("; "); if (c) h.cookie = c;
        const r = await fetch(BASE + url, { ...o, headers: h });
        for (const sc of (r.headers.getSetCookie?.() || [])) { const [kv] = sc.split(";"); const i = kv.indexOf("="); jar[kv.slice(0, i)] = kv.slice(i + 1); }
        return r;
      };
      w.EventSource = class { constructor() { sse.es = this; setTimeout(() => this.onopen && this.onopen(), 10); } close() {} };
      w.addEventListener("error", e => errors.push("window error: " + e.message));
    },
  });
  const w = dom.window, d = w.document;
  const $ = s => d.querySelector(s), $$ = s => [...d.querySelectorAll(s)];
  const ev = (el, t) => el.dispatchEvent(new w.Event(t, { bubbles: true, cancelable: true }));
  const P = {
    w, d, $, $$, sse,
    click: el => ev(el, "click"),
    type: (el, v) => { el.value = v; },
    fire: async () => { sse.es.onmessage && sse.es.onmessage({}); await sleep(700); },
    waitFor: async (fn, ms = 4000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if (fn()) return true } catch (e) {} await sleep(40); } return false; },
    tab: async name => { P.click($$("#nav button").find(b => b.textContent === name)); await sleep(500); },
    btn: (scope, text) => [...(scope || d).querySelectorAll("button,a")].find(b => b.textContent.trim().startsWith(text)),
    login: async (u, p) => { P.type($("#u"), u); P.type($("#p"), p); ev($("#loginForm"), "submit"); return P.waitFor(() => !$("#app").hidden); },
    register: async (u, p) => { P.click($("#toRegister")); P.type($("#ru"), u); P.type($("#rp"), p); ev($("#registerForm"), "submit"); await sleep(600); },
  };
  await sleep(500);
  return P;
}

(async () => {
  // ---------- speler Alice
  const A = await openPage();
  check("loginscherm zichtbaar", !A.$("#v-login").hidden && A.$("#app").hidden);
  await A.register("alice", "secret1");
  check("registratie -> terug naar login", !A.$("#loginForm").hidden);
  check("login ok", await A.login("alice", "secret1"));
  check("speler ziet 3 tabs (geen GM/Admin)", A.$$("#nav button").map(b => b.textContent).join(",") === "Challenges,Scoreboard,Teams");
  check("10 challenge-kaarten", await A.waitFor(() => A.$$("#grid .card").length === 10));

  await A.tab("Teams");
  A.type(A.$("#teamName"), "Red Team"); A.click(A.btn(A.$("#v-teams"), "Create team")); 
  check("team aangemaakt en 'your team' badge", await A.waitFor(() => A.$("#teamsBody").textContent.includes("Red Team") && A.$("#teamsBody").textContent.includes("your team")));
  check("teamnaam staat in koptekst", A.$("#who").textContent.includes("Red Team"));
  A.type(A.$("#teamName"), "<b>x</b>"); A.click(A.btn(A.$("#v-teams"), "Create team"));
  check("ongeldige teamnaam geeft foutmelding", await A.waitFor(() => !A.$("#teamErr").hidden));

  await A.tab("Challenges");
  A.click(A.$$("#grid .card")[0]);
  check("drawer opent", A.$("#drawer").classList.contains("open") && A.$("#drawer").textContent.includes("Open door"));
  A.click(A.btn(A.$("#drawer"), "Start environment"));
  check("omgeving gestart: adres + antwoordveld", await A.waitFor(() => A.$("#answerInput") && A.$("#drawer").textContent.includes("10.10.1.5")));
  A.type(A.$("#answerInput"), "fout"); A.click(A.btn(A.$("#drawer"), "Submit"));
  check("fout antwoord -> melding", await A.waitFor(() => A.$$(".toast").some(t => t.textContent === "Incorrect answer")));
  A.type(A.$("#answerInput"), "FLAG{default_creds_are_never_safe}"); A.click(A.btn(A.$("#drawer"), "Submit"));
  check("goed antwoord -> solved", await A.waitFor(() => A.$("#drawer").textContent.includes("Solved")));
  A.click(A.btn(A.$("#drawer"), "Close"));
  // tweede challenge mét hint
  A.click(A.$$("#grid .card")[1]); A.click(A.btn(A.$("#drawer"), "Start environment")); await A.waitFor(() => A.$("#answerInput"));
  A.click(A.btn(A.$("#drawer"), "Ask for hint"));
  check("hint zichtbaar na vragen", await A.waitFor(() => A.$("#hintArea").textContent.includes("nmap")));
  A.type(A.$("#answerInput"), "FLAG{one_open_port_is_enough}"); A.click(A.btn(A.$("#drawer"), "Submit"));
  await A.waitFor(() => A.$("#drawer").textContent.includes("Solved")); A.click(A.btn(A.$("#drawer"), "Close"));
  // derde challenge alleen starten (= bezig)
  A.click(A.$$("#grid .card")[2]); A.click(A.btn(A.$("#drawer"), "Start environment")); await A.waitFor(() => A.$("#answerInput")); A.click(A.btn(A.$("#drawer"), "Close"));

  await A.tab("Scoreboard");
  await A.waitFor(() => A.$("#playerBoard").textContent.includes("alice"));
  const row = A.$$("#playerBoard tr").find(r => r.textContent.includes("alice"));
  check("scoreboard: 2 groen, 1 oranje (bezig), 7 rood", row.querySelectorAll(".cell.done").length === 2 && row.querySelectorAll(".cell.busy").length === 1 && row.querySelectorAll(".cell.todo").length === 7,
    [row.querySelectorAll(".cell.done").length, row.querySelectorAll(".cell.busy").length, row.querySelectorAll(".cell.todo").length]);
  check("scoreboard: 190 punten (100+100-10)", row.querySelector(".pts").textContent === "190", row.querySelector(".pts").textContent);
  const trow = A.$$("#teamBoard tr").find(r => r.textContent.includes("Red Team"));
  check("team-scoreboard heeft kleuren + punten", trow && trow.querySelectorAll(".cell.done").length === 2 && trow.querySelectorAll(".cell.busy").length === 1 && trow.querySelector(".pts").textContent === "190");
  check("live-badge zegt Live", A.$("#liveBadge").textContent === "Live");
  check("legenda: groen/oranje/rood", A.$(".legend").textContent.includes("Completed") && A.$(".legend").textContent.includes("In progress") && A.$(".legend").textContent.includes("Not done"));

  // ---------- admin: rechten + XSS
  const AD = await openPage();
  check("admin login", await AD.login("admin", "admin123"));
  check("admin ziet alle tabs", AD.$$("#nav button").map(b => b.textContent).join(",") === "Challenges,Scoreboard,Teams,Game master,Admin");
  await AD.tab("Admin");
  check("gebruikerstabel toont alice + dagen", await AD.waitFor(() => AD.$("#usersBody").textContent.includes("alice") && AD.$("#usersBody").textContent.includes("90 days")));
  AD.type(AD.$("#f-name"), "<img src=x onerror=window.__pwned=1>"); AD.type(AD.$("#f-cat"), "Web"); AD.type(AD.$("#f-desc"), "<script>window.__pwned=2</script>");
  AD.type(AD.$("#f-flag"), "x"); AD.type(AD.$("#f-hint"), "<b>tip</b>"); AD.$("#addForm").dispatchEvent(new AD.w.Event("submit", { bubbles: true, cancelable: true }));
  check("XSS-challenge toegevoegd en als tekst getoond", await AD.waitFor(() => AD.$("#adminBody").textContent.includes("<img src=x")));
  check("XSS in admin-tabel niet uitgevoerd", !AD.w.__pwned && AD.$$("#adminBody img").length === 0);
  await A.fire(); await A.tab("Challenges");
  check("XSS in spelerskaart niet uitgevoerd", !A.w.__pwned && A.$$("#grid img").length === 0 && A.$("#grid").textContent.includes("<img src=x"));
  // verwijder test-challenge
  AD.click(AD.btn(AD.$$("#adminBody tr").find(r => r.textContent.includes("<img")), "Delete")); 
  check("challenge verwijderd", await AD.waitFor(() => !AD.$("#adminBody").textContent.includes("<img")));
  // rol wijzigen: alice -> gamemaster
  const sel = AD.$$("#usersBody tr").find(r => r.textContent.includes("alice")).querySelector("select");
  sel.value = "gamemaster"; sel.dispatchEvent(new AD.w.Event("change", { bubbles: true }));
  check("rol gewijzigd + melding", await AD.waitFor(() => AD.$$(".toast").some(t => t.textContent === "Role updated")));
  // Alice's menu past live aan
  await A.fire();
  check("live: alice krijgt tab 'Game master' zonder refresh", A.$$("#nav button").some(b => b.textContent === "Game master"), A.$$("#nav button").map(b => b.textContent));

  // ---------- Bob (speler) joint team; Alice (nu GM) start sessie
  const B = await openPage();
  await B.register("bob", "secret2"); await B.login("bob", "secret2");
  await B.tab("Teams");
  check("bob ziet het team", await B.waitFor(() => B.$("#teamsBody").textContent.includes("Red Team")));
  B.click(B.btn(B.$("#teamsBody"), "Join")); await B.waitFor(() => B.$("#teamsBody").textContent.includes("your team"));
  await A.tab("Game master");
  check("GM-tab: team + challenge selects gevuld", await A.waitFor(() => A.$("#sessTeam").options.length > 1 && A.$("#helpChallenge").options.length > 1));
  A.$("#sessTeam").value = A.$("#sessTeam").options[1].value; A.type(A.$("#sessMinutes"), "30");
  A.click(A.btn(A.$("#v-gm"), "Start session"));
  check("sessie staat op Running", await A.waitFor(() => A.$("#sessBody").textContent.includes("Running")));
  await B.fire();
  check("live: bob ziet sessiebalk met tijd", !B.$("#sessionBar").hidden && /\d\d:\d\d/.test(B.$("#sessionBar").textContent), B.$("#sessionBar").textContent);
  // bob start challenge 4; GM stuurt hint
  await B.tab("Challenges"); B.click(B.$$("#grid .card")[3]); B.click(B.btn(B.$("#drawer"), "Start environment")); await B.waitFor(() => B.$("#answerInput"));
  A.$("#helpTeam").value = A.$("#helpTeam").options[1].value; A.$("#helpChallenge").value = [...A.$("#helpChallenge").options].find(o => o.textContent === "Hidden message").value;
  A.type(A.$("#helpText"), "Think about encodings"); A.click(A.btn(A.$("#v-gm"), "Send hint"));
  check("GM-hint verstuurd", await A.waitFor(() => A.$$(".toast").some(t => t.textContent.startsWith("Hint sent to 1"))));
  await B.fire();
  check("live: hint verschijnt bij bob (drawer open)", B.$("#hintArea").textContent.includes("Think about encodings"), B.$("#hintArea").textContent);
  // GM reset
  A.click(A.btn(A.$("#v-gm"), "Reset challenge"));
  check("GM reset team-challenge", await A.waitFor(() => A.$$(".toast").some(t => t.textContent.startsWith("Reset 1 environment"))));
  // sessie beëindigen -> opruimen
  A.click(A.btn(A.$("#v-gm"), "End now"));
  check("sessie beëindigd, opruimtijd getoond", await A.waitFor(() => A.$("#sessBody").textContent.includes("Ended") && /\d+(\.\d+)?s/.test(A.$("#sessBody").textContent)), A.$("#sessBody").textContent);
  check("CSV-link beschikbaar", !!A.$("#sessBody a[href*='export']"));
  await B.fire();
  check("live: sessiebalk bij bob weg", B.$("#sessionBar").hidden);
  check("live: bobs omgeving opgeruimd (drawer toont Start)", B.$("#drawer").textContent.includes("Start environment") || !B.$("#drawer").classList.contains("open"));
  // admin-log toont cleanup + reset met tijdlimiet
  await AD.tab("Admin"); await AD.fire();
  check("admin-log: cleanup + reset binnen 5 min", await AD.waitFor(() => AD.$("#logBody").textContent.includes("cleanup") && AD.$("#logBody").textContent.includes("within 5 min")));

  // mislukte opruiming: weergave + knop (serverantwoord nagebootst)
  const origFetch = A.w.fetch;
  A.w.fetch = (u, o) => u === "/api/gm/sessions" ? Promise.resolve({ ok: true, status: 200, json: async () => [{ id: 99, team: "Fake <b>Team</b>", team_id: 1, status: "ended", started_at: "2026-10-09 10:00:00", ends_at: "2026-10-09 11:00:00", ended_at: "2026-10-09 11:00:00", cleanup_seconds: 12.5, cleanup_failed: 2, within_limit: true, remaining: null }] }) : origFetch(u, o);
  await A.fire();
  check("UI: mislukte opruiming zichtbaar + retry-knop", A.$("#sessBody").textContent.includes("cleanup failed (2)") && !!A.btn(A.$("#sessBody"), "Retry cleanup"), A.$("#sessBody").textContent);
  check("UI: teamnaam in sessietabel ge-escaped", A.$("#sessBody b") && A.$("#sessBody b").textContent === "Fake <b>Team</b>");
  A.w.fetch = origFetch;

  check("geen JS-fouten tijdens de hele test", errors.length === 0, errors.slice(0, 5));
  console.log(failed ? "\nGEFAAALD: " + failed : "\nALLES GESLAAGD");
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error("TEST CRASH", e); process.exit(2); });
