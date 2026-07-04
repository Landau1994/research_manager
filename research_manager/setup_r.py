"""Post-install helper for configuring R support in a conda environment."""

from __future__ import annotations

import json
import os
import selectors
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DEFAULT_R_VERSION = "4.5.3"
DEFAULT_R_PACKAGE = "tidyverse"
DEFAULT_R_SYSTEM_DEPS = ["pkg-config", "rust", "libuv", "curl", "libcurl"]
DEFAULT_R_REPOS = {
    "CRAN": "https://mirrors.ustc.edu.cn/CRAN",
    "BioCsoft": "https://mirrors.westlake.edu.cn/bioconductor/packages/3.22/bioc",
    "BioCann": "https://mirrors.westlake.edu.cn/bioconductor/packages/3.22/data/annotation",
}
DEFAULT_BIOC_MIRROR = "https://mirrors.westlake.edu.cn/bioconductor"
_CONFIGURE_REPOS_COMMAND = "easy-research:configure-r-repos"
_RPROFILE_START = "# >>> easy-research repositories >>>"
_RPROFILE_END = "# <<< easy-research repositories <<<"
_R_CONDA_ENV_CHECK_EXPR = (
    "prefix <- normalizePath(Sys.getenv('CONDA_PREFIX'), winslash = '/', mustWork = FALSE); "
    "r_home <- normalizePath(R.home(), winslash = '/', mustWork = FALSE); "
    "if (!nzchar(prefix) || !startsWith(r_home, paste0(prefix, '/'))) { "
    "cat(sprintf('R resolved outside conda env: R.home=%s CONDA_PREFIX=%s', r_home, prefix), "
    "file = stderr()); quit(status = 42) }; "
    "cat(as.character(getRversion()))"
)


@dataclass(frozen=True)
class RSetupStatus:
    conda_exe: str | None
    env_name: str
    r_installed: bool
    tidyverse_installed: bool
    repos_configured: bool
    missing_system_deps: list[str]
    r_version: str
    r_error: str
    tidyverse_error: str
    repos_error: str


def find_conda() -> str | None:
    """Return the configured conda executable, if available."""
    for env_var in ("CONDA_EXE",):
        exe = os.environ.get(env_var)
        if exe and shutil.which(exe):
            return exe
    return shutil.which("conda")


def current_conda_env(conda_exe: str | None = None) -> str:
    """Best-effort default environment name for interactive setup."""
    env = os.environ.get("CONDA_DEFAULT_ENV", "").strip()
    if env:
        return env

    conda = conda_exe or find_conda()
    if not conda:
        return "research-r"

    try:
        result = subprocess.run(
            [conda, "info", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "research-r"

    if result.returncode != 0:
        return "research-r"
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "research-r"
    return str(info.get("active_prefix_name") or "research-r")


def _run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _run_streaming(
    command: list[str],
    timeout: int,
    on_output: Callable[[str], None] | None,
) -> subprocess.CompletedProcess[str]:
    output_parts: list[str] = []
    start = time.monotonic()
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        while True:
            if time.monotonic() - start > timeout:
                proc.kill()
                raise subprocess.TimeoutExpired(command, timeout, output="".join(output_parts))

            events = selector.select(timeout=0.1)
            for key, _ in events:
                chunk = key.fileobj.readline()
                if not chunk:
                    continue
                output_parts.append(chunk)
                if on_output:
                    on_output(chunk.rstrip("\n"))

            if proc.poll() is not None:
                rest = proc.stdout.read()
                if rest:
                    output_parts.append(rest)
                    if on_output:
                        for line in rest.splitlines():
                            on_output(line)
                return subprocess.CompletedProcess(
                    command,
                    proc.returncode,
                    "".join(output_parts),
                    "",
                )
    finally:
        selector.close()
        proc.stdout.close()


def _rscript_expr(conda_exe: str, env_name: str, expr: str) -> subprocess.CompletedProcess[str]:
    return _run([conda_exe, "run", "-n", env_name, "Rscript", "-e", expr])


def _active_rscript_expr(expr: str) -> subprocess.CompletedProcess[str]:
    return _run(["Rscript", "-e", expr])


def _r_string(value: str) -> str:
    return json.dumps(value)


def _r_repo_config_check_expr() -> str:
    names = ", ".join(_r_string(name) for name in DEFAULT_R_REPOS)
    values = ", ".join(_r_string(value) for value in DEFAULT_R_REPOS.values())
    bioc_mirror = _r_string(DEFAULT_BIOC_MIRROR)
    return (
        f"repos <- getOption('repos'); "
        f"expected_names <- c({names}); "
        f"expected_values <- c({values}); "
        f"ok <- isTRUE(all(unname(repos[expected_names]) == expected_values)) && "
        f"isTRUE(getOption('BioC_mirror') == {bioc_mirror}); "
        "quit(status = ifelse(ok, 0, 1))"
    )


def _rprofile_repo_block() -> list[str]:
    repo_lines = [
        "options(repos = c(",
        *[
            f"  {name} = {_r_string(value)}{',' if idx < len(DEFAULT_R_REPOS) - 1 else ''}"
            for idx, (name, value) in enumerate(DEFAULT_R_REPOS.items())
        ],
        "))",
        f"options(BioC_mirror = {_r_string(DEFAULT_BIOC_MIRROR)})",
    ]
    return [_RPROFILE_START, "local({", *[f"  {line}" for line in repo_lines], "})", _RPROFILE_END]


def _replace_marked_block(lines: list[str], block: list[str]) -> list[str]:
    cleaned: list[str] = []
    idx = 0
    while idx < len(lines):
        if lines[idx] == _RPROFILE_START:
            end_idx = idx + 1
            while end_idx < len(lines) and lines[end_idx] != _RPROFILE_END:
                end_idx += 1
            if end_idx < len(lines):
                idx = end_idx + 1
                continue
        cleaned.append(lines[idx])
        idx += 1
    if cleaned and cleaned[-1] != "":
        cleaned.append("")
    return [*cleaned, *block]


def configure_r_repositories(
    conda_exe: str,
    env_name: str | None = None,
    *,
    use_active_rscript: bool = False,
) -> Path:
    """Write default R package repositories into the conda env's Rprofile.site."""
    expr = "cat(normalizePath(R.home('etc'), winslash = '/', mustWork = TRUE))"
    if use_active_rscript:
        result = _active_rscript_expr(expr)
    elif env_name:
        result = _rscript_expr(conda_exe, env_name, expr)
    else:
        raise RuntimeError("conda environment name is required when not using active Rscript")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or "failed to locate R.home('etc')")

    r_etc_text = result.stdout.strip()
    if not r_etc_text:
        raise RuntimeError("R.home('etc') returned an empty path")
    r_etc = Path(r_etc_text)
    profile = r_etc / "Rprofile.site"
    lines = profile.read_text(encoding="utf-8").splitlines() if profile.exists() else []
    profile.write_text("\n".join(_replace_marked_block(lines, _rprofile_repo_block())) + "\n", encoding="utf-8")
    return profile


def _missing_conda_packages(
    conda_exe: str,
    env_name: str,
    packages: list[str],
) -> list[str]:
    """Return packages that are not listed in the target conda environment."""
    if not packages:
        return []
    try:
        result = _run([conda_exe, "list", "-n", env_name, "--json"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return packages
    if result.returncode != 0:
        return packages
    try:
        installed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return packages
    installed_names = {
        str(item.get("name", "")).lower()
        for item in installed
        if isinstance(item, dict)
    }
    return [pkg for pkg in packages if pkg.lower() not in installed_names]


def inspect_r_setup(
    env_name: str,
    conda_exe: str | None = None,
    r_package: str = DEFAULT_R_PACKAGE,
) -> RSetupStatus:
    """Check whether R and the requested R package are usable in a conda env."""
    conda = conda_exe or find_conda()
    if not conda:
        return RSetupStatus(
            conda_exe=None,
            env_name=env_name,
            r_installed=False,
            tidyverse_installed=False,
            repos_configured=False,
            missing_system_deps=list(DEFAULT_R_SYSTEM_DEPS),
            r_version="",
            r_error="conda not found on PATH",
            tidyverse_error="conda not found on PATH",
            repos_error="conda not found on PATH",
        )

    r_version = ""
    r_error = ""
    tidyverse_error = ""
    repos_error = ""

    try:
        r_check = _rscript_expr(conda, env_name, _R_CONDA_ENV_CHECK_EXPR)
    except (OSError, subprocess.TimeoutExpired) as e:
        r_check = None
        r_error = str(e)

    r_installed = bool(r_check and r_check.returncode == 0)
    if r_check:
        r_version = r_check.stdout.strip()
        if not r_installed:
            r_error = (r_check.stderr or r_check.stdout).strip()

    tidyverse_installed = False
    if r_installed:
        try:
            pkg_check = _rscript_expr(
                conda,
                env_name,
                "quit(status = ifelse("
                f"requireNamespace({r_package!r}, quietly = TRUE), 0, 1))",
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            pkg_check = None
            tidyverse_error = str(e)
        tidyverse_installed = bool(pkg_check and pkg_check.returncode == 0)
        if pkg_check and not tidyverse_installed:
            tidyverse_error = (pkg_check.stderr or pkg_check.stdout).strip()
    else:
        tidyverse_error = "R is not installed"

    repos_configured = False
    if r_installed:
        try:
            repos_check = _rscript_expr(conda, env_name, _r_repo_config_check_expr())
        except (OSError, subprocess.TimeoutExpired) as e:
            repos_check = None
            repos_error = str(e)
        repos_configured = bool(repos_check and repos_check.returncode == 0)
        if repos_check and not repos_configured:
            repos_error = (repos_check.stderr or repos_check.stdout).strip()
    else:
        repos_error = "R is not installed"

    missing_system_deps = list(DEFAULT_R_SYSTEM_DEPS)
    if r_installed and not tidyverse_installed:
        missing_system_deps = _missing_conda_packages(conda, env_name, DEFAULT_R_SYSTEM_DEPS)
    elif tidyverse_installed:
        missing_system_deps = []

    return RSetupStatus(
        conda_exe=conda,
        env_name=env_name,
        r_installed=r_installed,
        tidyverse_installed=tidyverse_installed,
        repos_configured=repos_configured,
        missing_system_deps=missing_system_deps,
        r_version=r_version,
        r_error=r_error,
        tidyverse_error=tidyverse_error,
        repos_error=repos_error,
    )


def build_r_setup_commands(
    env_name: str,
    *,
    r_version: str = DEFAULT_R_VERSION,
    r_package: str = DEFAULT_R_PACKAGE,
    system_deps: list[str] | None = None,
    install_r: bool = True,
    configure_repos: bool = True,
    install_package: bool = True,
    use_active_rscript: bool = False,
) -> list[list[str]]:
    """Return command argv lists for missing R setup steps."""
    commands: list[list[str]] = []
    conda_packages: list[str] = []
    if install_r:
        conda_packages.append(f"r-base={r_version}")
    if install_package:
        deps = DEFAULT_R_SYSTEM_DEPS if system_deps is None else system_deps
        conda_packages.extend(deps)
    if conda_packages:
        commands.append([
            "conda",
            "install",
            "-n",
            env_name,
            "-c",
            "conda-forge",
            "-y",
            *conda_packages,
        ])
    if configure_repos:
        if use_active_rscript:
            commands.append([_CONFIGURE_REPOS_COMMAND])
        else:
            commands.append([_CONFIGURE_REPOS_COMMAND, "-n", env_name])
    if install_package:
        expr = f"install.packages({r_package!r})"
        if use_active_rscript:
            commands.append(["Rscript", "-e", expr])
        else:
            commands.append(["conda", "run", "-n", env_name, "Rscript", "-e", expr])
    return commands


def format_command(command: list[str], conda_exe: str | None = None) -> str:
    """Render a command for display, replacing the generic conda executable if known."""
    if command and command[0] == _CONFIGURE_REPOS_COMMAND:
        suffix = " ".join(shlex.quote(part) for part in command[1:])
        return "easy-research configure-r-repos" + (f" {suffix}" if suffix else "")
    rendered = list(command)
    if conda_exe and rendered and rendered[0] == "conda":
        rendered[0] = conda_exe
    return " ".join(shlex.quote(part) for part in rendered)


def execute_commands(
    commands: list[list[str]],
    *,
    conda_exe: str,
    timeout: int = 1800,
    on_step_start: Callable[[int, list[str]], None] | None = None,
    on_output: Callable[[int, str], None] | None = None,
    on_step_finish: Callable[[int, dict[str, str | int | bool]], None] | None = None,
) -> list[dict[str, str | int | bool]]:
    """Execute setup commands and return compact per-step results."""
    results: list[dict[str, str | int | bool]] = []
    for idx, command in enumerate(commands):
        argv = list(command)
        if argv and argv[0] == _CONFIGURE_REPOS_COMMAND:
            if on_step_start:
                on_step_start(idx, command)
            try:
                env_name = argv[argv.index("-n") + 1] if "-n" in argv else None
                profile = configure_r_repositories(
                    conda_exe,
                    env_name,
                    use_active_rscript=env_name is None,
                )
                step = {
                    "command": format_command(command, conda_exe),
                    "ok": True,
                    "returncode": 0,
                    "stdout_tail": f"wrote {profile}",
                    "stderr_tail": "",
                }
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
                step = {
                    "command": format_command(command, conda_exe),
                    "ok": False,
                    "returncode": -1,
                    "stdout_tail": "",
                    "stderr_tail": str(e),
                }
            results.append(step)
            if on_step_finish:
                on_step_finish(idx, step)
            if not step["ok"]:
                break
            continue
        if argv and argv[0] == "conda":
            argv[0] = conda_exe
        if on_step_start:
            on_step_start(idx, command)
        try:
            result = _run_streaming(
                argv,
                timeout=timeout,
                on_output=(lambda line, step=idx: on_output(step, line)) if on_output else None,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            step = {
                "command": format_command(command, conda_exe),
                "ok": False,
                "returncode": -1,
                "stdout_tail": "",
                "stderr_tail": str(e),
            }
            results.append(step)
            if on_step_finish:
                on_step_finish(idx, step)
            break
        ok = result.returncode == 0
        step = {
            "command": format_command(command, conda_exe),
            "ok": ok,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-800:] if result.stdout else "",
            "stderr_tail": result.stderr[-800:] if result.stderr else "",
        }
        results.append(step)
        if on_step_finish:
            on_step_finish(idx, step)
        if not ok:
            break
    return results
