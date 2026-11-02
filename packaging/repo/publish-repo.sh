#!/usr/bin/env bash
# Add freshly built .deb files to the APT repository tree and re-sign it.
#
#   publish-repo.sh <site-dir> <suite: edge|stable> <deb>...
#
# Layout (served by GitHub Pages):
#   pool/<suite>/<package>_<version>_<arch>.deb
#   dists/<suite>/{Release,InRelease,Release.gpg}
#   dists/<suite>/main/binary-amd64/Packages{,.gz}
#   corei-os-archive-keyring.gpg   install.sh   index.html
# Signing uses the secret key already imported into GNUPGHOME (APT_SIGNING_KEY in CI).
set -euo pipefail
SITE=$1; SUITE=$2; shift 2
KEEP=${KEEP_VERSIONS:-10}
HERE=$(cd "$(dirname "$0")" && pwd)
case "$SUITE" in edge|stable) ;; *) echo "suite must be edge or stable"; exit 1;; esac
KEY_ID=$(gpg --list-secret-keys --with-colons | awk -F: '/^sec/ {print $5; exit}')
[ -n "$KEY_ID" ] || { echo "publish-repo: no signing key imported"; exit 1; }

mkdir -p "$SITE/pool/$SUITE" "$SITE/dists/$SUITE/main/binary-amd64"
cp -f "$@" "$SITE/pool/$SUITE/"

# Keep the newest $KEEP versions of each package (older ones remain in git history).
for pkg in $(ls "$SITE/pool/$SUITE" | sed -n 's/_.*//p' | sort -u); do
    ls "$SITE/pool/$SUITE/${pkg}"_*.deb | while read -r f; do
        printf '%s\t%s\n' "$(dpkg-deb -f "$f" Version)" "$f"
    done | sort -t"$(printf '\t')" -k1,1 -V -r | tail -n +$((KEEP + 1)) | cut -f2 | xargs -r rm -f
done

cd "$SITE"
apt-ftparchive packages "pool/$SUITE" > "dists/$SUITE/main/binary-amd64/Packages"
gzip -9nkf "dists/$SUITE/main/binary-amd64/Packages"
apt-ftparchive \
    -o APT::FTPArchive::Release::Origin="COREI OS" \
    -o APT::FTPArchive::Release::Label="COREI OS" \
    -o APT::FTPArchive::Release::Suite="$SUITE" \
    -o APT::FTPArchive::Release::Codename="$SUITE" \
    -o APT::FTPArchive::Release::Architectures="amd64" \
    -o APT::FTPArchive::Release::Components="main" \
    -o APT::FTPArchive::Release::Description="COREI OS packages ($SUITE)" \
    release "dists/$SUITE" > "dists/$SUITE/Release"
rm -f "dists/$SUITE/InRelease" "dists/$SUITE/Release.gpg"
gpg --batch --yes --local-user "$KEY_ID" --clearsign -o "dists/$SUITE/InRelease" "dists/$SUITE/Release"
gpg --batch --yes --local-user "$KEY_ID" -abs -o "dists/$SUITE/Release.gpg" "dists/$SUITE/Release"
gpg --batch --yes --export "$KEY_ID" > corei-os-archive-keyring.gpg
install -m 0644 "$HERE/install.sh" install.sh
install -m 0644 "$HERE/index.html" index.html
touch .nojekyll
echo "publish-repo: $SUITE now lists:"
grep -E '^(Package|Version):' "dists/$SUITE/main/binary-amd64/Packages" | paste - - | sort -u
