"""Create and verify the declared Kafka topology against a real cluster (F44, F45).

Used by ``corei-os topology`` and ``scripts/kafka_topology.py``. Verification
reads the cluster: it never reports success for a topic it did not find.
"""

from __future__ import annotations

from confluent_kafka.admin import (  # type: ignore[attr-defined]
    AdminClient,
    ConfigResource,
    NewTopic,
)

from corei.infra.topics import TopicSpec, load_topology


def verify(admin: AdminClient, env: str, specs: tuple[TopicSpec, ...]) -> list[str]:
    md = admin.list_topics(timeout=15)
    problems: list[str] = []
    resources = []
    for spec in specs:
        name = spec.name(env)
        topic = md.topics.get(name)
        if topic is None or topic.error is not None:
            problems.append(f"{name}: missing")
            continue
        if len(topic.partitions) != spec.partitions:
            problems.append(
                f"{name}: {len(topic.partitions)} partitions, expected {spec.partitions}"
            )
        resources.append((spec, ConfigResource(ConfigResource.Type.TOPIC, name)))
    if resources:
        futures = admin.describe_configs([r for _, r in resources])
        for spec, res in resources:
            cfg = futures[res].result(timeout=15)
            for key, want in spec.configs().items():
                got = cfg[key].value if key in cfg else None
                if got != want:
                    problems.append(f"{spec.name(env)}: {key}={got}, expected {want}")
    return problems


def apply(admin: AdminClient, env: str, specs: tuple[TopicSpec, ...]) -> None:
    existing = set(admin.list_topics(timeout=15).topics)
    new = [
        NewTopic(
            s.name(env),
            num_partitions=s.partitions,
            replication_factor=s.replication_factor,
            config=s.configs(),
        )
        for s in specs
        if s.name(env) not in existing
    ]
    if not new:
        return
    for name, fut in admin.create_topics(new, operation_timeout=30).items():
        fut.result(timeout=60)
        print(f"created {name}")


def ensure(bootstrap: str, env: str, *, create: bool) -> list[str]:
    """Optionally create missing topics, then return every problem found (empty = OK)."""
    admin = AdminClient({"bootstrap.servers": bootstrap})
    specs = load_topology()
    if create:
        apply(admin, env, specs)
    return verify(admin, env, specs)
