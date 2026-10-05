# BreachOut backend

Echte Flask-backend met SQLite-database: login/registratie, challenges starten/
stoppen/resetten, vlag-inzending, live scorebord, en een admin-paneel om
challenges toe te voegen, aan/uit te zetten en te verwijderen.

## Lokaal draaien (test)
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    python3 app.py
Open http://127.0.0.1:8000 — standaard adminaccount: admin / admin123

## Op de Ubuntu-server draaien (echt, met nginx ervoor)
    sudo apt install python3 python3-pip python3-venv -y
    cd breachout-backend
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    gunicorn --bind 127.0.0.1:8000 app:app

Zet dan in /etc/nginx/sites-available/breachout:

    server {
        listen 80;
        server_name _;
        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
        }
    }

    sudo ln -s /etc/nginx/sites-available/breachout /etc/nginx/sites-enabled/
    sudo rm /etc/nginx/sites-enabled/default
    sudo nginx -t && sudo systemctl reload nginx

## Belangrijk
- Verander BREACHOUT_SECRET en het admin-wachtwoord voor echt gebruik.
- Start/stop/reset zijn nu nog nep (ze zetten alleen de status in de database).
  Zoek in app.py naar de NOTE-comments bij start_challenge/stop_challenge —
  daar roep je straks Terraform/Ansible aan om de echte VM te bouwen.
- Laat gunicorn als systemd-service draaien zodat hij blijft draaien na reboot
  (vraag het als je daar hulp bij wilt).
