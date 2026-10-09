# BreachOut (IronHold Labs)

Webapplicatie met security-challenges, teams, sessies en een live scoreboard. Flask + SQLite, draait achter nginx.

## Wat zit erin

| Onderdeel | Uitleg |
|---|---|
| Challenges | 10 standaard-challenges, door de admin uit te breiden. Per challenge een antwoord en optioneel een hint. |
| Hints | Zelf een hint vragen kost 10 punten. Een hint van de spelleider is gratis. |
| Scoreboard | Live (zonder verversen). Groen = opgelost zonder hint, oranje = met hint, rood = niet opgelost. Per team en per speler. |
| Teams | Aanmaken, joinen, verlaten. De spelleider wijst spelers toe. |
| Sessies (spelleider) | Sessie starten per team met een duur, verlengen/inkorten, beeindigen, resultaten als CSV. |
| Opruimen (US37) | Aan het einde van een sessie worden alle omgevingen van het team automatisch teruggezet. Mislukt dat, dan staat het zichtbaar bij de sessie met een knop om het opnieuw te proberen. |
| Reset (US24) | Een speler of de spelleider bouwt een challenge met een klik opnieuw op. De duur wordt gemeten en met de 5-minutennorm in het omgevingslog gezet. |
| Rechten | Rollen: Participant, Game master, Admin. De admin past rollen aan en verwijdert gebruikers. |
| Bewaartermijn | Participant-accounts worden na 90 dagen automatisch verwijderd (met scores, hints en omgevingen). |

Acceptatiecriteria en meetsjablonen voor het FO: `docs/acceptatiecriteria.md`.

## Mappen

```
app.py                  backend (alle API-routes, database, opruimen, live-stream)
templates/index.html    de hele front-end
static/style.css
ansible/                sjabloon om challenge-VM's op Proxmox terug te zetten (nog niet getest tegen Proxmox)
deploy/                 systemd-bestanden, nginx-config en voorbeeld van /etc/breachout.env
docs/                   acceptatiecriteria
tests/                  automatische tests
```

## Bijwerken op de server (bestaande opzet in ~/breachout-backend-repo)

1. **GitHub:** upload de *inhoud* van deze map (alle bestanden en mappen) en vervang wat er staat. Verwijder daarna op
   GitHub de losse `index.html` in de hoofdmap (die hoort alleen in `templates/`).
2. **Op de server:**
   ```bash
   cd ~/breachout-backend-repo
   git pull
   source venv/bin/activate && pip install -r requirements.txt
   ```
3. **Service en timer installeren (verplicht: voor het live scoreboard moet gunicorn met threads draaien):**
   ```bash
   sudo cp deploy/breachout.service deploy/breachout-maintenance.service deploy/breachout-maintenance.timer /etc/systemd/system/
   sudo cp deploy/breachout.env.example /etc/breachout.env      # alleen de eerste keer
   sudo nano /etc/breachout.env                                 # zet BREACHOUT_SECRET (bijv. uit: openssl rand -hex 32)
   sudo chown root:beheer /etc/breachout.env && sudo chmod 640 /etc/breachout.env
   sudo systemctl daemon-reload
   sudo systemctl enable --now breachout-maintenance.timer
   sudo systemctl restart breachout
   ```
4. **Nginx bijwerken** (zet o.a. de live-verbinding en een langere timeout goed):
   ```bash
   ls -l /etc/nginx/sites-enabled/        # 'breachout' moet een link naar sites-available/breachout zijn
   sudo cp deploy/nginx-breachout.conf /etc/nginx/sites-available/breachout
   sudo nginx -t && sudo systemctl reload nginx
   ```
5. De bestaande database wordt bij het starten automatisch bijgewerkt. Bestaande accounts krijgen een nieuwe
   bewaartermijn van 90 dagen vanaf dat moment.
6. **Verander het admin-wachtwoord** (standaard `admin` / `admin123`):
   ```bash
   cd ~/breachout-backend-repo && source venv/bin/activate
   python3 -c "
   import sqlite3
   from werkzeug.security import generate_password_hash
   db = sqlite3.connect('breachout.db')
   db.execute(\"UPDATE users SET password_hash=? WHERE username='admin'\", (generate_password_hash('KIES-EEN-LANG-WACHTWOORD'),))
   db.commit()"
   ```

Zonder stap 3 (threads) blijft de hele site hangen zodra iemand het scoreboard opent. Dat is getest.

## Rollen

| Rol | Mag |
|---|---|
| Participant | Challenges doen, hints vragen, team maken/joinen, scoreboard zien. |
| Game master | Alles van Participant (zonder team), plus: sessies starten/beeindigen/verlengen, hints sturen, challenges van een team resetten, spelers aan teams toewijzen, teams verwijderen, resultaten exporteren. |
| Admin | Alles van Game master, plus: challenges toevoegen/uitzetten/verwijderen, gebruikers en rollen beheren, omgevingslog zien. |

Nieuwe accounts zijn altijd Participant. Een admin maakt iemand Game master via Admin → Users & rights.

## Echte VM's koppelen (Proxmox)

De app simuleert omgevingen zolang `BREACHOUT_ENV_CMD` leeg is. Zet in `/etc/breachout.env`:

```
BREACHOUT_ENV_CMD=ansible-playbook /home/beheer/breachout-backend-repo/ansible/challenge.yml -e env_action={action} -e challenge_id={challenge_id} -e user_id={user_id}
PROXMOX_TOKEN_SECRET=...
```

`{action}` is `start`, `reset`, `stop` of `cleanup`. Er worden alleen getallen en vaste woorden ingevuld, geen
gebruikersinvoer, en er wordt geen shell gebruikt. Pas `ansible/vars.yml` aan (node, API-gebruiker, VM-id per challenge)
en test het playbook eerst met de hand:

```bash
ansible-playbook ansible/challenge.yml -e env_action=reset -e challenge_id=1 -e user_id=1
```

Een nieuwe challenge die de admin toevoegt heeft ook een VM nodig en een regel in `challenge_vms`.
Op de webserver is nodig: `pip install proxmoxer requests` en `ansible-galaxy collection install community.general`.

## Instellingen (omgevingsvariabelen)

| Variabele | Betekenis |
|---|---|
| `BREACHOUT_SECRET` | Geheim voor de sessiecookies. Verplicht aanpassen. |
| `BREACHOUT_SECURE_COOKIES=1` | Cookies alleen via HTTPS. Weglaten bij testen zonder HTTPS. |
| `BREACHOUT_ENV_CMD` | Commando om omgevingen te bouwen/opruimen (zie boven). Leeg = simuleren. |
| `BREACHOUT_ENV_TIMEOUT` | Maximale duur van een omgevingsactie in seconden (standaard 600). |
| `BREACHOUT_DB` | Pad naar de database (standaard `breachout.db` naast `app.py`). |

## Tests

```bash
bash tests/run_all.sh
```

Draait de backend-tests (rechten, teams, sessies, opruimen, hints, 90 dagen, migratie van een oude database,
gelijktijdig opstarten van meerdere workers) en een browser-simulatie met jsdom (inloggen, klikken, live updates,
XSS-bescherming). De browsertest heeft `node` nodig en haalt zelf `jsdom` op.

## Bekende beperkingen

- Adres en wachtwoord in het verbindingsvenster van een challenge zijn nog vaste voorbeeldwaarden
  (`10.10.<id>.5`, `student`). Koppel die aan jullie echte VM's.
- Het Ansible-sjabloon gaat uit van een VM per challenge. Doen meerdere spelers tegelijk dezelfde challenge,
  dan raakt een reset ze allemaal. Voor een VM per speler: kloon de basis-VM per `user_id`.
- Er is nog geen scherm om je eigen wachtwoord te wijzigen of te resetten, en er is geen begrenzing op
  inlogpogingen (fail2ban beschermt alleen SSH).
- Verbonden browsers houden een open verbinding; de app is bedoeld voor tientallen gelijktijdige gebruikers
  (2 workers x 20 threads), niet voor honderden.
