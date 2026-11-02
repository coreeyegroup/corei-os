"""The installed `corei-os` command: argument handling and environment-file rules."""

from pathlib import Path

import pytest

from corei import cli


def test_env_file_parsing_handles_comments_quotes_and_blanks() -> None:
    text = "# comment\n\nCOREI_ENV=prod\nDATABASE_URL='postgresql://x'\nEMPTY=\n"
    assert cli.parse_env_file(text) == {
        "COREI_ENV": "prod",
        "DATABASE_URL": "postgresql://x",
        "EMPTY": "",
    }
    with pytest.raises(cli.CliError):
        cli.parse_env_file("not a setting\n")


def test_environment_variables_win_over_the_env_file(tmp_path: Path) -> None:
    f = tmp_path / "corei.env"
    f.write_text("COREI_ENV=fromfile\nDATABASE_URL=postgresql://file\n")
    env = cli.load_environment({"COREI_ENV_FILE": str(f), "COREI_ENV": "fromenv"})
    assert env["COREI_ENV"] == "fromenv"
    assert env["DATABASE_URL"] == "postgresql://file"
    assert cli.load_environment({"COREI_ENV_FILE": str(tmp_path / "missing")})["COREI_ENV_FILE"]


def test_update_env_file_keeps_other_lines_and_restricts_mode(tmp_path: Path) -> None:
    f = tmp_path / "corei.env"
    f.write_text("# keep me\nCOREI_ENV=prod\n#DATABASE_URL=\n")
    cli.update_env_file(f, {"DATABASE_URL": "postgresql://new", "KAFKA_BOOTSTRAP": "h:9092"})
    lines = f.read_text().splitlines()
    assert lines[:3] == ["# keep me", "COREI_ENV=prod", "DATABASE_URL=postgresql://new"]
    assert lines[3] == "KAFKA_BOOTSTRAP=h:9092"
    assert f.stat().st_mode & 0o777 == 0o640


def test_provision_sql_is_idempotent_and_rejects_unsafe_passwords() -> None:
    sql = cli.provision_sql("abc-DEF_123")
    assert "WHERE NOT EXISTS" in sql and "\\gexec" in sql and "'abc-DEF_123'" in sql
    with pytest.raises(cli.CliError):
        cli.provision_sql("x'; DROP ROLE corei; --")


def test_missing_settings_are_a_clear_error() -> None:
    with pytest.raises(cli.CliError, match="COREI_ENV"):
        cli._settings({})


def test_live_modes_cannot_be_configured() -> None:
    with pytest.raises(cli.CliError, match="LIVE"):
        cli._settings(
            {
                "COREI_ENV": "x1",
                "DATABASE_URL": "postgresql://x",
                "COREI_ALLOWED_MODES": "LIVE_AUTO",
            }
        )


def test_relay_loop_backs_off_on_failure_and_stops() -> None:
    calls = iter([RuntimeError("kafka down"), 3, 0])
    stops = iter([False, False, False, True])

    def once() -> int:
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    sleeps: list[float] = []
    real_sleep = cli.time.sleep
    cli.time.sleep = sleeps.append  # type: ignore[assignment]
    try:
        assert cli.relay_forever(once, lambda: next(stops), idle_s=0.01) == 3
    finally:
        cli.time.sleep = real_sleep
    assert sleeps == [1.0, 0.01]


def test_parser_knows_every_service_command() -> None:
    parser = cli.build_parser()
    for argv in (
        ["api"],
        ["relay"],
        ["ledger-consumer"],
        ["migrate", "--check"],
        ["status", "--json"],
        ["backup", "--label", "pre-upgrade"],
        ["topology", "--verify"],
        ["auto-update", "status"],
    ):
        assert parser.parse_args(argv).fn
