# Installing and updating COREI OS on Ubuntu

COREI OS installs and updates like any Ubuntu package, from a signed APT
repository. Supported: **Ubuntu 24.04 LTS (noble), amd64**.

## Install

```bash
curl -fsSL https://coreeyegroup.github.io/corei-os/install.sh | sudo sh
```

The installer:

1. adds the repository key to `/etc/apt/keyrings/corei-os.gpg`;
2. adds `/etc/apt/sources.list.d/corei-os.list`;
3. runs `apt install corei-os-server`.

That one package brings in and wires together:

- **PostgreSQL**, from Ubuntu;
- **corei-kafka**, a single-node Kafka on localhost;
- **corei-os**, the services and the `corei-os` command.

You can also add the repository by hand, which is the same as what the script does:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://coreeyegroup.github.io/corei-os/corei-os-archive-keyring.gpg \
  | sudo tee /etc/apt/keyrings/corei-os.gpg >/dev/null
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/corei-os.gpg] https://coreeyegroup.github.io/corei-os stable main" \
  | sudo tee /etc/apt/sources.list.d/corei-os.list
sudo apt update
sudo apt install corei-os-server
```

### Channels

| Channel | Contains | How to choose it |
| --- | --- | --- |
| `stable` (default) | Tagged releases (`vX.Y.Z`) | `curl … \| sudo sh` |
| `edge` | Every change merged to `main` that passed all checks | `curl … \| sudo COREI_SUITE=edge sh` |

To switch channel later, edit `/etc/apt/sources.list.d/corei-os.list`.

### Packages

| Package | What it installs |
| --- | --- |
| `corei-os-server` | Everything on one machine: PostgreSQL, `corei-kafka`, `corei-os`. Creates the `corei` database with a generated password, applies migrations, declares Kafka topics, starts all services |
| `corei-os` | The services and the `corei-os` command, for a host that uses a database and Kafka elsewhere: set `DATABASE_URL` and `KAFKA_BOOTSTRAP` in `/etc/corei-os/corei.env` |
| `corei-kafka` | Apache Kafka 3.8.1 (KRaft, single node, localhost only). The official release is downloaded during install, and refused unless its SHA-512 matches the value verified against Apache's signature when the package was built |

## After installing

```bash
sudo corei-os status                                               # readiness: database, schema, control, Kafka
sudo corei-os bootstrap /etc/corei-os/profiles/paper-example.yaml  # load the example PAPER profile
systemctl status corei-os.target                                   # all services
journalctl -u corei-api -u corei-relay -u corei-ledger -f          # logs
```

The operator API listens on `127.0.0.1:8780`. To give someone access:

1. Generate a token on your own machine.
2. Put its SHA-256 in `COREI_API_PRINCIPALS` in `/etc/corei-os/corei.env`.
3. Run `sudo systemctl restart corei-api`.

```bash
TOKEN=$(openssl rand -hex 32); echo "token: $TOKEN"        # give this to the operator, store it safely
printf '%s' "$TOKEN" | sha256sum                          # put "name:role:<this hash>" in the env file
```

Live trading modes are refused by this build.

## Updating

```bash
sudo apt update && sudo apt upgrade
```

Every upgrade of `corei-os` does three things, in this order:

1. Stops the COREI services.
2. Backs up the database to `/var/backups/corei-os/corei-pre-upgrade-<time>.dump`. The 10 newest backups are kept.
3. Applies database migrations, then restarts the services.

If the backup or a migration fails, the upgrade stops and the services stay stopped, so new code never runs against an old schema. Your edits to `/etc/corei-os/corei.env` and the profiles are kept, because they are conffiles.

### Automatic updates (optional, off by default)

```bash
sudo corei-os auto-update on      # unattended-upgrades installs COREI OS updates automatically
sudo corei-os auto-update off
sudo corei-os auto-update status
```

This uses Ubuntu's `unattended-upgrades`, limited to the COREI OS repository.

Automatic updates restart the trading services. Turn them on only for hosts where an unattended restart is acceptable.

## Backups and rollback

```bash
sudo corei-os backup                       # manual backup to /var/backups/corei-os
sudo apt install corei-os=<older version>  # older package (listed by: apt-cache policy corei-os)
```

Migrations only move forward. An older package refuses to start against a database that has newer migrations. To go back across a migration, restore the pre-upgrade backup with `pg_restore` first.

## Removing

```bash
sudo apt remove corei-os-server corei-os corei-kafka      # stop and remove programs, keep configuration
sudo apt purge corei-os-server corei-os corei-kafka       # also remove /etc/corei-os
```

Data is never deleted automatically:

- `/var/lib/corei-os`;
- `/var/backups/corei-os`;
- `/var/lib/corei-kafka`;
- the PostgreSQL database `corei`.

## One-time repository setup (maintainers)

The `packages` workflow builds and tests on every pull request, and publishes on every push to `main` (`edge`) and every `v*` tag (`stable`). Publishing needs two one-time settings in GitHub.

**1. A signing key**, created on your own machine and stored only as a repository secret:

```bash
export GNUPGHOME=$(mktemp -d)
gpg --batch --passphrase '' --quick-gen-key "COREI OS Archive Signing Key <noreply@coreeyegroup.github.io>" ed25519 sign 3y
gpg --armor --export-secret-keys | gh secret set APT_SIGNING_KEY -R coreeyegroup/corei-os
gpg --armor --export-secret-keys > corei-os-apt-signing-key.asc   # keep an offline backup, then:
rm -rf "$GNUPGHOME"
```

**2. GitHub Pages:** in **Settings → Pages**, set **Source** to **Deploy from a branch**, branch **`gh-pages`**, folder **`/ (root)`**.

Then re-run the latest `packages` workflow, or push to `main`. The `verify-published` job installs from the public repository, just as a user would.

To rotate the key, repeat step 1. Users then refresh their copy with the "add the repository by hand" commands above.
