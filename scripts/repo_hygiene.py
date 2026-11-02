"""Fail if the repository tracks secrets, vendored dependencies or runtime output.

Guards against the legacy failure modes: private keys and Vault init files in a
public repo (F01), 230k committed dependency/runtime files (F37), and empty
placeholder source files presented as implementations (F33, F46).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1_000_000

FORBIDDEN_PATH = re.compile(
    r"(^|/)(node_modules|site-packages|\.venv|venv|runtime|__pycache__|\.pytest_cache)(/|$)"
    r"|\.(key|pem|p12|jks|tar|tgz|tar\.gz|zip|bak|pyc)$"
    r"|(^|/)\.env($|\.(?!example$))"
    r"|(^|/)secrets/"
    r"|vault.*init.*\.json$|init\.json$"
)
SECRET_CONTENT = [
    re.compile(rb"-----BEGIN (RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    re.compile(rb'"root_token"\s*:\s*"[^"]+"'),
    re.compile(rb'"unseal_keys_(b64|hex)"\s*:\s*\[\s*"'),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"ghp_[A-Za-z0-9]{36}"),
]
EMPTY_ALLOWED = {"__init__.py", ".gitkeep", "py.typed"}
SOURCE_SUFFIXES = {".py", ".go", ".ts", ".tsx", ".js", ".yaml", ".yml", ".sql", ".sh", ".toml"}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        return [ROOT / p for p in out.decode().split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


def problems() -> list[str]:
    found: list[str] = []
    for path in tracked_files():
        if not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if FORBIDDEN_PATH.search(rel):
            found.append(
                f"{rel}: forbidden path (secret, vendored dependency, runtime output or archive)"
            )
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            found.append(
                f"{rel}: {size} bytes exceeds {MAX_BYTES}; large artifacts don't belong in Git"
            )
        if size == 0 and path.suffix in SOURCE_SUFFIXES and path.name not in EMPTY_ALLOWED:
            found.append(f"{rel}: empty source file (placeholders are not implementations)")
        if path.name == "repo_hygiene.py" or size > MAX_BYTES:
            continue
        data = path.read_bytes()
        for pattern in SECRET_CONTENT:
            if pattern.search(data):
                found.append(f"{rel}: looks like secret material ({pattern.pattern[:40]!r})")
    return found


def main() -> int:
    issues = problems()
    for line in issues:
        print(line)
    print(f"repo hygiene: {'FAIL' if issues else 'OK'} ({len(issues)} issue(s))")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
