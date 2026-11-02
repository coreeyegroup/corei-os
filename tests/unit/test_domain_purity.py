"""Static guard: domain code never reads the wall clock or uses randomness (F16).

The only sanctioned wall-clock reader is ``SystemClock`` in ``timeutil.py``.
"""

import ast
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[2] / "src" / "corei" / "domain"
FORBIDDEN_CALLS = {
    ("time", "time"),
    ("time", "time_ns"),
    ("time", "monotonic"),
    ("time", "perf_counter"),
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("uuid", "uuid1"),
    ("uuid", "uuid4"),
}
FORBIDDEN_MODULES = {"random", "secrets", "requests", "httpx", "aiokafka", "socket"}
ALLOWED = {("timeutil.py", "SystemClock")}


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []

    class V(ast.NodeVisitor):
        cls: str | None = None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            prev, self.cls = self.cls, node.name
            self.generic_visit(node)
            self.cls = prev

        def visit_Import(self, node: ast.Import) -> None:
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_MODULES:
                    found.append(f"{path.name}:{node.lineno} imports {a.name}")

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module and node.module.split(".")[0] in FORBIDDEN_MODULES:
                found.append(f"{path.name}:{node.lineno} imports {node.module}")

        def visit_Call(self, node: ast.Call) -> None:
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and (f.value.id, f.attr) in FORBIDDEN_CALLS
                and (path.name, self.cls) not in ALLOWED
            ):
                found.append(f"{path.name}:{node.lineno} calls {f.value.id}.{f.attr}()")
            self.generic_visit(node)

    V().visit(tree)
    return found


def test_domain_is_pure() -> None:
    problems = [v for p in sorted(DOMAIN.glob("*.py")) for v in _violations(p)]
    assert not problems, "\n".join(problems)
