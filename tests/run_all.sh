#!/usr/bin/env bash
# Draait alle tests tegen een tijdelijke database. Vereist: python3 + flask (venv), en voor de browsertest node + npm.
#   bash tests/run_all.sh
set -u
cd "$(dirname "$0")/.."
REPO="$(pwd)"; export REPO
fail=0
for t in tests/test_backend.py tests/test_backend2.py tests/test_backend3.py; do
  echo "== $t"; python3 "$t" 2>&1 | grep -E "^FAIL|GESLAAGD|GEFAAALD" ; [ "${PIPESTATUS[0]}" -eq 0 ] || fail=1
done
if command -v node >/dev/null 2>&1; then
  echo "== browsertest (jsdom)"
  (cd tests/ui && [ -d node_modules ] || npm install --silent) 
  export BREACHOUT_DB="$(mktemp -d)/ui.db"
  python3 app.py > /tmp/breachout_test_server.log 2>&1 &
  PID=$!; sleep 2.5
  (cd tests/ui && timeout 120 node ui_test.js | grep -E "^FAIL|CRASH|GESLAAGD|GEFAAALD"); [ "${PIPESTATUS[0]}" -eq 0 ] || fail=1
  kill $PID 2>/dev/null; wait $PID 2>/dev/null
else
  echo "== browsertest overgeslagen (node niet gevonden)"
fi
[ $fail -eq 0 ] && echo "ALLE TESTS GESLAAGD" || { echo "ER ZIJN TESTS GEFAALD"; exit 1; }
