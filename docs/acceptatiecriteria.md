# Acceptatiecriteria en testen: US37, US24, US32

Bedoeld om in het Functioneel Ontwerp over te nemen. Onder elke user story staat waar het in de applicatie zit
en hoe je het test. De tabellen "Gemeten op de echte omgeving" moeten jullie zelf invullen: de tijden hangen af
van Proxmox en zijn hier niet gemeten (de automatische tests draaien zonder echte VM's).

---

## US37 Challenge automatisch opruimen na sessie (MUST)

**Doel:** het volgende team start met een schone omgeving.

**Acceptatiecriteria**
1. Aan het einde van een sessie (tijd verlopen, of de spelleider kiest *End now*) worden alle challenge-omgevingen
   van de teamleden automatisch teruggezet naar de beginstand.
2. Het opruimen is klaar binnen **5 minuten** (300 seconden). De gemeten tijd staat bij de sessie.
3. Na afloop staat geen omgeving van het team meer op actief. De resultaten van de sessie zijn vooraf bewaard
   en als CSV te exporteren.
4. Mislukt het opruimen, dan toont de sessie *cleanup failed (n)* en kan de spelleider *Retry cleanup* kiezen.
   Elke poging staat met duur en resultaat in het omgevingslog (Admin).
5. Het volgende team start een challenge altijd vanaf de beginstand (start = terugzetten naar de schone snapshot).
6. Spelers kunnen tijdens een lopende sessie niet van team wisselen.

**Keuze die jullie in het FO moeten bevestigen:** "teamdata verwijderd" is uitgewerkt als: omgevingsdata wordt gewist,
de behaalde punten blijven staan (nodig voor scoreboard en export) en verdwijnen na 90 dagen samen met het account.

**Waar zit het:** `finalize_session()` en `maintenance()` in `app.py`; playbook `ansible/challenge.yml`
(`env_action=cleanup`); een systemd-timer draait `app.py maintenance` elke minuut, zodat een verlopen sessie ook
wordt opgeruimd als niemand de site open heeft.

**Testen (handmatig):** Teams → maak team met 2 spelers → Game master → Start session (5 min) → spelers starten een
challenge → *End now* (of wacht tot de tijd om is). Controleer: sessie *Ended*, opruimtijd groen, omgevingslog toont
`cleanup` per omgeving, spelers zien de challenge weer als *Available*.

| Gemeten op de echte omgeving | Aantal omgevingen | Opruimtijd | Binnen 5 min? |
|---|---|---|---|
| Sessie 1 | | | |
| Sessie 2 | | | |

---

## US24 Challenge met één actie opnieuw opbouwen (SHOULD)

**Doel:** minimale downtime bij een kapotte challenge.

**Acceptatiecriteria**
1. Een speler (*Reset*) of de spelleider (*Reset challenge* voor het hele team) bouwt een challenge met één actie
   opnieuw op: het playbook draait met `env_action=reset`.
2. De duur wordt gemeten en gelogd. Norm: binnen 5 minuten (NFR10).
3. Het omgevingslog (Admin) toont per reset de duur en *within 5 min* of *over 5 min*.
4. Mislukt een reset, dan krijgt de gebruiker een foutmelding en wordt de omgeving niet op actief gezet.

**Testen (handmatig, per categorie):** start de challenge, klik *Reset*, lees de duur af in Admin → Environment log.

| Categorie | Challenge | VM-id | Gemeten tijd | Binnen 5 min? |
|---|---|---|---|---|
| Authentication | | | | |
| Phishing & Social Engineering | | | | |
| Network & Firewall | | | | |
| Forensics & Log Analysis | | | | |
| Encryption & Data Protection | | | | |

---

## US32 Hints sturen en challenge resetten (SHOULD)

**Doel:** een team loopt niet onnodig vast.

**Acceptatiecriteria**
1. Per challenge kan een hint worden vastgelegd (Admin → Add challenge).
2. De spelleider kan tijdens een sessie een hint naar een team sturen (Game master → Help a team). De hint verschijnt
   live bij alle teamleden, zonder verversen. De spelleider kan een eigen tekst meegeven.
3. Een hint van de spelleider kost de spelers geen punten. Een hint die een speler zelf vraagt kost 10 punten.
   Op het scoreboard is een challenge met een hint oranje.
4. De spelleider kan een vastgelopen challenge resetten voor het hele team (alleen voor leden die de challenge
   draaiend hebben).

**Testen (handmatig):** twee spelers in een team starten dezelfde challenge, spelleider stuurt een hint en klikt *Reset challenge*.

---

## Overige eisen die erbij zijn gekomen

| Eis | Uitwerking |
|---|---|
| Deelnemers maximaal 90 dagen op de webserver | Accounts met rol *Participant* worden na 90 dagen automatisch verwijderd, inclusief scores, hints en omgevingen. Admin ziet per gebruiker hoeveel dagen er nog over zijn. |
| Admin past rechten van deelnemers aan | Admin → Users & rights: rol wijzigen (Participant, Game master, Admin) of gebruiker verwijderen. Eigen rol wijzigen of jezelf verwijderen kan niet. |
| Live scoreboard zonder verversen | Server-Sent Events. Het scoreboard toont *Live*, of *Reconnecting…* als de verbinding wegvalt (dan wordt elke 8 s ververst). |
| Teams | Teams aanmaken, joinen en verlaten; de spelleider wijst spelers toe. Scoreboard toont teams en spelers. |
| Kleuren scoreboard | Groen = opgelost zonder hint, oranje = opgelost met hint, rood = niet opgelost. |

## Automatische tests

`bash tests/run_all.sh` draait alle tests (backend en browser-simulatie). Zie `README.md`.
