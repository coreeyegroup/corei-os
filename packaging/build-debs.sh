#!/usr/bin/env bash
# Build the COREI OS Debian packages for Ubuntu 22.04 (jammy) and 24.04 (noble), amd64.
#
#   sudo packaging/build-debs.sh                   # corei-os + corei-os-server
#   sudo KAFKA_SHA512=<verified sha512> packaging/build-debs.sh       # + corei-kafka
#
# Output: dist/*.deb. Needs root because the Python environment is built at its
# final path (/opt/corei-os/venv) so every script and shebang is correct after
# install; the build removes it again. Run it on a disposable machine (CI runner).
#
# Version:
#   tag vX.Y.Z  -> X.Y.Z                        (must equal pyproject version)
#   otherwise   -> X.Y.Z~dev<commit time>.g<sha> (sorts before X.Y.Z, increases per commit)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PKG="$ROOT/packaging"
DIST=${DIST:-$ROOT/dist}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
KAFKA_VERSION=${KAFKA_VERSION:-3.8.1}
KAFKA_DEB_VERSION="${KAFKA_VERSION}-1corei1"
PYTHON_VERSION=${PYTHON_VERSION:-3.12}
APP=/opt/corei-os

[ "$(id -u)" -eq 0 ] || { echo "build-debs: run as root (sudo)"; exit 1; }
command -v uv >/dev/null || { echo "build-debs: uv is required"; exit 1; }
cd "$ROOT"

BASE=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
REF=${GITHUB_REF:-$(git symbolic-ref -q HEAD || true)}
if [[ "$REF" == refs/tags/v* ]]; then
    VERSION=${REF#refs/tags/v}
    [ "$VERSION" = "$BASE" ] || { echo "tag v$VERSION != pyproject $BASE"; exit 1; }
else
    STAMP=$(git log -1 --format=%cd --date=format:%Y%m%d%H%M%S)
    VERSION="${BASE}~dev${STAMP}.g$(git rev-parse --short=7 HEAD)"
fi
VERSION=${VERSION_OVERRIDE:-$VERSION}
echo "build-debs: corei-os $VERSION"
mkdir -p "$DIST"

finish() { # $1 = staging root, $2 = package dir, $3 = version, $4 = output name
    local stage=$1 pkgdir=$2 ver=$3 out=$4 size
    size=$(du -sk --exclude=DEBIAN "$stage" | cut -f1)
    sed -e "s/@VERSION@/$ver/g" -e "s/@SIZE@/$size/" -e "s/@KAFKA_VERSION@/$KAFKA_VERSION/g" \
        "$pkgdir/control.in" > "$stage/DEBIAN/control"
    for f in conffiles postinst prerm postrm; do
        if [ -f "$pkgdir/$f" ]; then install -m "$([ "$f" = conffiles ] && echo 644 || echo 755)" "$pkgdir/$f" "$stage/DEBIAN/$f"; fi
    done
    (cd "$stage" && find . -type f ! -path './DEBIAN/*' -printf '%P\0' | sort -z | xargs -0 md5sum > DEBIAN/md5sums)
    dpkg-deb --root-owner-group -Zxz --build "$stage" "$DIST/$out" >/dev/null
    echo "build-debs: $DIST/$out"
}

doc() { # $1 = stage, $2 = package
    install -d "$1/usr/share/doc/$2"
    printf 'COREI OS (%s)\nSource: https://github.com/coreeyegroup/corei-os\n' "$2" > "$1/usr/share/doc/$2/copyright"
    printf '%s (%s) noble; urgency=medium\n\n  * Build of commit %s.\n\n -- COREI OS <noreply@coreeyegroup.github.io>  %s\n' \
        "$2" "$3" "$(git rev-parse --short=12 HEAD)" "$(date -R)" | gzip -9n > "$1/usr/share/doc/$2/changelog.gz"
}

# ---------------------------------------------------------------- corei-os
S="$WORK/corei-os"
install -d "$S/DEBIAN" "$S/usr/bin" "$S/etc/corei-os/profiles" "$S/lib/systemd/system"
rm -rf "$APP"
# Self-contained CPython (python-build-standalone via uv) inside the package, so the
# package does not depend on the distribution's Python (Ubuntu 22.04 has 3.10, 24.04 has 3.12).
UV_PYTHON_INSTALL_DIR="$APP/python" uv python install --quiet "$PYTHON_VERSION"
PY=$(find "$APP/python" -maxdepth 3 -type f -path '*/bin/python3.[0-9]*' ! -name '*-config' | sort -V | tail -1)
"$PY" -c 'import sys; assert sys.version_info[:2] >= (3, 12)' || { echo "build-debs: bad bundled Python $PY"; exit 1; }
[ -x "$PY" ] || { echo "build-debs: bundled Python not found"; exit 1; }
rm -rf "$APP/python/.lock" "$APP/python/.temp" "$APP/python/.cache"
# Trim what a headless server never uses (GUI toolkit, IDE, headers, docs) and strip debug symbols.
PYROOT=$(dirname "$(dirname "$PY")")
rm -rf "$PYROOT"/lib/python3.*/{idlelib,tkinter,turtledemo,ensurepip,lib2to3,test} \
       "$PYROOT"/lib/{tcl*,tk*,itcl*} "$PYROOT"/include "$PYROOT"/share
find "$PYROOT" -name '*.so*' -type f -exec strip --strip-unneeded {} + 2>/dev/null || true
uv venv --quiet --python "$PY" "$APP/venv"
uv export --quiet --frozen --no-dev --no-emit-project --format requirements-txt > "$WORK/requirements.txt"
uv pip install --quiet --python "$APP/venv/bin/python" --require-hashes -r "$WORK/requirements.txt"
uv build --quiet --wheel --out-dir "$WORK/wheel"
uv pip install --quiet --python "$APP/venv/bin/python" --no-deps "$WORK"/wheel/*.whl
"$APP/venv/bin/python" -m compileall -q "$APP/venv" >/dev/null || true
rm -f "$APP/venv/.lock" "$APP/venv/.gitignore" "$APP/venv/CACHEDIR.TAG"
printf '%s\n' "$VERSION" > "$APP/VERSION"
printf '%s\n' "$(git rev-parse HEAD)" > "$APP/COMMIT"
"$APP/venv/bin/corei-os" version | grep -qxF "$VERSION" || { echo "build-debs: installed version mismatch"; exit 1; }
install -d "$S/opt"
cp -a "$APP" "$S/opt/"
rm -rf "$APP"
ln -s /opt/corei-os/venv/bin/corei-os "$S/usr/bin/corei-os"
install -m 0640 "$PKG/corei-os/corei.env" "$S/etc/corei-os/corei.env"
install -m 0644 "$ROOT/config/paper-bootstrap.yaml" "$S/etc/corei-os/profiles/paper-example.yaml"
install -m 0644 "$PKG"/corei-os/systemd/* "$S/lib/systemd/system/"
doc "$S" corei-os "$VERSION"
finish "$S" "$PKG/corei-os" "$VERSION" "corei-os_${VERSION}_amd64.deb"

# ---------------------------------------------------------------- corei-os-server (meta)
S="$WORK/corei-os-server"
install -d "$S/DEBIAN"
doc "$S" corei-os-server "$VERSION"
finish "$S" "$PKG/corei-os-server" "$VERSION" "corei-os-server_${VERSION}_all.deb"

# ---------------------------------------------------------------- corei-kafka
# KAFKA_SHA512 must be the SHA-512 of the official release, verified against Apache's signature.
if [ -n "${KAFKA_SHA512:-}" ]; then
    [[ "$KAFKA_SHA512" =~ ^[0-9a-f]{128}$ ]] || { echo "build-debs: KAFKA_SHA512 malformed"; exit 1; }
    S="$WORK/corei-kafka"
    install -d "$S/DEBIAN" "$S/etc/corei-kafka" "$S/lib/systemd/system" "$S/usr/lib/corei-kafka" "$S/usr/share/corei-kafka"
    install -m 0644 "$PKG/corei-kafka/server.properties" "$S/etc/corei-kafka/server.properties"
    install -m 0644 "$PKG/corei-kafka/corei-kafka.service" "$S/lib/systemd/system/"
    install -m 0755 "$PKG/corei-kafka/fetch-kafka" "$S/usr/lib/corei-kafka/fetch-kafka"
    printf '%s\n' "$KAFKA_VERSION" > "$S/usr/share/corei-kafka/kafka-version"
    printf '%s\n' "$KAFKA_SHA512" > "$S/usr/share/corei-kafka/kafka.sha512"
    doc "$S" corei-kafka "$KAFKA_DEB_VERSION"
    finish "$S" "$PKG/corei-kafka" "$KAFKA_DEB_VERSION" "corei-kafka_${KAFKA_DEB_VERSION}_all.deb"
else
    echo "build-debs: KAFKA_SHA512 not set; corei-kafka not built"
fi
