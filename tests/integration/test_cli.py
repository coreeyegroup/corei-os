"""`corei-os` against a real database: migrate, status, backup, bootstrap, API factory."""

import shutil
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from corei import cli

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
Connect = Callable[[], psycopg.Connection]


def env_for(database_url: str, tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "COREI_ENV": "it",
        "DATABASE_URL": database_url,
        "COREI_ENV_FILE": str(tmp_path / "none.env"),
        **extra,
    }


def run(argv: list[str], env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> int:
    for k in (
        "COREI_ENV",
        "DATABASE_URL",
        "KAFKA_BOOTSTRAP",
        "COREI_ENV_FILE",
        "COREI_API_PRINCIPALS",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return cli.main(argv)


def test_migrate_status_and_bootstrap(
    connect: Connect,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = env_for(database_url, tmp_path)
    assert run(["migrate", "--check"], env, monkeypatch) == 0
    assert run(["migrate"], env, monkeypatch) == 0
    assert "already at head" in capsys.readouterr().out
    assert run(["status"], env, monkeypatch) == cli.EXIT_NOT_READY  # no GLOBAL control yet
    assert "FAIL control" in capsys.readouterr().out
    profile = ROOT / "config/paper-bootstrap.yaml"
    assert run(["bootstrap", str(profile)], env, monkeypatch) == 0
    assert run(["status", "--json"], env, monkeypatch) == 0
    assert '"ready": true' in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("pg_dump") is None, reason="pg_dump not installed")
def test_backup_writes_a_restorable_dump_and_prunes(
    connect: Connect, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = env_for(database_url, tmp_path)
    out = tmp_path / "backups"
    for _ in range(3):
        assert run(["backup", "--dir", str(out), "--keep", "2"], env, monkeypatch) == 0
    dumps = sorted(out.glob("corei-manual-*.dump"))
    assert 1 <= len(dumps) <= 2 and all(p.stat().st_mode & 0o777 == 0o600 for p in dumps)


def test_api_factory_serves_health_and_requires_auth(
    connect: Connect, database_url: str, tmp_path: Path
) -> None:
    app = cli.build_app(env_for(database_url, tmp_path))
    client = TestClient(app)
    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/control/state").status_code == 401  # no principals configured


def test_unknown_envelope_mode_is_refused(database_url: str, tmp_path: Path) -> None:
    with pytest.raises(cli.CliError):
        cli.build_app(env_for(database_url, tmp_path, COREI_ENVELOPE_MODE="LIVE_AUTO"))
