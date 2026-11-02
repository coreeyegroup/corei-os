"""``corei-os``: the operator command installed with the Debian package.

Every service and every maintenance step on an installed host goes through
this one command, so the systemd units, the package scripts and an operator at
a shell all do exactly the same thing:

    corei-os version                  installed version
    corei-os status                   readiness (database, schema, control, Kafka)
    corei-os migrate [--check]        apply (or only check) database migrations
    corei-os backup [--dir D]         pg_dump the database before an upgrade
    corei-os topology [--verify]      create / verify the declared Kafka topics
    corei-os bootstrap PROFILE.yaml   load a deployment profile (PAPER/BACKTEST only)
    corei-os api                      run the operator API          (corei-api.service)
    corei-os relay                    run the outbox -> Kafka relay (corei-relay.service)
    corei-os ledger-consumer          run the fill -> ledger consumer (corei-ledger.service)
    corei-os setup-local              provision the local PostgreSQL database (root)
    corei-os auto-update on|off|status   unattended apt upgrades for COREI OS only (root)

Settings come from the environment. When a setting is not already set, it is
read from ``/etc/corei-os/corei.env`` (override with ``COREI_ENV_FILE``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from fastapi import FastAPI

    from corei.infra.settings import Settings

log = logging.getLogger("corei")

DEFAULT_ENV_FILE = Path("/etc/corei-os/corei.env")
DEFAULT_BACKUP_DIR = Path("/var/backups/corei-os")
AUTO_UPDATE_FILE = Path("/etc/apt/apt.conf.d/52corei-os-auto-update")
AUTO_UPDATE_BODY = (
    "// Managed by `corei-os auto-update`. Lets unattended-upgrades install COREI OS\n"
    "// packages from the COREI OS repository. Remove with `corei-os auto-update off`.\n"
    'Unattended-Upgrade::Origins-Pattern { "origin=COREI OS,label=COREI OS"; };\n'
)
EXIT_NOT_READY = 3
NO_SETTINGS_COMMANDS = frozenset({"version", "auto-update"})


class CliError(RuntimeError):
    pass


# ---------------------------------------------------------------- environment file


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines (systemd EnvironmentFile subset): comments, blanks, quotes."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key.replace("_", "").isalnum():
            raise CliError(f"bad line in environment file: {raw!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_environment(environ: dict[str, str]) -> dict[str, str]:
    """Environment variables win; the env file fills only what is missing."""
    path = Path(environ.get("COREI_ENV_FILE", str(DEFAULT_ENV_FILE)))
    merged = dict(environ)
    try:
        file_values = parse_env_file(path.read_text())
    except FileNotFoundError:
        return merged
    except PermissionError as exc:
        raise CliError(f"cannot read {path}: run with sudo or as the corei user") from exc
    for key, value in file_values.items():
        merged.setdefault(key, value)
    return merged


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Set keys in an env file, keeping every other line (and comments) as they were."""
    lines = path.read_text().splitlines() if path.exists() else []
    done: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip().lstrip("#").strip()
        if key in updates and "=" in line:
            out.append(f"{key}={updates[key]}")
            done.add(key)
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in updates.items() if k not in done)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n")
    tmp.chmod(0o640)
    tmp.replace(path)


# ---------------------------------------------------------------- helpers


VERSION_FILE = Path("/opt/corei-os/VERSION")


def installed_version() -> str:
    """The package version on an installed host; the Python package version otherwise."""
    try:
        return VERSION_FILE.read_text().strip()
    except OSError:
        pass
    try:
        return dist_version("corei-os")
    except PackageNotFoundError:
        return "0+unknown"


def _settings(env: dict[str, str]) -> Settings:
    from corei.infra.settings import Settings, SettingsError

    try:
        return Settings.from_env(env)
    except SettingsError as exc:
        raise CliError(f"{exc} (set it in {DEFAULT_ENV_FILE})") from exc


def _stop_flag() -> Callable[[], bool]:
    stopping = {"now": False}

    def handler(signum: int, _frame: object) -> None:
        log.info("signal %s received, stopping", signum)
        stopping["now"] = True

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    return lambda: stopping["now"]


def _kafka_probe(bootstrap: str) -> Callable[[], str]:
    from confluent_kafka.admin import AdminClient

    admin = AdminClient({"bootstrap.servers": bootstrap})

    def probe() -> str:
        md = admin.list_topics(timeout=3)
        return f"{len(md.brokers)} broker(s), {len(md.topics)} topic(s)"

    return probe


# ---------------------------------------------------------------- commands


def cmd_version(_args: argparse.Namespace, _env: dict[str, str]) -> int:
    print(installed_version())
    return 0


def cmd_migrate(args: argparse.Namespace, env: dict[str, str]) -> int:
    from corei.infra.db import connection_factory, migrate, schema_status

    settings = _settings(env)
    with connection_factory(settings.database_url)() as conn:
        if args.check:
            applied, expected = schema_status(conn)
            print(f"schema: applied {applied}, expected {expected}")
            return 0 if applied == expected else EXIT_NOT_READY
        done = migrate(conn)
    print(f"migrations applied now: {done or 'none (already at head)'}")
    return 0


def cmd_status(args: argparse.Namespace, env: dict[str, str]) -> int:
    from corei.api.readiness import check_readiness
    from corei.infra.db import connection_factory

    settings = _settings(env)
    probe = _kafka_probe(settings.kafka_bootstrap) if settings.kafka_bootstrap else None
    report = check_readiness(connection_factory(settings.database_url), probe)
    if args.json:
        print(json.dumps({"version": installed_version(), **report.as_dict()}, indent=2))
    else:
        print(f"corei-os {installed_version()}: {'READY' if report.ready else 'NOT READY'}")
        for c in report.checks:
            print(f"  {'ok ' if c.ok else 'FAIL'} {c.name:<9} {c.detail}")
    return 0 if report.ready else EXIT_NOT_READY


def cmd_backup(args: argparse.Namespace, env: dict[str, str]) -> int:
    from corei.infra.db import connection_factory

    settings = _settings(env)
    with connection_factory(settings.database_url)() as conn:
        has_schema = conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()
    if not has_schema or has_schema[0] is None:
        print("backup: database has no COREI schema yet; nothing to back up")
        return 0
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise CliError("pg_dump not found; install postgresql-client")
    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / f"corei-{args.label}-{stamp}.dump"
    subprocess.run(
        [pg_dump, "--format=custom", "--no-owner", f"--file={target}", settings.database_url],
        check=True,
    )
    target.chmod(0o600)
    dumps = sorted(out_dir.glob("corei-*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dumps[args.keep :]:
        old.unlink()
    print(f"backup: {target}")
    return 0


def cmd_topology(args: argparse.Namespace, env: dict[str, str]) -> int:
    from corei.infra.topology_admin import ensure

    settings = _settings(env)
    if not settings.kafka_bootstrap:
        raise CliError("KAFKA_BOOTSTRAP is not set")
    problems = ensure(settings.kafka_bootstrap, settings.environment, create=not args.verify)
    for p in problems:
        print(p)
    print(f"kafka topology: {'FAIL' if problems else 'OK'} ({len(problems)} problem(s))")
    return 1 if problems else 0


def cmd_bootstrap(args: argparse.Namespace, env: dict[str, str]) -> int:
    from corei.bootstrap import bootstrap
    from corei.domain.timeutil import SystemClock

    settings = _settings(env)
    config = yaml.safe_load(Path(args.profile).read_text())
    print(bootstrap(config, settings, SystemClock()))
    return 0


def build_app(env: dict[str, str]) -> FastAPI:
    """Operator API from settings (no principals configured: every authenticated call fails)."""
    from corei.api.app import AppContext, create_app
    from corei.control.auth import TokenAuthenticator
    from corei.domain.control import ExecutionMode
    from corei.domain.timeutil import SystemClock
    from corei.infra.db import connection_factory

    settings = _settings(env)
    mode = ExecutionMode(env.get("COREI_ENVELOPE_MODE", "PAPER"))
    if mode not in settings.allowed_modes:
        raise CliError(f"COREI_ENVELOPE_MODE {mode} is not in COREI_ALLOWED_MODES")
    ctx = AppContext(
        connect=connection_factory(settings.database_url),
        authenticator=TokenAuthenticator.from_config(env.get("COREI_API_PRINCIPALS", "")),
        clock=SystemClock(),
        environment=settings.environment,
        tenant_id=settings.tenant_id,
        envelope_mode=mode,
        kafka_probe=_kafka_probe(settings.kafka_bootstrap) if settings.kafka_bootstrap else None,
    )
    return create_app(ctx)


def cmd_api(_args: argparse.Namespace, env: dict[str, str]) -> int:
    import uvicorn

    app = build_app(env)
    host = env.get("COREI_API_HOST", "127.0.0.1")
    port = int(env.get("COREI_API_PORT", "8780"))
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
    return 0


def relay_forever(
    relay_once: Callable[[], int], should_stop: Callable[[], bool], idle_s: float = 0.2
) -> int:
    """Publish outbox batches until stopped; back off (up to 30 s) while Kafka fails."""
    total, backoff = 0, 1.0
    while not should_stop():
        try:
            n = relay_once()
        except Exception as exc:  # broker or database unavailable: keep rows, retry later
            log.warning("relay failed (%s); retrying in %.0fs", type(exc).__name__, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            continue
        backoff = 1.0
        total += n
        if n == 0:
            time.sleep(idle_s)
    return total


def cmd_relay(args: argparse.Namespace, env: dict[str, str]) -> int:
    from corei.domain.timeutil import SystemClock
    from corei.infra.db import connection_factory
    from corei.infra.kafka import KafkaPublisher
    from corei.infra.processing import relay_outbox

    settings = _settings(env)
    if not settings.kafka_bootstrap:
        raise CliError("KAFKA_BOOTSTRAP is not set")
    connect = connection_factory(settings.database_url)
    publisher = KafkaPublisher(settings.kafka_bootstrap)
    clock = SystemClock()

    def once() -> int:
        return relay_outbox(connect, publisher, clock)

    if args.once:
        print(f"relayed {once()} event(s)")
        return 0
    log.info("outbox relay started (environment %s)", settings.environment)
    relay_forever(once, _stop_flag())
    return 0


def cmd_ledger_consumer(_args: argparse.Namespace, env: dict[str, str]) -> int:
    from corei.domain.timeutil import SystemClock
    from corei.infra.db import connection_factory
    from corei.infra.kafka import run_consumer
    from corei.infra.processing import Processor
    from corei.infra.topics import topic_for
    from corei.ledger.handlers import make_fill_handler

    settings = _settings(env)
    if not settings.kafka_bootstrap:
        raise CliError("KAFKA_BOOTSTRAP is not set")
    clock = SystemClock()
    processor = Processor(
        consumer="ledger",
        environment=settings.environment,
        allowed_modes=settings.allowed_modes,
        handlers={"order.fill": make_fill_handler(clock.now_ns)},
        connect=connection_factory(settings.database_url),
        clock=clock,
    )
    topic = topic_for("order.fill", settings.environment)
    log.info("ledger consumer started on %s", topic)
    counts = run_consumer(
        settings.kafka_bootstrap,
        f"corei.{settings.environment}.ledger",
        [topic],
        processor.process_raw,
        _stop_flag(),
    )
    log.info("ledger consumer stopped: %s", counts)
    return 0


def _require_root() -> None:
    if os.geteuid() != 0:
        raise CliError("this command must run as root (use sudo)")


def provision_sql(password: str) -> str:
    """Idempotent SQL that creates the corei role and database (password is URL-safe base64)."""
    if not password or not all(c.isalnum() or c in "-_" for c in password):
        raise CliError("generated password has unexpected characters")
    return (
        "SELECT 'CREATE ROLE corei LOGIN' WHERE NOT EXISTS "
        "(SELECT FROM pg_roles WHERE rolname = 'corei')\\gexec\n"
        f"ALTER ROLE corei WITH LOGIN PASSWORD '{password}';\n"
        "SELECT 'CREATE DATABASE corei OWNER corei' WHERE NOT EXISTS "
        "(SELECT FROM pg_database WHERE datname = 'corei')\\gexec\n"
    )


def cmd_setup_local(args: argparse.Namespace, env: dict[str, str]) -> int:
    _require_root()
    path = Path(env.get("COREI_ENV_FILE", str(DEFAULT_ENV_FILE)))
    current = parse_env_file(path.read_text()) if path.exists() else {}
    updates: dict[str, str] = {}
    if current.get("DATABASE_URL"):
        print("setup-local: DATABASE_URL already configured; database left as it is")
    else:
        password = secrets.token_urlsafe(32)
        subprocess.run(
            ["runuser", "-u", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1", "-q", "-X"],
            input=provision_sql(password),
            text=True,
            check=True,
            cwd="/",
        )
        updates["DATABASE_URL"] = f"postgresql://corei:{password}@127.0.0.1:5432/corei"
        print("setup-local: created PostgreSQL role and database 'corei'")
    if not current.get("KAFKA_BOOTSTRAP") and args.kafka:
        updates["KAFKA_BOOTSTRAP"] = args.kafka
    if updates:
        update_env_file(path, updates)
        shutil.chown(path, "root", "corei")
    return 0


def cmd_auto_update(args: argparse.Namespace, _env: dict[str, str]) -> int:
    if args.action == "status":
        print(f"auto-update: {'on' if AUTO_UPDATE_FILE.exists() else 'off'}")
        return 0
    _require_root()
    if args.action == "on":
        AUTO_UPDATE_FILE.write_text(AUTO_UPDATE_BODY)
        print("auto-update: on (unattended-upgrades will install COREI OS updates)")
    else:
        AUTO_UPDATE_FILE.unlink(missing_ok=True)
        print("auto-update: off (update with: sudo apt update && sudo apt upgrade)")
    return 0


# ---------------------------------------------------------------- entry point


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="corei-os", description="COREI OS operator command")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("version").set_defaults(fn=cmd_version)
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("migrate")
    p.add_argument("--check", action="store_true")
    p.set_defaults(fn=cmd_migrate)
    p = sub.add_parser("backup")
    p.add_argument("--dir", default=str(DEFAULT_BACKUP_DIR))
    p.add_argument("--label", default="manual", choices=["manual", "pre-upgrade"])
    p.add_argument("--keep", type=int, default=10)
    p.set_defaults(fn=cmd_backup)
    p = sub.add_parser("topology")
    p.add_argument("--verify", action="store_true")
    p.set_defaults(fn=cmd_topology)
    p = sub.add_parser("bootstrap")
    p.add_argument("profile")
    p.set_defaults(fn=cmd_bootstrap)
    sub.add_parser("api").set_defaults(fn=cmd_api)
    p = sub.add_parser("relay")
    p.add_argument("--once", action="store_true")
    p.set_defaults(fn=cmd_relay)
    sub.add_parser("ledger-consumer").set_defaults(fn=cmd_ledger_consumer)
    p = sub.add_parser("setup-local")
    p.add_argument("--kafka", default=None, help="KAFKA_BOOTSTRAP to record if unset")
    p.set_defaults(fn=cmd_setup_local)
    p = sub.add_parser("auto-update")
    p.add_argument("action", choices=["on", "off", "status"])
    p.set_defaults(fn=cmd_auto_update)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        # `version` and `auto-update` need no settings, so they work without access to the env file.
        if args.command in NO_SETTINGS_COMMANDS:
            env = dict(os.environ)
        else:
            env = load_environment(dict(os.environ))
        result: int = args.fn(args, env)
        return result
    except CliError as exc:
        print(f"corei-os: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
