"""LLM-callable tools for setting up conda environments from workspace scripts.

Flow:
1. `scan_dependencies` — walk script/ (and optionally packages/) to detect
   Python imports (AST) and R library/require calls (regex).
2. `plan_environment` — generate three install plans (conda-only, mixed
   conda+pip, environment.yml) for user comparison.
3. `apply_environment_plan` — execute the chosen plan (or just render the
   commands for the user to copy).
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

from research_manager.context import get_workspace
from research_manager.executor.runner import ScriptRunner
from research_manager.tools.registry import tool

# Map "what you write in Python" → "what you pip install".
# Conservative; covers the common bioinformatics / scientific Python aliases.
_IMPORT_TO_PIP: dict[str, str] = {
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "Bio": "biopython",
    "anndata": "anndata",
    "scanpy": "scanpy",
}

# Packages we know are PyPI-only (no maintained conda-forge build by default).
# Kept small and conservative — everything else defaults to conda.
_PYPI_ONLY: set[str] = {
    "deepseek",
    "openai",
    "anthropic",
    "tiktoken",
}

_R_LIBRARY_RE = re.compile(r"\b(?:library|require)\s*\(\s*['\"]?([A-Za-z][A-Za-z0-9._]*)")
_PYTHON_VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")


def _parse_python_imports(path: Path) -> set[str]:
    """Return top-level module names imported by a Python file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


def _parse_r_libraries(path: Path) -> set[str]:
    """Return package names referenced by library()/require() in an R file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return set(_R_LIBRARY_RE.findall(text))


def _filter_stdlib(modules: set[str]) -> set[str]:
    return {m for m in modules if m not in sys.stdlib_module_names and not m.startswith("_")}


def _to_pip_name(import_name: str) -> str:
    return _IMPORT_TO_PIP.get(import_name, import_name)


@tool(name="scan_dependencies", category="dynamic")
def scan_dependencies_tool(include_packages: bool) -> str:
    """Scan workspace scripts for imported Python and R dependencies.

    Parses Python files with AST and R files with regex. Standard-library
    modules and submodule imports are normalized (top-level only).

    Args:
        include_packages: If true, also scan packages/<name>/src/ trees.
    """
    ws = get_workspace()
    script_dir = ws / "script"

    py_imports: set[str] = set()
    r_libs: set[str] = set()
    per_file: list[dict] = []

    targets: list[Path] = []
    if script_dir.exists():
        targets.extend(p for p in script_dir.rglob("*") if p.is_file())
    if include_packages:
        pkg_root = ws / "packages"
        if pkg_root.exists():
            for src_dir in pkg_root.glob("*/src"):
                targets.extend(p for p in src_dir.rglob("*") if p.is_file())

    for f in targets:
        ext = f.suffix.lower()
        if ext == ".py":
            mods = _filter_stdlib(_parse_python_imports(f))
            py_imports |= mods
            if mods:
                per_file.append({"path": str(f.relative_to(ws)), "python_imports": sorted(mods)})
        elif ext in (".r", ".rscript"):
            libs = _parse_r_libraries(f)
            r_libs |= libs
            if libs:
                per_file.append({"path": str(f.relative_to(ws)), "r_packages": sorted(libs)})

    return json.dumps(
        {
            "python_imports": sorted(py_imports),
            "r_packages": sorted(r_libs),
            "files_scanned": len(targets),
            "per_file": per_file,
        },
        ensure_ascii=False,
    )


def _validate_env_name(name: str) -> str | None:
    if not _ENV_NAME_RE.match(name):
        return f"invalid env name {name!r} — must start with a letter, use [A-Za-z0-9._-] only"
    return None


def _validate_python_version(version: str) -> str | None:
    if not _PYTHON_VERSION_RE.match(version):
        return f"invalid python version {version!r} — must look like '3.11' or '3.11.5'"
    return None


def _render_conda_only(env: str, py_pkgs: list[str], r_pkgs: list[str], create_new: bool, py_ver: str) -> dict:
    cmds: list[str] = []
    if create_new:
        cmds.append(f"conda create -n {env} python={py_ver} -y")
    conda_pkgs: list[str] = []
    fallback: list[str] = []
    for p in py_pkgs:
        pip_name = _to_pip_name(p)
        if p in _PYPI_ONLY or pip_name in _PYPI_ONLY:
            fallback.append(pip_name)
        else:
            conda_pkgs.append(pip_name)
    if conda_pkgs:
        cmds.append(f"conda install -n {env} -c conda-forge -y " + " ".join(conda_pkgs))
    if r_pkgs:
        r_conda = " ".join(f"r-{pkg.lower()}" for pkg in r_pkgs)
        cmds.append(f"conda install -n {env} -c conda-forge -y " + r_conda)
    return {"commands": cmds, "fallback_pypi_only": sorted(set(fallback))}


def _render_mixed(env: str, py_pkgs: list[str], r_pkgs: list[str], create_new: bool, py_ver: str) -> dict:
    cmds: list[str] = []
    if create_new:
        cmds.append(f"conda create -n {env} python={py_ver} -y")
    conda_pkgs: list[str] = []
    pip_pkgs: list[str] = []
    for p in py_pkgs:
        pip_name = _to_pip_name(p)
        if p in _PYPI_ONLY or pip_name in _PYPI_ONLY:
            pip_pkgs.append(pip_name)
        else:
            conda_pkgs.append(pip_name)
    if conda_pkgs:
        cmds.append(f"conda install -n {env} -c conda-forge -y " + " ".join(conda_pkgs))
    if r_pkgs:
        r_conda = " ".join(f"r-{pkg.lower()}" for pkg in r_pkgs)
        cmds.append(f"conda install -n {env} -c conda-forge -y " + r_conda)
    if pip_pkgs:
        cmds.append(f"conda run -n {env} pip install " + " ".join(pip_pkgs))
    return {"commands": cmds}


def _render_environment_yml(env: str, py_pkgs: list[str], r_pkgs: list[str], py_ver: str) -> dict:
    conda_pkgs: list[str] = [f"python={py_ver}"]
    pip_pkgs: list[str] = []
    for p in py_pkgs:
        pip_name = _to_pip_name(p)
        if p in _PYPI_ONLY or pip_name in _PYPI_ONLY:
            pip_pkgs.append(pip_name)
        else:
            conda_pkgs.append(pip_name)
    for pkg in r_pkgs:
        conda_pkgs.append(f"r-{pkg.lower()}")

    lines = [f"name: {env}", "channels:", "  - conda-forge", "dependencies:"]
    for c in conda_pkgs:
        lines.append(f"  - {c}")
    if pip_pkgs:
        lines.append("  - pip")
        lines.append("  - pip:")
        for p in pip_pkgs:
            lines.append(f"      - {p}")
    yml = "\n".join(lines) + "\n"
    return {
        "yml": yml,
        "apply_command": f"conda env create -f environment.yml" if not _env_exists(env) else f"conda env update -n {env} -f environment.yml",
    }


def _env_exists(env: str) -> bool:
    """Best-effort check; if conda is missing or the listing fails, return False."""
    runner = ScriptRunner(workspace=get_workspace(), default_timeout=30)
    try:
        result = runner.run_shell(command="conda env list", env=None, timeout=30)
    except Exception:
        return False
    if not result.ok:
        return False
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split()[0]
        if name == env:
            return True
    return False


@tool(name="plan_environment", category="dynamic")
def plan_environment_tool(
    python_packages: list[str],
    r_packages: list[str],
    target_env: str,
    python_version: str,
    create_new: bool,
) -> str:
    """Generate three install plans (conda-only, mixed, environment.yml) for review.

    Returns the plans as JSON; the user must confirm via the REPL or via
    apply_environment_plan before anything is installed.

    Args:
        python_packages: List of top-level Python import names (or PyPI names).
        r_packages: List of R package names (as referenced in library() calls).
        target_env: Conda environment name (existing or to be created).
        python_version: Python version string for create_new (e.g. "3.11").
        create_new: If true, prepend `conda create -n <env> python=<ver> -y`.
    """
    err = _validate_env_name(target_env)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)
    err = _validate_python_version(python_version)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)

    py = sorted(set(python_packages))
    r = sorted(set(r_packages))

    plan_a = _render_conda_only(target_env, py, r, create_new, python_version)
    plan_b = _render_mixed(target_env, py, r, create_new, python_version)
    plan_c = _render_environment_yml(target_env, py, r, python_version)

    return json.dumps(
        {
            "needs_user_confirmation": True,
            "target_env": target_env,
            "python_version": python_version,
            "create_new": create_new,
            "python_packages": py,
            "r_packages": r,
            "plans": {
                "conda_only": plan_a,
                "mixed": plan_b,
                "yml": plan_c,
            },
            "hint": "Show all three plans to the user, let them pick one, then call apply_environment_plan.",
        },
        ensure_ascii=False,
    )


@tool(name="apply_environment_plan", category="dynamic")
def apply_environment_plan_tool(
    plan: str,
    target_env: str,
    python_packages: list[str],
    r_packages: list[str],
    python_version: str,
    create_new: bool,
    execute: bool,
) -> str:
    """Render or execute one of the install plans.

    Args:
        plan: One of "conda_only", "mixed", "yml".
        target_env: Conda environment name.
        python_packages: Top-level Python import names (or PyPI names).
        r_packages: R package names.
        python_version: Python version (e.g. "3.11").
        create_new: If true, run `conda create -n <env> python=<ver> -y` first.
        execute: If true, run the commands; if false, only render them.
    """
    err = _validate_env_name(target_env)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)
    err = _validate_python_version(python_version)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)

    py = sorted(set(python_packages))
    r = sorted(set(r_packages))

    if plan == "conda_only":
        rendered = _render_conda_only(target_env, py, r, create_new, python_version)
        commands = rendered["commands"]
    elif plan == "mixed":
        rendered = _render_mixed(target_env, py, r, create_new, python_version)
        commands = rendered["commands"]
    elif plan == "yml":
        rendered = _render_environment_yml(target_env, py, r, python_version)
        yml_path = get_workspace() / "environment.yml"
        yml_path.write_text(rendered["yml"], encoding="utf-8")
        commands = [rendered["apply_command"]]
    else:
        return json.dumps({"error": f"unknown plan {plan!r} (use conda_only/mixed/yml)"}, ensure_ascii=False)

    if not execute:
        return json.dumps(
            {"ok": True, "executed": False, "commands": commands, "rendered": rendered},
            ensure_ascii=False,
        )

    runner = ScriptRunner(workspace=get_workspace(), default_timeout=1800)
    step_results = []
    overall_ok = True
    for cmd in commands:
        result = runner.run_shell(command=cmd, env=None, timeout=1800)
        step_results.append({
            "command": cmd,
            "ok": result.ok,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-400:] if result.stdout else "",
            "stderr_tail": result.stderr[-400:] if result.stderr else "",
        })
        if not result.ok:
            overall_ok = False
            break

    return json.dumps(
        {
            "ok": overall_ok,
            "executed": True,
            "plan": plan,
            "target_env": target_env,
            "steps": step_results,
        },
        ensure_ascii=False,
    )
