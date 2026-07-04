from __future__ import annotations

import subprocess
import sys

from research_manager import setup_r


def test_current_conda_env_prefers_active_env(monkeypatch) -> None:
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "active-env")
    assert setup_r.current_conda_env("/usr/bin/conda") == "active-env"


def test_find_conda_uses_conda_only(monkeypatch) -> None:
    monkeypatch.setenv("MAMBA_EXE", "/usr/bin/mamba")
    monkeypatch.delenv("CONDA_EXE", raising=False)

    def fake_which(exe):
        return {
            "mamba": "/usr/bin/mamba",
            "micromamba": "/usr/bin/micromamba",
            "conda": "/usr/bin/conda",
        }.get(exe)

    monkeypatch.setattr(setup_r.shutil, "which", fake_which)

    assert setup_r.find_conda() == "/usr/bin/conda"


def test_current_conda_env_falls_back_to_conda_info(monkeypatch) -> None:
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, '{"active_prefix_name": "from-info"}', "")

    monkeypatch.setattr(setup_r.subprocess, "run", fake_run)
    assert setup_r.current_conda_env("/usr/bin/conda") == "from-info"


def test_build_r_setup_commands_default_to_r_base_and_tidyverse() -> None:
    commands = setup_r.build_r_setup_commands("analysis")
    assert commands[0] == [
        "conda",
        "install",
        "-n",
        "analysis",
        "-c",
        "conda-forge",
        "-y",
        "r-base=4.5.3",
        "pkg-config",
        "rust",
        "libuv",
        "curl",
        "libcurl",
    ]
    assert commands[1] == ["easy-research:configure-r-repos", "-n", "analysis"]
    assert commands[2] == [
        "conda",
        "run",
        "-n",
        "analysis",
        "Rscript",
        "-e",
        "install.packages('tidyverse')",
    ]


def test_build_r_setup_commands_can_skip_installed_parts() -> None:
    commands = setup_r.build_r_setup_commands("analysis", install_r=False, install_package=True)
    assert len(commands) == 3
    assert commands[0][-5:] == ["pkg-config", "rust", "libuv", "curl", "libcurl"]
    assert commands[1] == ["easy-research:configure-r-repos", "-n", "analysis"]
    assert commands[2][:5] == ["conda", "run", "-n", "analysis", "Rscript"]


def test_build_r_setup_commands_skips_installed_system_deps() -> None:
    commands = setup_r.build_r_setup_commands(
        "analysis",
        system_deps=[],
        install_r=False,
        configure_repos=False,
        install_package=True,
    )
    assert commands == [
        [
            "conda",
            "run",
            "-n",
            "analysis",
            "Rscript",
            "-e",
            "install.packages('tidyverse')",
        ]
    ]


def test_build_r_setup_commands_uses_active_rscript() -> None:
    commands = setup_r.build_r_setup_commands(
        "analysis",
        system_deps=[],
        install_r=False,
        configure_repos=True,
        install_package=True,
        use_active_rscript=True,
    )
    assert commands == [
        ["easy-research:configure-r-repos"],
        ["Rscript", "-e", "install.packages('tidyverse')"],
    ]


def test_build_r_setup_commands_can_configure_repos_only() -> None:
    commands = setup_r.build_r_setup_commands(
        "analysis",
        install_r=False,
        configure_repos=True,
        install_package=False,
    )
    assert len(commands) == 1
    assert commands[0] == ["easy-research:configure-r-repos", "-n", "analysis"]


def test_format_configure_repos_command() -> None:
    assert (
        setup_r.format_command(["easy-research:configure-r-repos", "-n", "analysis"])
        == "easy-research configure-r-repos -n analysis"
    )
    assert setup_r.format_command(["easy-research:configure-r-repos"]) == "easy-research configure-r-repos"


def test_replace_marked_rprofile_block() -> None:
    block = setup_r._rprofile_repo_block()
    updated = setup_r._replace_marked_block(
        [
            "old <- TRUE",
            "# >>> easy-research repositories >>>",
            "stale",
            "# <<< easy-research repositories <<<",
            "keep <- TRUE",
        ],
        block,
    )

    assert "old <- TRUE" in updated
    assert "keep <- TRUE" in updated
    assert "stale" not in updated
    assert updated[-len(block):] == block
    assert "https://mirrors.ustc.edu.cn/CRAN" in "\n".join(updated)


def test_missing_conda_packages_returns_only_missing(monkeypatch) -> None:
    def fake_run(command, timeout=120):
        assert command == ["/usr/bin/conda", "list", "-n", "analysis", "--json"]
        return subprocess.CompletedProcess(
            command,
            0,
            '[{"name": "pkg-config"}, {"name": "rust"}, {"name": "curl"}]',
            "",
        )

    monkeypatch.setattr(setup_r, "_run", fake_run)

    assert setup_r._missing_conda_packages(
        "/usr/bin/conda",
        "analysis",
        ["pkg-config", "rust", "libuv", "curl", "libcurl"],
    ) == ["libuv", "libcurl"]


def test_build_r_setup_commands_skip_system_deps_when_no_package_install() -> None:
    commands = setup_r.build_r_setup_commands(
        "analysis",
        install_r=True,
        configure_repos=False,
        install_package=False,
    )
    assert len(commands) == 1
    assert commands[0][-1] == "r-base=4.5.3"
    assert "--override-channels" not in commands[0]


def test_execute_commands_streams_output() -> None:
    seen = []
    started = []
    finished = []

    results = setup_r.execute_commands(
        [[sys.executable, "-c", "print('hello from setup')"]],
        conda_exe="/usr/bin/conda",
        timeout=30,
        on_step_start=lambda idx, command: started.append((idx, command)),
        on_output=lambda idx, line: seen.append((idx, line)),
        on_step_finish=lambda idx, step: finished.append((idx, step["ok"])),
    )

    assert results[0]["ok"]
    assert started[0][0] == 0
    assert seen == [(0, "hello from setup")]
    assert finished == [(0, True)]


def test_execute_commands_configures_repos_with_python_writer(monkeypatch, tmp_path) -> None:
    r_etc = tmp_path / "R" / "etc"
    r_etc.mkdir(parents=True)
    (r_etc / "Rprofile.site").write_text("custom <- TRUE\n", encoding="utf-8")

    def fake_rscript_expr(conda_exe, env_name, expr):
        assert conda_exe == "/usr/bin/conda"
        assert env_name == "analysis"
        assert "R.home('etc')" in expr
        return subprocess.CompletedProcess(["Rscript"], 0, str(r_etc), "")

    monkeypatch.setattr(setup_r, "_rscript_expr", fake_rscript_expr)

    results = setup_r.execute_commands(
        [["easy-research:configure-r-repos", "-n", "analysis"]],
        conda_exe="/usr/bin/conda",
    )

    assert results[0]["ok"]
    profile = (r_etc / "Rprofile.site").read_text(encoding="utf-8")
    assert "custom <- TRUE" in profile
    assert "https://mirrors.ustc.edu.cn/CRAN" in profile
    assert "https://mirrors.westlake.edu.cn/bioconductor/packages/3.22/bioc" in profile


def test_execute_commands_configures_repos_with_active_rscript(monkeypatch, tmp_path) -> None:
    r_etc = tmp_path / "R" / "etc"
    r_etc.mkdir(parents=True)

    def fake_active_rscript_expr(expr):
        assert "R.home('etc')" in expr
        return subprocess.CompletedProcess(["Rscript"], 0, str(r_etc), "")

    monkeypatch.setattr(setup_r, "_active_rscript_expr", fake_active_rscript_expr)

    results = setup_r.execute_commands(
        [["easy-research:configure-r-repos"]],
        conda_exe="/usr/bin/conda",
    )

    assert results[0]["ok"]
    profile = (r_etc / "Rprofile.site").read_text(encoding="utf-8")
    assert "https://mirrors.ustc.edu.cn/CRAN" in profile


def test_inspect_r_setup_without_conda(monkeypatch) -> None:
    monkeypatch.setattr(setup_r, "find_conda", lambda: None)
    status = setup_r.inspect_r_setup("analysis")
    assert status.conda_exe is None
    assert not status.r_installed
    assert not status.tidyverse_installed


def test_inspect_r_setup_rejects_r_outside_conda_env(monkeypatch) -> None:
    calls = []

    def fake_rscript_expr(conda_exe, env_name, expr):
        calls.append((conda_exe, env_name, expr))
        return subprocess.CompletedProcess(
            ["conda", "run", "-n", env_name, "Rscript"],
            42,
            "",
            "R resolved outside conda env: R.home=/usr/lib/R CONDA_PREFIX=/opt/conda/envs/analysis",
        )

    monkeypatch.setattr(setup_r, "_rscript_expr", fake_rscript_expr)

    status = setup_r.inspect_r_setup("analysis", "/usr/bin/conda")

    assert not status.r_installed
    assert not status.tidyverse_installed
    assert "outside conda env" in status.r_error
    assert "CONDA_PREFIX" in calls[0][2]
