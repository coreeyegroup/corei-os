"""Authentication and authorization for operator commands (findings F05, F06).

Tokens are never stored: configuration holds ``sha256(token)`` per principal.
Each control command type requires a role; LIVE modes are refused outright in
this build (live activation is a separately authorized delivery, C01).

    COREI_API_PRINCIPALS="alice:operator:<sha256hex>;rita:risk_officer:<sha256hex>"
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import StrEnum

from corei.domain.control import LIVE_MODES, ControlCommandType, ExecutionMode


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    RISK_OFFICER = "risk_officer"
    SYSTEM = "system"  # automated controls, e.g. the reconciler raising a halt


class AuthError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class Principal:
    name: str
    roles: frozenset[Role]


@dataclass(frozen=True, slots=True)
class _Entry:
    principal: Principal
    token_sha256: bytes


class TokenAuthenticator:
    def __init__(self, entries: list[_Entry]) -> None:
        names = [e.principal.name for e in entries]
        if len(names) != len(set(names)):
            raise ValueError("duplicate principal names")
        self._entries = entries

    @staticmethod
    def from_config(config: str) -> TokenAuthenticator:
        entries = []
        for item in filter(None, (s.strip() for s in config.split(";"))):
            name, roles, digest = item.split(":")
            if len(digest) != 64:
                raise ValueError(f"principal {name}: token hash must be sha256 hex")
            entries.append(
                _Entry(
                    Principal(name, frozenset(Role(r) for r in roles.split(","))),
                    bytes.fromhex(digest),
                )
            )
        return TokenAuthenticator(entries)

    def authenticate(self, token: str | None) -> Principal:
        if not token:
            raise AuthError("missing bearer token")
        digest = hashlib.sha256(token.encode()).digest()
        match = None
        for entry in self._entries:  # compare against every entry: constant-time per entry
            if hmac.compare_digest(entry.token_sha256, digest):
                match = entry.principal
        if match is None:
            raise AuthError("invalid token")
        return match


_REQUIRED: dict[ControlCommandType, frozenset[Role]] = {
    ControlCommandType.HALT_NEW_ORDERS: frozenset({Role.OPERATOR, Role.RISK_OFFICER, Role.SYSTEM}),
    ControlCommandType.CANCEL_OPEN_ORDERS: frozenset({Role.OPERATOR, Role.RISK_OFFICER}),
    ControlCommandType.RELEASE_HALT: frozenset({Role.RISK_OFFICER}),
    ControlCommandType.FLATTEN_POSITIONS: frozenset({Role.RISK_OFFICER}),
    ControlCommandType.SET_MODE: frozenset({Role.RISK_OFFICER}),
}


def authorize(
    principal: Principal, command: ControlCommandType, mode: ExecutionMode | None
) -> Role:
    """Return the role that grants the command, or raise ``AuthError``."""
    if command is ControlCommandType.SET_MODE and mode in LIVE_MODES:
        raise AuthError("LIVE modes cannot be enabled in this build (Delivery H)")
    granted = principal.roles & _REQUIRED[command]
    if not granted:
        need = ", ".join(sorted(r.value for r in _REQUIRED[command]))
        raise AuthError(f"{principal.name} may not {command.value}; requires one of: {need}")
    return sorted(granted)[0]
