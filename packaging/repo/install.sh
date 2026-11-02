#!/bin/sh
# COREI OS installer for Ubuntu 24.04 LTS (noble), amd64.
#
#   curl -fsSL https://coreeyegroup.github.io/corei-os/install.sh | sudo sh
#
# Adds the signed COREI OS APT repository, then installs COREI OS like any
# Ubuntu package. Afterwards update with:  sudo apt update && sudo apt upgrade
#
# Options (environment variables):
#   COREI_SUITE=stable|edge        stable = tagged releases (default), edge = every merge to main
#   COREI_PACKAGE=corei-os-server  everything on one machine (default) | corei-os (app only)
set -eu
BASE=https://coreeyegroup.github.io/corei-os
SUITE=${COREI_SUITE:-stable}
PACKAGE=${COREI_PACKAGE:-corei-os-server}

[ "$(id -u)" -eq 0 ] || { echo "Run as root: curl -fsSL $BASE/install.sh | sudo sh"; exit 1; }
case "$SUITE" in stable|edge) ;; *) echo "COREI_SUITE must be stable or edge"; exit 1;; esac
. /etc/os-release
if [ "${VERSION_CODENAME:-}" != "noble" ]; then
    echo "WARNING: COREI OS is built and tested for Ubuntu 24.04 (noble); this is ${PRETTY_NAME:-unknown}."
fi
[ "$(dpkg --print-architecture)" = amd64 ] || { echo "COREI OS packages are amd64 only"; exit 1; }

apt-get update -q
apt-get install -y -q ca-certificates curl gnupg >/dev/null
install -d -m 0755 /etc/apt/keyrings
curl -fsSL "$BASE/corei-os-archive-keyring.gpg" -o /etc/apt/keyrings/corei-os.gpg
chmod 0644 /etc/apt/keyrings/corei-os.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/corei-os.gpg] $BASE $SUITE main" \
    > /etc/apt/sources.list.d/corei-os.list
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y "$PACKAGE"
echo
echo "COREI OS is installed ($(corei-os version), channel: $SUITE)."
echo "  Status:   sudo corei-os status"
echo "  Update:   sudo apt update && sudo apt upgrade"
echo "  Optional: sudo corei-os auto-update on   (install COREI OS updates automatically)"
