"""LLM-callable tools for building pip-installable packages from workspace scripts.

Two-phase pattern:
1. `build_package` — validates inputs, returns a manifest for user confirmation
2. `confirm_package_build` — actually writes the package structure to packages/<name>/
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from research_manager.context import get_workspace
from research_manager.tools.registry import tool

_VALID_PKG_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

_PYPROJECT_TEMPLATE = """\
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "{version}"
description = "{description}"
requires-python = ">=3.11"
dependencies = [
{deps}]

[tool.setuptools.packages.find]
where = ["src"]
"""

_README_TEMPLATE = """\
# {name}

{description}

## Installation

```bash
cd packages/{name}
pip install -e .
```

## Usage

```python
import {name}
```
"""

_TEST_TEMPLATE = """\
\"\"\"Basic tests for {name}.\"\"\"


def test_import():
    import {name}
    assert hasattr({name}, "__version__")
"""


def _safe_under(workspace: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(workspace.resolve())
        return True
    except ValueError:
        return False


def _validate_name(name: str) -> str | None:
    if not _VALID_PKG_NAME.match(name):
        return (
            f"invalid package name {name!r} — must match [a-z][a-z0-9_]* "
            "(lowercase, start with letter, underscores ok)"
        )
    return None


def _render_pyproject(name: str, version: str, description: str, dependencies: list[str]) -> str:
    deps_lines = ""
    for dep in dependencies:
        deps_lines += f'    "{dep}",\n'
    return _PYPROJECT_TEMPLATE.format(
        name=name, version=version, description=description, deps=deps_lines,
    )


def _render_init(package_name: str, modules: list[str]) -> str:
    lines = [f'"""Package {package_name}."""', "", '__version__ = "0.1.0"', ""]
    for mod in modules:
        stem = Path(mod).stem
        lines.append(f"from .{stem} import *  # noqa: F401,F403")
    lines.append("")
    return "\n".join(lines)


@tool(name="build_package", category="dynamic")
def build_package_tool(
    package_name: str,
    description: str,
    scripts: list[str],
    version: str,
    dependencies: list[str],
) -> str:
    """Propose building a pip-installable package from workspace scripts.

    This tool validates inputs and returns a manifest for user confirmation.
    It does NOT write any files — call confirm_package_build after user approval.

    Args:
        package_name: Python package name (lowercase, underscores ok, e.g. "my_analysis").
        description: One-line description for pyproject.toml.
        scripts: List of filenames from script/ to include (e.g. ["clean.py", "utils.py"]).
        version: Package version string (e.g. "0.1.0").
        dependencies: Python package dependencies (e.g. ["numpy>=1.24", "pandas"]).
    """
    err = _validate_name(package_name)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)

    ws = get_workspace()
    pkg_dir = ws / "packages" / package_name
    if pkg_dir.exists():
        return json.dumps(
            {"error": f"packages/{package_name} already exists; pass overwrite=true to confirm_package_build to replace"},
            ensure_ascii=False,
        )

    missing = [s for s in scripts if not (ws / "script" / s).exists()]
    if missing:
        return json.dumps(
            {"error": f"scripts not found in script/: {missing}"},
            ensure_ascii=False,
        )

    non_py = [s for s in scripts if not s.endswith(".py")]
    if non_py:
        return json.dumps(
            {"error": f"only .py scripts can be packaged, got: {non_py}"},
            ensure_ascii=False,
        )

    if not scripts and not description:
        return json.dumps(
            {"error": "at least one script or a description is required"},
            ensure_ascii=False,
        )

    manifest = [
        f"packages/{package_name}/pyproject.toml",
        f"packages/{package_name}/README.md",
        f"packages/{package_name}/src/{package_name}/__init__.py",
    ]
    for s in scripts:
        manifest.append(f"packages/{package_name}/src/{package_name}/{s}")
    manifest.append(f"packages/{package_name}/tests/__init__.py")
    manifest.append(f"packages/{package_name}/tests/test_{package_name}.py")

    return json.dumps(
        {
            "needs_user_confirmation": True,
            "package_name": package_name,
            "description": description,
            "version": version,
            "scripts": scripts,
            "dependencies": dependencies,
            "manifest": manifest,
            "hint": "Show the manifest to the user and ask for confirmation before calling confirm_package_build.",
        },
        ensure_ascii=False,
    )


@tool(name="confirm_package_build", category="dynamic")
def confirm_package_build_tool(
    package_name: str,
    scripts: list[str],
    description: str,
    version: str,
    dependencies: list[str],
    overwrite: bool,
) -> str:
    """Write the package structure to packages/<package_name>/.

    Only call this after the user has confirmed the build_package manifest.

    Args:
        package_name: Python package name.
        scripts: List of filenames from script/ to copy into the package.
        description: One-line description.
        version: Package version string.
        dependencies: Python package dependencies.
        overwrite: If true, backup and replace an existing package directory.
    """
    err = _validate_name(package_name)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)

    ws = get_workspace()
    pkg_root = ws / "packages" / package_name

    if pkg_root.exists():
        if not overwrite:
            return json.dumps(
                {"error": f"packages/{package_name} already exists; set overwrite=true to replace"},
                ensure_ascii=False,
            )
        backup = ws / "packages" / f"{package_name}_backup_{int(time.time())}"
        shutil.move(str(pkg_root), str(backup))

    src_dir = pkg_root / "src" / package_name
    tests_dir = pkg_root / "tests"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    pyproject_content = _render_pyproject(package_name, version, description, dependencies)
    (pkg_root / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")

    readme_content = _README_TEMPLATE.format(name=package_name, description=description)
    (pkg_root / "README.md").write_text(readme_content, encoding="utf-8")

    copied: list[str] = []
    for script_name in scripts:
        src_file = ws / "script" / script_name
        if not src_file.exists():
            continue
        if not _safe_under(ws, src_file):
            continue
        dest = src_dir / script_name
        shutil.copy2(src_file, dest)
        copied.append(script_name)

    init_content = _render_init(package_name, copied)
    (src_dir / "__init__.py").write_text(init_content, encoding="utf-8")

    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    test_content = _TEST_TEMPLATE.format(name=package_name)
    (tests_dir / f"test_{package_name}.py").write_text(test_content, encoding="utf-8")

    files_created = [
        str(p.relative_to(ws))
        for p in sorted(pkg_root.rglob("*"))
        if p.is_file()
    ]

    return json.dumps(
        {
            "ok": True,
            "package_path": f"packages/{package_name}",
            "files_created": files_created,
            "install_hint": f"cd packages/{package_name} && pip install -e .",
        },
        ensure_ascii=False,
    )
