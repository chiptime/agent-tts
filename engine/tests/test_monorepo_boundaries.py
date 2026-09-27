"""RF-AT-10-5: Monorepo Boundary Test.

Enforces strict one-way dependency rule:
- `hosts/*` may depend on `engine` via contracts and public interfaces.
- `engine/` MUST NEVER import, invoke, or depend on any code in `hosts/`.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_SRC = REPO_ROOT / "engine" / "src" / "agent_tts"


def test_engine_has_zero_imports_from_hosts():
    """Verify that no Python file in engine/src/ imports from hosts or host plugins."""
    assert ENGINE_SRC.is_dir(), f"engine source directory not found at {ENGINE_SRC}"

    forbidden_prefixes = ("hosts", "herdr_tts", "herdr_brain")
    violations = []

    for py_file in ENGINE_SRC.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception as exc:
            violations.append(f"Failed to parse {py_file}: {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prefix in forbidden_prefixes:
                        if alias.name == prefix or alias.name.startswith(f"{prefix}."):
                            violations.append(
                                f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} imports forbidden '{alias.name}'"
                            )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for prefix in forbidden_prefixes:
                        if node.module == prefix or node.module.startswith(f"{prefix}."):
                            violations.append(
                                f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} imports from forbidden '{node.module}'"
                            )

    assert not violations, "RF-AT-10-5 Boundary Violations found:\n" + "\n".join(violations)


def test_engine_has_no_hardcoded_hosts_path_references():
    """Ensure engine source files do not hardcode paths to hosts/ directory."""
    violations = []
    for py_file in ENGINE_SRC.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        for idx, line in enumerate(lines, 1):
            if "hosts/herdr" in line:
                violations.append(f"{py_file.relative_to(REPO_ROOT)}:{idx}: {line.strip()}")

    assert not violations, "RF-AT-10-5 hosts/ path references found in engine:\n" + "\n".join(violations)
