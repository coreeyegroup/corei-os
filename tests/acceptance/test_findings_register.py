"""The findings register is the contract between the audit and the rebuild.

* Every finding F01-F50 is present exactly once, with the audit's severities.
* Every test a finding cites exists (file, function and, for vectors, the case).
* Every finding without full coverage shows up as an explicit xfail, so the
  test report always lists what is still open and in which delivery.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTER: list[dict[str, Any]] = yaml.safe_load((ROOT / "docs/findings/register.yaml").read_text())
STATUSES = {"covered", "partial", "pending", "process"}
DELIVERIES = set("ABCDEFGHI")
CRITICAL = {f"F{i:02d}" for i in range(1, 7)}
MEDIUM = {"F37", "F38"}


def _test_exists(nodeid: str) -> bool:
    m = re.fullmatch(r"(?P<file>[\w/.]+\.py)::(?P<func>\w+)(\[(?P<param>[^\]]+)\])?", nodeid)
    if not m:
        return False
    path = ROOT / m["file"]
    if not path.exists() or not re.search(rf"^def {m['func']}\(", path.read_text(), re.M):
        return False
    param = m["param"]
    if param and "invalid_vector" in m["func"]:
        return (ROOT / "contracts/vectors/invalid" / f"{param}.json").exists()
    return True


def test_register_is_complete_and_consistent() -> None:
    ids = [f["id"] for f in REGISTER]
    assert ids == [f"F{i:02d}" for i in range(1, 51)], "register must list F01..F50 in order"
    for f in REGISTER:
        expected = "critical" if f["id"] in CRITICAL else "medium" if f["id"] in MEDIUM else "high"
        assert f["severity"] == expected, f["id"]
        assert f["status"] in STATUSES, f["id"]
        assert f["delivery"] in DELIVERIES, f["id"]
        assert re.fullmatch(r"R(1[0-3]|[0-9])", f["gate"]), f["id"]
        if f["status"] in {"covered", "partial", "process"}:
            assert f.get("tests"), f"{f['id']} is {f['status']} but cites no tests"
        if f["status"] == "process":
            assert f.get("action"), f"{f['id']} needs the manual action spelled out"
        if f["status"] == "covered":
            assert f["delivery"] == "A", f"{f['id']} covered outside Delivery A?"


@pytest.mark.parametrize(
    "nodeid", sorted({t for f in REGISTER for t in f.get("tests", [])}), ids=str
)
def test_cited_test_exists(nodeid: str) -> None:
    assert _test_exists(nodeid), f"register cites a test that does not exist: {nodeid}"


OPEN = [f for f in REGISTER if f["status"] in {"partial", "pending"}]


@pytest.mark.parametrize("finding", OPEN, ids=lambda f: f["id"])
def test_open_finding_acceptance(finding: dict[str, Any]) -> None:
    pytest.xfail(
        f"{finding['id']} ({finding['severity']}) open until Delivery {finding['delivery']} "
        f"/ {finding['gate']}: {finding['title']}"
    )
