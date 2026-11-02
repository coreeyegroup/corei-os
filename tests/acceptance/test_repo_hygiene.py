import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("repo_hygiene", ROOT / "scripts" / "repo_hygiene.py")
assert _spec and _spec.loader
hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hygiene)


def test_no_secret_material_tracked() -> None:
    """F01: no private keys, Vault init files or tokens in the repository."""
    issues = [p for p in hygiene.problems() if "secret" in p]
    assert not issues, "\n".join(issues)


def test_no_vendored_or_runtime_artifacts_tracked() -> None:
    """F37: dependencies, runtime output and archives are rebuilt, never committed."""
    assert not hygiene.problems(), "\n".join(hygiene.problems())


def test_hygiene_detects_legacy_patterns() -> None:
    bad_paths = [
        "node_modules/x/index.js",
        "runtime/stage-14/events/a.json",
        "secrets/gitops/argocd-repo-key.pem",
        "state/security/kafka/ca.key",
        "state/security/vault/init.json",
        "backup.tar.gz",
        ".env",
    ]
    for p in bad_paths:
        assert hygiene.FORBIDDEN_PATH.search(p), p
    assert not hygiene.FORBIDDEN_PATH.search(".env.example")
    assert not hygiene.FORBIDDEN_PATH.search("src/corei/runtime_config.py")
    # Built from parts so this file itself never contains a contiguous secret marker.
    fake_key = b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----"
    fake_vault = b'{"root_' + b'token": "s.abc"}'
    assert any(r.search(fake_key) for r in hygiene.SECRET_CONTENT)
    assert any(r.search(fake_vault) for r in hygiene.SECRET_CONTENT)


def test_ci_workflow_is_real() -> None:
    """F33: the CI definition exists and actually runs the checks."""
    wf = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for required in (
        "pytest",
        "ruff",
        "mypy",
        "export_schemas.py --check",
        "make_vectors.py --check",
        "repo_hygiene.py",
        "gitleaks",
    ):
        assert required in wf, f"CI does not run {required}"
