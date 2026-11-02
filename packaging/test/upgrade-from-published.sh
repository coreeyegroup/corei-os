#!/usr/bin/env bash
# Upgrade test from what users run today: install the currently published edge
# release from the public APT repository, put real state in it (profile, halt),
# then apt-upgrade to the freshly built packages and prove nothing was lost.
#
#   packaging/test/upgrade-from-published.sh <dir with new .debs>
set -euo pipefail
NEW=$1
export DEBIAN_FRONTEND=noninteractive
fail() { echo "FAILED: $*"; for u in corei-api corei-relay corei-ledger corei-kafka postgresql; do sudo journalctl -u "$u" --no-pager -n 40 || true; done; exit 1; }
psql_corei() { sudo -u postgres psql -d corei -tAc "$1"; }

echo "=== install the published edge release"
curl -fsSL https://coreeyegroup.github.io/corei-os/install.sh -o /tmp/corei-install.sh
sudo COREI_SUITE=edge sh /tmp/corei-install.sh
OLD=$(corei-os version)
sudo corei-os bootstrap /etc/corei-os/profiles/paper-example.yaml >/dev/null
sudo corei-os status || fail "published release not ready"
psql_corei "UPDATE control_state SET halted = true, halt_reason = 'upgrade test', version = version + 1 WHERE scope = 'ACCOUNT:acc_us_paper_01'" >/dev/null
CASH_BEFORE=$(psql_corei "SELECT sum(balance_units) FROM cash_balances")

echo "=== apt upgrade from $OLD to the new build"
sudo apt-get install -y -q "$NEW"/*.deb
NEWV=$(corei-os version)
[ "$NEWV" != "$OLD" ] || fail "version unchanged"
sudo ls /var/backups/corei-os | grep -q '^corei-pre-upgrade-' || fail "no pre-upgrade backup"
sudo corei-os status || fail "not ready after upgrade"
[ "$(psql_corei "SELECT halted FROM control_state WHERE scope = 'ACCOUNT:acc_us_paper_01'")" = t ] || fail "halt lost"
[ "$(psql_corei "SELECT sum(balance_units) FROM cash_balances")" = "$CASH_BEFORE" ] || fail "cash changed"
TENANTS=$(psql_corei "SELECT string_agg(DISTINCT tenant_id, ',') FROM accounts" 2>/dev/null || echo none)
echo "accounts belong to tenant(s): $TENANTS"
echo "UPGRADE FROM PUBLISHED PASSED ($OLD -> $NEWV)"
