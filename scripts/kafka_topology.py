"""Create and verify the declared Kafka topology (F44, F45).

    uv run python scripts/kafka_topology.py --bootstrap localhost:9092 --env dev --apply
    uv run python scripts/kafka_topology.py --bootstrap localhost:9092 --env dev --verify

``--apply`` creates missing topics, then verifies. ``--verify`` only checks.
Exit code 1 if any topic is missing or differs (partitions, cleanup policy,
retention) — the legacy script printed "registered" without calling Kafka.
"""

from __future__ import annotations

import argparse
import sys

from confluent_kafka.admin import AdminClient, ConfigResource, NewTopic

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", required=True)
    ap.add_argument("--env", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    admin = AdminClient({"bootstrap.servers": args.bootstrap})
    specs = load_topology()
    if args.apply:
        apply(admin, args.env, specs)
    problems = verify(admin, args.env, specs)
    for p in problems:
        print(p)
    print(
        f"kafka topology: {'FAIL' if problems else 'OK'} "
        f"({len(specs)} topics, {len(problems)} problem(s))"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
