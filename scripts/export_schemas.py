"""Write language-neutral JSON Schemas for every registered contract.

uv run python scripts/export_schemas.py           # regenerate
uv run python scripts/export_schemas.py --check   # CI: fail on drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from corei.contracts.envelope import REGISTRY, EventEnvelope

OUT = Path(__file__).resolve().parents[1] / "contracts" / "schemas"


def render() -> dict[str, str]:
    files: dict[str, str] = {}
    for name, et in sorted(REGISTRY.items()):
        schema = et.model.model_json_schema(mode="serialization")
        schema["$id"] = f"https://corei.local/schemas/{name}.v{et.version}.json"
        schema["x-corei-event-type"] = name
        schema["x-corei-schema-version"] = et.version
        files[f"{name}.v{et.version}.json"] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    env = EventEnvelope.model_json_schema(mode="serialization")
    env["$id"] = "https://corei.local/schemas/envelope.v1.json"
    files["envelope.v1.json"] = json.dumps(env, indent=2, sort_keys=True) + "\n"
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if files differ")
    args = parser.parse_args()
    wanted = render()
    existing = {p.name: p.read_text() for p in OUT.glob("*.json")}
    if args.check:
        drift = sorted(n for n in set(wanted) | set(existing) if wanted.get(n) != existing.get(n))
        if drift:
            print("JSON Schemas are out of date; run `make schemas`:", *drift, sep="\n  ")
            return 1
        print(f"{len(wanted)} schemas up to date")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in set(existing) - set(wanted):
        (OUT / stale).unlink()
    for name, text in wanted.items():
        (OUT / name).write_text(text)
    print(f"wrote {len(wanted)} schemas to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
