"""Declared Kafka topology: event type -> topic, with expected topic settings."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import yaml

from corei.contracts.envelope import REGISTRY


class TopologyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TopicSpec:
    family: str
    event_types: tuple[str, ...]
    partitions: int
    replication_factor: int
    retention_ms: int
    cleanup_policy: str

    def name(self, environment: str) -> str:
        return f"corei.{environment}.{self.family}"

    def configs(self) -> dict[str, str]:
        return {"cleanup.policy": self.cleanup_policy, "retention.ms": str(self.retention_ms)}


@cache
def load_topology() -> tuple[TopicSpec, ...]:
    raw: dict[str, Any] = yaml.safe_load(
        resources.files("corei.infra").joinpath("kafka_topics.yaml").read_text("utf-8")
    )
    defaults = raw["defaults"]
    specs = []
    for family, cfg in raw["families"].items():
        merged = defaults | (cfg or {})
        specs.append(
            TopicSpec(
                family=family,
                event_types=tuple(merged["event_types"]),
                partitions=int(merged["partitions"]),
                replication_factor=int(merged["replication_factor"]),
                retention_ms=int(merged["retention_ms"]),
                cleanup_policy=str(merged["cleanup_policy"]),
            )
        )
    _validate(specs)
    return tuple(specs)


def _validate(specs: list[TopicSpec]) -> None:
    seen: dict[str, str] = {}
    for spec in specs:
        if spec.cleanup_policy not in {"delete", "compact"}:
            raise TopologyError(f"{spec.family}: unsupported cleanup policy {spec.cleanup_policy}")
        if spec.partitions < 1 or spec.replication_factor < 1:
            raise TopologyError(f"{spec.family}: partitions and replication must be >= 1")
        for et in spec.event_types:
            if et not in REGISTRY:
                raise TopologyError(f"{spec.family}: unknown event type {et}")
            if et in seen:
                raise TopologyError(f"{et} mapped to both {seen[et]} and {spec.family}")
            seen[et] = spec.family
    unmapped = set(REGISTRY) - set(seen)
    if unmapped:
        raise TopologyError(f"event types without a topic: {sorted(unmapped)}")


def topic_for(event_type: str, environment: str) -> str:
    for spec in load_topology():
        if event_type in spec.event_types:
            return spec.name(environment)
    raise TopologyError(f"no topic for {event_type}")
