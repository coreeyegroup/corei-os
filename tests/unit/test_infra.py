import hashlib

import pytest

from corei.contracts.envelope import REGISTRY
from corei.control.auth import AuthError, Principal, Role, TokenAuthenticator, authorize
from corei.domain.control import ControlCommandType, ExecutionMode
from corei.infra.db import available_migrations
from corei.infra.settings import Settings, SettingsError
from corei.infra.topics import load_topology, topic_for


def test_settings_require_explicit_endpoints() -> None:
    """F44: no hidden default cluster hostnames; one bootstrap for every component."""
    with pytest.raises(SettingsError, match="COREI_ENV"):
        Settings.from_env({"DATABASE_URL": "postgresql://x"})
    s = Settings.from_env(
        {"COREI_ENV": "dev", "DATABASE_URL": "postgresql://x", "KAFKA_BOOTSTRAP": "localhost:9092"}
    )
    assert s.kafka_bootstrap == "localhost:9092" and s.topic_prefix() == "corei.dev"
    assert s.allowed_modes == {ExecutionMode.PAPER, ExecutionMode.SIMULATION}
    with pytest.raises(SettingsError, match="LIVE"):
        Settings.from_env(
            {"COREI_ENV": "dev", "DATABASE_URL": "x", "COREI_ALLOWED_MODES": "LIVE_SEMI"}
        )
    with pytest.raises(SettingsError):
        Settings.from_env({"COREI_ENV": "Prod!", "DATABASE_URL": "x"})


def test_every_event_type_has_exactly_one_topic() -> None:
    mapped = [et for spec in load_topology() for et in spec.event_types]
    assert sorted(mapped) == sorted(REGISTRY)
    assert topic_for("order.fill", "dev") == "corei.dev.orders.fills"
    compacted = {s.family for s in load_topology() if s.cleanup_policy == "compact"}
    assert "orders.fills" not in compacted  # money facts are never compacted away (C12)


def test_migrations_are_contiguous_and_shipped_in_package() -> None:
    migs = available_migrations()
    assert [m.version for m in migs] == list(range(1, len(migs) + 1))
    assert "CREATE TABLE inbox" in migs[0].sql


def test_tokens_are_hashed_and_roles_enforced() -> None:
    auth = TokenAuthenticator.from_config(f"ops:operator:{hashlib.sha256(b'secret-1').hexdigest()}")
    assert auth.authenticate("secret-1").name == "ops"
    for bad in (None, "", "secret-2"):
        with pytest.raises(AuthError):
            auth.authenticate(bad)
    op = Principal("ops", frozenset({Role.OPERATOR}))
    assert authorize(op, ControlCommandType.HALT_NEW_ORDERS, None) is Role.OPERATOR
    for cmd in (
        ControlCommandType.RELEASE_HALT,
        ControlCommandType.FLATTEN_POSITIONS,
        ControlCommandType.SET_MODE,
    ):
        with pytest.raises(AuthError):
            authorize(op, cmd, ExecutionMode.PAPER)
    risk = Principal("rita", frozenset({Role.RISK_OFFICER}))
    with pytest.raises(AuthError, match="LIVE"):
        authorize(risk, ControlCommandType.SET_MODE, ExecutionMode.LIVE_AUTO)
