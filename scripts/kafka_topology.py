"""Create and verify the declared Kafka topology (F44, F45).

    uv run python scripts/kafka_topology.py --bootstrap localhost:9092 --env dev --apply
    uv run python scripts/kafka_topology.py --bootstrap localhost:9092 --env dev --verify

``--apply`` creates missing topics, then verifies. ``--verify`` only checks.
Exit code 1 if any topic is missing or differs (partitions, cleanup policy,
retention). Installed systems use ``corei-os topology`` instead.
"""

from __future__ import annotations

import argparse
import sys

from corei.infra.topics import load_topology
from corei.infra.topology_admin import ensure


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", required=True)
    ap.add_argument("--env", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    problems = ensure(args.bootstrap, args.env, create=args.apply)
    for p in problems:
        print(p)
    print(
        f"kafka topology: {'FAIL' if problems else 'OK'} "
        f"({len(load_topology())} topics, {len(problems)} problem(s))"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
