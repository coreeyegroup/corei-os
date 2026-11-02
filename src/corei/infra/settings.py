"""Runtime settings from the environment (finding F44).

One bootstrap address, one database URL, one environment name for every
component. There are deliberately no defaults pointing at cluster hostnames:
a missing setting fails at startup instead of silently talking to the wrong
Kafka or database.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from corei.domain.control import ExecutionMode

_ENV_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")


class SettingsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    database_url: str
    kafka_bootstrap: str | None
    tenant_id: str
    allowed_modes: frozenset[ExecutionMode]

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)
        missing = [k for k in ("COREI_ENV", "DATABASE_URL") if not e.get(k)]
        if missing:
            raise SettingsError(f"missing required settings: {', '.join(missing)}")
        environment = e["COREI_ENV"]
        if not _ENV_RE.match(environment):
            raise SettingsError(f"invalid COREI_ENV {environment!r}")
        modes = frozenset(
            ExecutionMode(m.strip())
            for m in e.get("COREI_ALLOWED_MODES", "PAPER,BACKTEST").split(",")
            if m.strip()
        )
        live = {ExecutionMode.LIVE_SEMI, ExecutionMode.LIVE_AUTO} & modes
        if live:
            raise SettingsError(
                "LIVE modes cannot be enabled in this build; live activation is Delivery H (C01)"
            )
        return Settings(
            environment=environment,
            database_url=e["DATABASE_URL"],
            kafka_bootstrap=e.get("KAFKA_BOOTSTRAP") or None,
            tenant_id=e.get("COREI_TENANT", "corei"),
            allowed_modes=modes,
        )

    def topic_prefix(self) -> str:
        return f"corei.{self.environment}"
