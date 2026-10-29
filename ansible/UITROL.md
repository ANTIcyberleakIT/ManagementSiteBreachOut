# BreachOut – challenges uitrollen

In `ansible/challenges/` staat per escaperoom een zip. Elke zip bevat de volledige
room: `build.yml`, `challenge.yml`, `data.yml` en de `app/` (Flask-room). Hieronder
staat hoe je ze uitrolt.

## Overzicht challenges

| Zip | Sjabloon (vmid) | Niveau | Naam |
|---|---|---|---|
| `phishing-01.zip` | 9101 | beginner | De verdachte mailbox |
| `datalek-melden-01.zip` | 9102 | beginner | De gestolen laptop |
| `wachtwoord-bingo-01.zip` | 9103 | beginner | The Weakest Password |
| `brute-force-log-01.zip` | 9104 | intermediate | The Breach in the Logs |
| `forgotten-comment-01.zip` | 9105 | intermediate | The Forgotten Comment |
| `idor-factuur-01.zip` | 9106 | intermediate | The Number in the URL |
| `kerstborrel-01.zip` | 9110 | intermediate | De Kerstborrel-hack |
| `mark-case-01.zip` | 9107 | difficult | Mark's Last Working Day |
| `share-leak-01.zip` | 9108 | difficult | The Wrong Share Link |
| `hackback-01.zip` | 9109 | difficult | Hack Back: ShinyHunters |
| `ransomware-serverkamer-01.zip` | 9111 | difficult | Ransomware op de serverkamer |

## Machines

| Machine | Adres | Rol |
|---|---|---|
| Beheer | `10.105.40.20` | Ansible control node (hier draai je de playbooks) |
| Webserver | `10.105.25.10` | webapp + Nginx-proxy naar de rooms |
| ProxServer1 | `10.105.30.5` | Proxmox (API + containers/sjablonen) |

## Eenmalige voorbereiding (op Beheer)

Het volledige ansible-project moet op Beheer staan in `~/breachout-ansible`, met een
ingevulde `inventory/`. Die bevat geheimen en staat **bewust niet in Git**:

```
~/breachout-ansible/
  ansible.cfg
  inventory/
    hosts                         # [local] beheer, [proxmox] proxserver1, [web] webserver
    group_vars/all/pve.yml        # Proxmox-instellingen (in de repo)
    group_vars/all/vars.yml       # proxmox_api_host / user / token-id
    group_vars/all/vault.yml      # vault_proxmox_api_token_secret (GEHEIM)
```

`inventory/hosts` (vul je eigen waarden in):

```ini
[local]
beheer ansible_connection=local

[proxmox]
proxserver1 ansible_host=10.105.30.5 ansible_user=root

[web]
webserver ansible_host=10.105.25.10 ansible_user=beheer ansible_become_pass='<sudo-wachtwoord beheer op webserver>'
```

## Een challenge uitrollen

### Stap 1 – zip op Beheer zetten (PowerShell op Windows)

```powershell
scp "pad\naar\<challenge>.zip" beheer@10.105.40.20:~/
```

### Stap 2 – uitpakken in het project (op Beheer)

Pak de zip uit in de juiste challenge-map. Voorbeeld voor de ransomware-room:

```bash
cd ~/breachout-ansible
mkdir -p challenges/ransomware-serverkamer-01
unzip -o ~/ransomware-serverkamer-01.zip -d challenges/ransomware-serverkamer-01
```

> Gebruik altijd `unzip -o` (overschrijven). Doe nooit `rm -rf` op het project: dan
> ben je je `inventory/` (met je token) kwijt.

### Stap 3 – het sjabloon bouwen (op Beheer)

```bash
cd ~/breachout-ansible
ansible-playbook challenges/<challenge-id>/build.yml
```

Dit maakt een kale container in VLAN 40 (met internet), installeert de Flask-room,
zet het netwerk naar VLAN 51 en zet de container om naar een **sjabloon**
(de vmid uit de tabel). Verwacht resultaat: `failed=0` en als laatste
`"Sjabloon <vmid> ... staat klaar op ProxServer1."`

> **Bestaat het sjabloon al en wordt het niet verwijderd?** Melding
> `base volume ... is still in use by linked clone`: er draait nog een gestarte
> room (een kloon). Verwijder die eerst op ProxServer1:
> ```bash
> pct list                 # zoek de kloon (reeks 5000–5149)
> pct stop <id> && pct destroy <id>
> ```
> en draai `build.yml` opnieuw.

## Een room starten voor een team

Dit doet de webapp automatisch via de uitrol-API. Handmatig testen kan zo (op Beheer):

```bash
cd ~/breachout-ansible
ansible-playbook playbooks/challenge_start.yml \
  -e '{"challenge_id":"<challenge-id>","team_id":"1","run_id":"test01"}'
```

De start kloont het sjabloon, zet een unieke flag in de container, en maakt een
Nginx-route op de webserver (`/room/<token>/`). `team_id` moet een **getal** zijn.
Resultaat `failed=0`, `unreachable=0` = de room is bereikbaar.

## Webapp (opnieuw) uitrollen

```bash
cd ~/breachout-ansible
ansible-playbook playbooks/webapp_deploy.yml
```

Dit kopieert de map `webapp/` naar de webserver en herstart de service.
De database (`breachout.db`) blijft behouden (er wordt een back-up van gemaakt).

## Flags

- Per-team rooms (bv. IDOR, ransomware) krijgen bij het starten een **unieke flag**
  die je in de room zelf vindt.
- Sommige challenges hebben een **vaste, afgeleide flag** (bv. "Mark's Last Working
  Day"). Die staat in `data.yml` en wordt bij registratie als vaste flag ingevuld.
