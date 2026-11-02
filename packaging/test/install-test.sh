#!/usr/bin/env bash
# End-to-end package test on a disposable Ubuntu 24.04 machine with systemd (CI runner).
#
#   packaging/test/install-test.sh <dir with first .debs> <dir with newer .debs>
#
# 1. apt install corei-os-server (+ PostgreSQL, Kafka) from the built packages
# 2. every service active; status READY after loading the example profile
# 3. operator API: authenticated halt; outbox relay publishes it to Kafka
# 4. apt upgrade to the newer build: pre-upgrade backup, migrations, data and halt kept
# 5. auto-update switch; 6. purge removes programs and keeps data
set -euo pipefail
FIRST=$1
NEXT=$2
SERVICES="postgresql corei-kafka corei-api corei-relay corei-ledger"
export DEBIAN_FRONTEND=noninteractive

step() { echo; echo "=== $*"; }
fail() { echo "FAILED: $*"; for u in $SERVICES; do sudo journalctl -u "$u" --no-pager -n 40 || true; done; exit 1; }
active() { for u in $SERVICES; do systemctl is-active --quiet "$u" || fail "$u not active"; done; echo "all services active"; }
psql_corei() { sudo -u postgres psql -d corei -tAc "$1"; }

step "1. install corei-os-server from built packages"
sudo apt-get update -q
sudo apt-get install -y -q "$FIRST"/*.deb
corei-os version
active

step "2. status before and after loading the example profile"
if sudo corei-os status; then fail "status READY before any control state exists"; fi
sudo corei-os bootstrap /etc/corei-os/profiles/paper-example.yaml >/dev/null
sudo corei-os status || fail "not ready after bootstrap"
sudo corei-os status --json | grep -q '"name": "kafka"' || fail "kafka not checked"
curl -fsS http://127.0.0.1:8780/health/ready >/dev/null || fail "API not ready"

step "3. authenticated halt through the API; relay publishes it"
TOKEN=$(openssl rand -hex 24)
HASH=$(printf '%s' "$TOKEN" | sha256sum | cut -d' ' -f1)
sudo sed -i "s/^COREI_API_PRINCIPALS=.*/COREI_API_PRINCIPALS=ci:trading_ops:${HASH}/" /etc/corei-os/corei.env
sudo systemctl restart corei-api
for _ in $(seq 30); do curl -fsS http://127.0.0.1:8780/health/live >/dev/null 2>&1 && break; sleep 1; done
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8780/control/state)
[ "$code" = 401 ] || fail "unauthenticated request returned $code"
VERSION_NOW=$(curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8780/control/state \
    | python3 -c 'import json,sys; print([s["version"] for s in json.load(sys.stdin) if s["scope"]=="ACCOUNT:acc_us_paper_01"][0])')
curl -fsS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"command_id\":\"cmd-ci-halt-0001\",\"type\":\"HALT_NEW_ORDERS\",\"scope\":\"ACCOUNT:acc_us_paper_01\",\"expected_version\":$VERSION_NOW,\"reason\":\"package test\"}" \
    http://127.0.0.1:8780/control/commands >/dev/null || fail "halt command refused"
for _ in $(seq 60); do
    [ "$(psql_corei 'SELECT count(*) FROM outbox WHERE published_at_ns IS NULL')" = 0 ] && break; sleep 1
done
[ "$(psql_corei 'SELECT count(*) FROM outbox WHERE published_at_ns IS NULL')" = 0 ] || fail "relay did not publish the outbox"
echo "halt recorded and published to Kafka"

step "4. apt upgrade to the newer build"
BEFORE=$(corei-os version)
sudo apt-get install -y -q "$NEXT"/*.deb
AFTER=$(corei-os version)
[ "$BEFORE" != "$AFTER" ] || fail "version did not change"
echo "upgraded $BEFORE -> $AFTER"
sudo ls /var/backups/corei-os | grep -q '^corei-pre-upgrade-' || fail "no pre-upgrade backup"
[ "$(psql_corei "SELECT halted FROM control_state WHERE scope='ACCOUNT:acc_us_paper_01'")" = t ] || fail "halt lost on upgrade"
[ "$(psql_corei 'SELECT count(*) FROM accounts')" = 3 ] || fail "accounts lost on upgrade"
sudo grep -q "^COREI_API_PRINCIPALS=ci:" /etc/corei-os/corei.env || fail "config edit lost on upgrade"
active
sudo corei-os status

step "5. auto-update switch"
sudo corei-os auto-update on && test -f /etc/apt/apt.conf.d/52corei-os-auto-update
sudo corei-os auto-update off && test ! -f /etc/apt/apt.conf.d/52corei-os-auto-update

step "6. purge"
sudo apt-get purge -y -q corei-os-server corei-os corei-kafka
for u in corei-api corei-relay corei-ledger corei-kafka; do
    if systemctl is-active --quiet "$u"; then fail "$u still running after purge"; fi
done
[ ! -e /opt/corei-os ] && [ ! -e /opt/corei-kafka ] && [ ! -e /usr/bin/corei-os ] || fail "files left after purge"
sudo test -d /var/backups/corei-os || fail "backups removed by purge"
echo
echo "PACKAGE TEST PASSED"
