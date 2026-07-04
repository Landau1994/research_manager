from __future__ import annotations

import json
import subprocess

from research_manager.context import set_workspace
from research_manager import cli
from research_manager.executor.runner import ScriptRunner
from research_manager.tools.dynamic_tools import propose_script_tool, save_proposed_script_tool
from research_manager.tools.env_tools import scan_dependencies_tool
from research_manager.workspace.manager import init_workspace, validate_workspace


def test_init_workspace_creates_language_code_layout(tmp_path) -> None:
    result = init_workspace(tmp_path)

    assert (tmp_path / "code" / "r").is_dir()
    assert (tmp_path / "code" / "python").is_dir()
    assert (tmp_path / "code" / "bash").is_dir()
    assert (tmp_path / "code" / "r" / "setup_packages.R").is_file()
    assert (tmp_path / "code" / "bash" / "setup_packages.sh").is_file()
    assert "code/r/setup_packages.R" in result["created"]

    validation = validate_workspace(tmp_path)
    assert validation["ok"]


def test_scan_dependencies_reads_code_layout_and_legacy_script(tmp_path) -> None:
    init_workspace(tmp_path)
    set_workspace(tmp_path)
    (tmp_path / "code" / "python" / "analyze.py").write_text(
        "import pandas\nimport os\n", encoding="utf-8"
    )
    (tmp_path / "code" / "r" / "plot.R").write_text(
        "library(ggplot2)\n", encoding="utf-8"
    )
    (tmp_path / "script" / "legacy.py").write_text("import numpy\n", encoding="utf-8")

    result = json.loads(scan_dependencies_tool(include_packages=False))

    assert result["python_imports"] == ["numpy", "pandas"]
    assert result["r_packages"] == ["ggplot2"]


def test_save_proposed_script_uses_language_code_dir(tmp_path) -> None:
    init_workspace(tmp_path)
    set_workspace(tmp_path)

    proposal = json.loads(propose_script_tool(
        name="clean",
        language="python",
        code="print('ok')\n",
        description="test",
        run=False,
        conda_env="",
        timeout=30,
    ))
    saved = json.loads(save_proposed_script_tool(
        proposal_id=proposal["proposal_id"],
        target_name="",
        overwrite=False,
    ))

    assert saved["ok"]
    assert saved["saved_to"] == "code/python/clean.py"
    assert (tmp_path / "code" / "python" / "clean.py").is_file()


def test_runner_resolves_r_scripts_under_code_r(tmp_path) -> None:
    init_workspace(tmp_path)
    script = tmp_path / "code" / "r" / "analyze.R"
    script.write_text("cat('ok')\n", encoding="utf-8")

    runner = ScriptRunner(tmp_path)

    assert runner._resolve_script("analyze.R", language="r") == script


def test_startup_generates_requirements_from_placeholder(tmp_path, monkeypatch) -> None:
    init_workspace(tmp_path)
    req = tmp_path / "code" / "python" / "requirements.txt"

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "numpy==1.0\npandas==2.0\n", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    cli._maybe_generate_python_requirements(tmp_path)

    assert req.read_text(encoding="utf-8") == "numpy==1.0\npandas==2.0\n"


def test_startup_does_not_overwrite_user_requirements(tmp_path, monkeypatch) -> None:
    init_workspace(tmp_path)
    req = tmp_path / "code" / "python" / "requirements.txt"
    req.write_text("scanpy==1.10.0\n", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("pip freeze should not be called")

    monkeypatch.setattr(cli.subprocess, "run", fail_run)

    cli._maybe_generate_python_requirements(tmp_path)

    assert req.read_text(encoding="utf-8") == "scanpy==1.10.0\n"
