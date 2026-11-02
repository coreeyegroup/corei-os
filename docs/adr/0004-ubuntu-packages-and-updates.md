# ADR 0004: Ubuntu packages, signed APT repository and the update model

Status: accepted (owner request, 2 Nov 2026)

## Context

The owner asked for COREI OS to install and update on Ubuntu "like Ubuntu
itself": install with `sudo` and update with `apt`. Before this ADR, the only
way to run COREI OS was from a source checkout with `uv`, and Kafka and
PostgreSQL came from Docker Compose.

## Decision

1. **Debian packages for Ubuntu 22.04 and 24.04 LTS (amd64).** There are three:
   - `corei-os` (application);
   - `corei-kafka` (single-node Kafka);
   - `corei-os-server` (one-machine meta package depending on PostgreSQL 14 or later, `corei-kafka` and `corei-os`). The database setup reads the local cluster's real port instead of assuming 5432.
2. **Self-contained application.** The package carries its own CPython 3.12 (python-build-standalone, in `/opt/corei-os/python`) and its environment in `/opt/corei-os/venv`, built from `uv.lock` with hash-checked dependencies. Installed dependencies are exactly the tested ones, and the package does not depend on the distribution's Python (22.04 ships 3.10). Amended 3 Nov 2026, after the first install on core-node (Ubuntu 22.04) failed on the Python and PostgreSQL 16 requirements.
3. **One command, `corei-os`, for everything.** It runs the services, migrations, backups, status, topology, profiles and the auto-update switch. The systemd units, the maintainer scripts and operators all use the same code path.
4. **systemd services:**
   - `corei-api`, `corei-relay` and `corei-ledger`, grouped by `corei-os.target`;
   - each runs as the unprivileged `corei` user with filesystem hardening;
   - configuration errors (exit code 2) are not restarted in a loop.
5. **Configuration** lives in the conffile `/etc/corei-os/corei.env` (mode 0640 root:corei), so upgrades never overwrite local edits. `corei-os-server` generates the database password on the machine; it never leaves the host and is never in Git.
6. **Upgrade order:** stop services → `pg_dump` backup → forward-only migrations → restart. A failed backup or migration fails the package configuration, so new code never starts on an old schema. An older package refuses a database with newer migrations.
7. **Kafka is downloaded at install time.** The release is about 120 MB, too large for a Git-hosted repository. `corei-kafka` downloads it during install and refuses it unless the SHA-512 matches. That value was verified against Apache's GPG signature in CI when the package was built. The same pattern is used by Ubuntu "installer" packages.
8. **Signed APT repository on GitHub Pages**, with two channels:
   - `edge`: every push to `main` after all checks pass;
   - `stable`: `v*` tags.

   Indexes are signed with an ed25519 key held only as the `APT_SIGNING_KEY` secret. Release files carry `Origin: COREI OS`, so `unattended-upgrades` can be limited to this repository (`corei-os auto-update on`, off by default).
9. **Reproducible builds and versions.** Building the same commit twice gives byte-identical packages:
   - file times come from the commit time (`SOURCE_DATE_EPOCH`);
   - `.pyc` files are hash-based;
   - install metadata that records the build path is dropped.

   `corei-kafka` has its own packaging revision (`3.8.1-NcoreiM`), bumped whenever `packaging/corei-kafka` changes. That way apt never sees the same version with different contents, which it would report as a downgrade. Added 3 Nov 2026 after the upgrade test caught exactly that.
10. **Evidence:**
   - **Every pull request:** builds the packages and runs `packaging/test/install-test.sh` on fresh Ubuntu 22.04 and 24.04 runners with systemd. It covers install, readiness, an authenticated halt published through the relay to Kafka, and an upgrade that must back up and keep data, configuration and the halt. It also covers the auto-update switch and purge.
   - **After publishing:** installs from the public repository exactly as a user would.

## Consequences

- Hosting and production topology are still open (decision D13). Packages make
  one-machine and app-only hosts possible now; multi-host Kafka and PostgreSQL with
  replication remain a stage G design.
- Automatic updates restart trading services. They stay off by default and must
  be a per-host decision.
- Rolling back across a migration requires restoring the pre-upgrade backup.
- Publishing needs two one-time owner actions: the signing-key secret and the
  Pages source (see `docs/install.md`). Without them, packages are still built and
  tested on every change, but not published.
