"""CLI entry point for the research manager agent."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.syntax import Syntax
from rich.table import Table

from research_manager import __version__
from research_manager.cli_atrefs import expand_at_refs
from research_manager.cli_prompt import make_session, read_input
from research_manager.context import get_workspace, set_workspace
from research_manager.llm.client import ResearchLLMClient, sanitize_history
from research_manager.llm.prompts import BASE_SYSTEM_PROMPT, excluded_tools_for, writing_prompt_for
from research_manager.memory import (
    append_memory,
    freshest_resumable_slot,
    humanize_age,
    inject_into_system_prompt,
    load_memory,
)
from research_manager.recording import TrajectoryRecorder
from research_manager.sessions import auto_save, list_sessions, load_session, manual_save, pick_auto_slot
from research_manager.setup_r import (
    DEFAULT_R_PACKAGE,
    DEFAULT_R_VERSION,
    build_r_setup_commands,
    configure_r_repositories,
    current_conda_env,
    execute_commands,
    find_conda,
    format_command,
    inspect_r_setup,
)
from research_manager.tools import ToolRegistry  # noqa: F401  (triggers tool registration)
from research_manager.tools import external_access
from research_manager.workspace.manager import init_workspace, validate_workspace

console = Console()

_AUTO_APPROVE_PROPOSALS = False
_PYTHON_REQUIREMENTS_PLACEHOLDER = "# Add project-specific Python packages here"


def _print_banner() -> None:
    console.print(
        Panel(
            f"[bold]Research Manager[/bold] [dim]v{__version__}[/dim]\n"
            "[dim]LLM agent for managing research projects.[/dim]",
            border_style="cyan",
            padding=(0, 2),
        )
    )


def _print_tools() -> None:
    tools = ToolRegistry.list_tools()
    if not tools:
        console.print("[yellow]No tools registered.[/yellow]")
        return
    table = Table(title="Registered Tools", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Category", style="magenta")
    table.add_column("Description")
    for t in sorted(tools, key=lambda x: (x["category"], x["name"])):
        table.add_row(t["name"], t["category"], t["description"])
    console.print(table)


def _requirements_is_auto_placeholder(text: str) -> bool:
    stripped = text.strip()
    return not stripped or stripped.startswith(_PYTHON_REQUIREMENTS_PLACEHOLDER)


def _maybe_generate_python_requirements(ws: Path) -> None:
    """Create code/python/requirements.txt from the active Python environment.

    Existing user-maintained requirements are left untouched. The init scaffold's
    placeholder file is treated as empty so first launch can populate it.
    """
    python_dir = ws / "code" / "python"
    if not python_dir.exists():
        return

    req = python_dir / "requirements.txt"
    try:
        existing = req.read_text(encoding="utf-8") if req.exists() else ""
    except OSError:
        return
    if not _requirements_is_auto_placeholder(existing):
        return

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze", "--local"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        console.print(f"[dim yellow]could not generate code/python/requirements.txt: {e}[/dim yellow]")
        return
    if result.returncode != 0:
        msg = (result.stderr or result.stdout).strip()
        console.print(f"[dim yellow]could not generate code/python/requirements.txt: {msg[-300:]}[/dim yellow]")
        return

    content = result.stdout.strip()
    if content:
        content += "\n"
    else:
        content = "# No local pip packages detected in the active Python environment.\n"
    try:
        req.write_text(content, encoding="utf-8")
    except OSError as e:
        console.print(f"[dim yellow]could not write code/python/requirements.txt: {e}[/dim yellow]")
        return
    console.print(f"[dim]updated code/python/requirements.txt from active Python environment[/dim]")


def _print_help() -> None:
    console.print(
        "[dim]Input: include `@<path>` to attach a file or directory to your message.\n"
        "       Press Tab inside an `@<partial>` token to complete from the workspace\n"
        "       (and Tab on a leading `/` to complete commands).\n"
        "       Small text files are inlined; large/non-text files become a hint;\n"
        "       directories show a shallow listing. External paths must be /allow'd.\n\n"
        "Commands:\n"
        "  /tools          - list registered tools\n"
        "  /mode <kind>    - switch writing mode (base, article, blog, book)\n"
        "  /workspace      - show current workspace path\n"
        "  /allow <dir>    - approve a directory for external file reads\n"
        "  /allowed        - show approved external directories\n"
        "  /deny <dir>     - revoke a previously approved directory\n"
        "  /package <name> - build a pip-installable package from scripts\n"
        "  /env scan|plan  - scan deps and plan a conda environment\n"
        "  /sessions       - list saved and auto-saved sessions\n"
        "  /save [name]    - save current conversation (permanent)\n"
        "  /load <name>    - load a session (e.g. 'auto-0' or a saved name)\n"
        "  /branch [name]  - fork: save the current path under a new name\n"
        "  /remember <fact>- save a durable fact to MEMORY.md (loaded next session)\n"
        "                    optional `title :: fact` syntax for explicit headers\n"
        "  /memory         - show the current MEMORY.md\n"
        "  /good [note]    - mark the last response as good (recording mode)\n"
        "  /bad [note]     - mark the last response as bad (recording mode)\n"
        "  /outcome <kind> - tag session as success|partial|fail (recording)\n"
        "  /redo           - regenerate last response; reject saved as label\n"
        "  /reset          - clear conversation\n"
        "  /help           - show this help\n"
        "  /quit           - exit[/dim]"
    )


def _on_tool_call(name: str, args: dict, result: str) -> None:
    args_preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
    if len(args) > 3:
        args_preview += ", ..."
    console.print(f"[dim cyan]→ tool[/dim cyan] [bold]{name}[/bold]([dim]{args_preview}[/dim])")
    if name == "propose_script":
        _handle_proposal(args, result)
    if name == "build_package":
        _handle_package_confirmation(result)
    if name == "plan_environment":
        _handle_env_plan_confirmation(result)


def _print_proposal_preview(args: dict, parsed: dict) -> None:
    lang = parsed.get("language", "text")
    syntax_lang = {"python": "python", "r": "r", "shell": "bash"}.get(lang, "text")
    target_dir = parsed.get("target_dir") or "code"
    code = args.get("code", "")
    if not code:
        ws = get_workspace()
        proposal_path = parsed.get("proposal_path")
        if proposal_path:
            try:
                code = (ws / proposal_path).read_text(encoding="utf-8")
            except OSError:
                code = ""
    if code:
        console.print(
            Panel(
                Syntax(code, syntax_lang, line_numbers=True, theme="ansi_dark"),
                title=f"proposal {parsed.get('proposal_id', '')} → {target_dir}/{parsed.get('target_filename', '')}",
                border_style="yellow",
            )
        )


def _handle_proposal(args: dict, result: str) -> None:
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return
    if not parsed.get("needs_user_confirmation"):
        return

    proposal_id = parsed.get("proposal_id")
    target = parsed.get("target_filename", "")
    target_dir = parsed.get("target_dir") or "code"
    if not proposal_id:
        return

    _print_proposal_preview(args, parsed)

    if _AUTO_APPROVE_PROPOSALS:
        _save_proposal(proposal_id, target, overwrite=False, prompt_on_conflict=False)
        return

    if not sys.stdin.isatty():
        console.print(
            f"[dim]proposal {proposal_id} left in res/_proposals/ "
            "(non-interactive session — pass --auto-approve to save automatically)[/dim]"
        )
        return

    try:
        choice = console.input(
            f"[bold yellow]save proposal {proposal_id} → {target_dir}/{target}? "
            "(y/N/edit/rename): [/bold yellow]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]left in res/_proposals/[/dim]")
        return

    if choice in ("", "n", "no"):
        console.print(f"[dim]kept proposal in res/_proposals/ (id: {proposal_id})[/dim]")
        return

    if choice in ("e", "edit"):
        _edit_proposal(proposal_id)
        _save_proposal(proposal_id, target, overwrite=False, prompt_on_conflict=True)
        return

    if choice in ("r", "rename"):
        try:
            new_name = console.input("[yellow]new filename: [/yellow]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]cancelled[/dim]")
            return
        if not new_name:
            console.print("[dim]cancelled[/dim]")
            return
        _save_proposal(proposal_id, new_name, overwrite=False, prompt_on_conflict=True)
        return

    if choice in ("y", "yes"):
        _save_proposal(proposal_id, target, overwrite=False, prompt_on_conflict=True)
        return

    console.print(f"[yellow]unknown choice: {choice} — left in res/_proposals/[/yellow]")


def _edit_proposal(proposal_id: str) -> None:
    ws = get_workspace()
    proposals_dir = ws / "res" / "_proposals"
    matches = [p for p in proposals_dir.glob(f"{proposal_id}.*") if not p.name.endswith(".json")]
    if not matches:
        console.print(f"[red]proposal file not found for id {proposal_id}[/red]")
        return
    editor = os.environ.get("EDITOR", "vi")
    try:
        subprocess.run([editor, str(matches[0])], check=False)
    except FileNotFoundError:
        console.print(f"[red]editor not found: {editor}[/red]")


def _save_proposal(
    proposal_id: str,
    target_name: str,
    overwrite: bool,
    prompt_on_conflict: bool,
) -> None:
    result = ToolRegistry.call_tool(
        "save_proposed_script",
        {"proposal_id": proposal_id, "target_name": target_name, "overwrite": overwrite},
    )
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        console.print(f"[red]save failed: {result}[/red]")
        return

    if parsed.get("ok"):
        console.print(f"[green]saved → {parsed['saved_to']}[/green]")
        return

    err = parsed.get("error", "")
    if "already exists" in err and prompt_on_conflict and sys.stdin.isatty():
        try:
            choice = console.input(
                f"[yellow]{target_name} already exists — (o)verwrite / (r)ename / (c)ancel? [/yellow]"
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]cancelled[/dim]")
            return
        if choice in ("o", "overwrite"):
            _save_proposal(proposal_id, target_name, overwrite=True, prompt_on_conflict=False)
            return
        if choice in ("r", "rename"):
            try:
                new_name = console.input("[yellow]new filename: [/yellow]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]cancelled[/dim]")
                return
            if not new_name:
                console.print("[dim]cancelled[/dim]")
                return
            _save_proposal(proposal_id, new_name, overwrite=False, prompt_on_conflict=True)
            return
        console.print("[dim]cancelled — proposal kept in res/_proposals/[/dim]")
        return

    console.print(f"[red]save failed: {err}[/red]")


def _handle_package_confirmation(result: str) -> None:
    """Intercept build_package tool result and prompt user for confirmation."""
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return
    if not parsed.get("needs_user_confirmation"):
        return

    pkg_name = parsed.get("package_name", "")
    manifest = parsed.get("manifest", [])
    if not pkg_name or not manifest:
        return

    console.print(
        Panel(
            "\n".join(f"  {f}" for f in manifest),
            title=f"package: {pkg_name}",
            border_style="yellow",
        )
    )

    if _AUTO_APPROVE_PROPOSALS:
        _do_confirm_package(parsed)
        return

    if not sys.stdin.isatty():
        console.print("[dim]package build pending (non-interactive session)[/dim]")
        return

    try:
        choice = console.input(
            f"[bold yellow]build package '{pkg_name}'? (y/N): [/bold yellow]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return

    if choice in ("y", "yes"):
        _do_confirm_package(parsed)
    else:
        console.print("[dim]cancelled[/dim]")


def _do_confirm_package(parsed: dict) -> None:
    r = ToolRegistry.call_tool("confirm_package_build", {
        "package_name": parsed["package_name"],
        "scripts": parsed.get("scripts", []),
        "description": parsed.get("description", ""),
        "version": parsed.get("version", "0.1.0"),
        "dependencies": parsed.get("dependencies", []),
        "overwrite": False,
    })
    try:
        res = json.loads(r)
    except (json.JSONDecodeError, TypeError):
        console.print(f"[red]build failed: {r}[/red]")
        return
    if res.get("ok"):
        console.print(f"[green]built → {res['package_path']}/[/green]")
        for f in res.get("files_created", []):
            console.print(f"[dim]  {f}[/dim]")
        console.print(f"[dim]install: {res.get('install_hint', '')}[/dim]")
    else:
        console.print(f"[red]build failed: {res.get('error', '')}[/red]")


def _run_package_command(name: str, ws: Path) -> None:
    """Interactive /package <name> flow."""
    if not name:
        console.print("[yellow]usage: /package <name>[/yellow]")
        return

    py_scripts: list[str] = []
    for base in (ws / "code" / "python", ws / "script"):
        if base.exists():
            for f in sorted(base.iterdir()):
                if f.is_file() and f.suffix == ".py":
                    label = str(f.relative_to(ws))
                    if label not in py_scripts:
                        py_scripts.append(label)
    if not py_scripts:
        console.print("[yellow]no .py scripts found in code/python/ or script/[/yellow]")
        return

    console.print("[dim]Python scripts in workspace:[/dim]")
    for i, s in enumerate(py_scripts, 1):
        console.print(f"  [green]{i}.[/green] {s}")

    try:
        sel = console.input(
            "[bold yellow]include which scripts? (comma-separated numbers, 'all', or 'none'): [/bold yellow]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return

    if sel == "all":
        selected = py_scripts[:]
    elif sel in ("none", ""):
        selected = []
    else:
        try:
            indices = [int(x.strip()) for x in sel.split(",")]
            selected = [py_scripts[i - 1] for i in indices if 1 <= i <= len(py_scripts)]
        except (ValueError, IndexError):
            console.print("[red]invalid selection[/red]")
            return

    try:
        desc = console.input("[yellow]description: [/yellow]").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return

    try:
        deps_raw = console.input("[yellow]dependencies (comma-separated, or empty): [/yellow]").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return
    deps = [d.strip() for d in deps_raw.split(",") if d.strip()] if deps_raw else []

    console.print(f"\n[dim]package: {name}[/dim]")
    console.print(f"[dim]scripts: {selected or '(none)'}[/dim]")
    console.print(f"[dim]deps: {deps or '(none)'}[/dim]")

    try:
        confirm = console.input("[bold yellow]build? (y/N): [/bold yellow]").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return

    if confirm not in ("y", "yes"):
        console.print("[dim]cancelled[/dim]")
        return

    r = ToolRegistry.call_tool("confirm_package_build", {
        "package_name": name,
        "scripts": selected,
        "description": desc or f"Package {name}",
        "version": "0.1.0",
        "dependencies": deps,
        "overwrite": False,
    })
    try:
        res = json.loads(r)
    except (json.JSONDecodeError, TypeError):
        console.print(f"[red]build failed: {r}[/red]")
        return
    if res.get("ok"):
        console.print(f"\n[green]built → {res['package_path']}/[/green]")
        for f in res.get("files_created", []):
            console.print(f"[dim]  {f}[/dim]")
        console.print(f"\n[dim]{res.get('install_hint', '')}[/dim]")
    else:
        err = res.get("error", "")
        if "already exists" in err:
            try:
                ow = console.input("[yellow]package exists — overwrite? (y/N): [/yellow]").strip().lower()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]cancelled[/dim]")
                return
            if ow in ("y", "yes"):
                r2 = ToolRegistry.call_tool("confirm_package_build", {
                    "package_name": name,
                    "scripts": selected,
                    "description": desc or f"Package {name}",
                    "version": "0.1.0",
                    "dependencies": deps,
                    "overwrite": True,
                })
                res2 = json.loads(r2)
                if res2.get("ok"):
                    console.print(f"\n[green]rebuilt → {res2['package_path']}/[/green]")
                    for f in res2.get("files_created", []):
                        console.print(f"[dim]  {f}[/dim]")
                else:
                    console.print(f"[red]{res2.get('error', '')}[/red]")
            else:
                console.print("[dim]cancelled[/dim]")
        else:
            console.print(f"[red]build failed: {err}[/red]")


def _handle_env_plan_confirmation(result: str) -> None:
    """Intercept plan_environment tool result; show plans, prompt selection, optionally apply."""
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return
    if not parsed.get("needs_user_confirmation"):
        return

    target_env = parsed.get("target_env", "")
    plans = parsed.get("plans", {})
    if not plans:
        return

    _show_plan_panels(plans)

    if not sys.stdin.isatty() or _AUTO_APPROVE_PROPOSALS:
        console.print("[dim]plan_environment is informational — no auto-execution[/dim]")
        return

    _prompt_and_apply_plan(parsed, target_env)


def _show_plan_panels(plans: dict) -> None:
    a = plans.get("conda_only", {})
    b = plans.get("mixed", {})
    c = plans.get("yml", {})

    a_body = "\n".join(a.get("commands", []))
    if a.get("fallback_pypi_only"):
        a_body += f"\n\n[dim]not in conda-forge: {', '.join(a['fallback_pypi_only'])}[/dim]"
    console.print(Panel(a_body or "(empty)", title="Plan A — conda only", border_style="cyan"))

    b_body = "\n".join(b.get("commands", []))
    console.print(Panel(b_body or "(empty)", title="Plan B — conda + pip", border_style="green"))

    c_body = c.get("yml", "") + "\n[dim]apply with:[/dim]\n" + c.get("apply_command", "")
    console.print(Panel(c_body, title="Plan C — environment.yml", border_style="magenta"))


def _prompt_and_apply_plan(parsed: dict, target_env: str) -> None:
    try:
        choice = console.input(
            "[bold yellow]choose plan [A/B/C/cancel]: [/bold yellow]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return

    plan_map = {"a": "conda_only", "b": "mixed", "c": "yml"}
    plan_key = plan_map.get(choice)
    if not plan_key:
        console.print("[dim]cancelled[/dim]")
        return

    try:
        run_choice = console.input(
            "[bold yellow]execute now? (y/N/show): [/bold yellow]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return

    execute = run_choice in ("y", "yes")
    if not execute and run_choice not in ("show", "s"):
        console.print("[dim]cancelled[/dim]")
        return

    args = {
        "plan": plan_key,
        "target_env": target_env,
        "python_packages": parsed.get("python_packages", []),
        "r_packages": parsed.get("r_packages", []),
        "python_version": parsed.get("python_version", "3.11"),
        "create_new": parsed.get("create_new", False),
        "execute": execute,
    }
    r = ToolRegistry.call_tool("apply_environment_plan", args, timeout=1800)
    try:
        res = json.loads(r)
    except (json.JSONDecodeError, TypeError):
        console.print(f"[red]apply failed: {r}[/red]")
        return

    if not execute:
        console.print("[dim]commands rendered (not executed):[/dim]")
        for cmd in res.get("commands", []):
            console.print(f"  {cmd}")
        return

    if res.get("ok"):
        console.print(f"[green]done — env '{target_env}' set up via plan {plan_key}[/green]")
    else:
        console.print(f"[red]apply failed; check the steps below[/red]")
    for step in res.get("steps", []):
        marker = "[green]✓[/green]" if step.get("ok") else "[red]✗[/red]"
        console.print(f"{marker} {step['command']}")
        if step.get("stderr_tail") and not step.get("ok"):
            console.print(f"[dim red]{step['stderr_tail']}[/dim red]")


def _run_env_command(arg: str, ws: Path) -> None:
    """REPL /env scan|plan dispatcher."""
    parts = arg.split(maxsplit=1) if arg else []
    sub = parts[0].lower() if parts else ""

    if sub == "scan":
        include_pkg = "--all" in (parts[1:] or [])
        r = ToolRegistry.call_tool("scan_dependencies", {"include_packages": include_pkg})
        try:
            res = json.loads(r)
        except (json.JSONDecodeError, TypeError):
            console.print(f"[red]scan failed: {r}[/red]")
            return
        py = res.get("python_imports", [])
        r_pkgs = res.get("r_packages", [])
        console.print(f"[dim]files scanned: {res.get('files_scanned', 0)}[/dim]")
        console.print(f"[bold]python:[/bold] {', '.join(py) if py else '(none)'}")
        console.print(f"[bold]R:[/bold]      {', '.join(r_pkgs) if r_pkgs else '(none)'}")
        return

    if sub == "plan":
        _interactive_env_plan(ws)
        return

    console.print(
        "[dim]usage:\n"
        "  /env scan [--all]   - scan code/ and legacy script/ for deps\n"
        "  /env plan           - interactively plan a conda env from scanned deps[/dim]"
    )


def _interactive_env_plan(ws: Path) -> None:
    scan = json.loads(ToolRegistry.call_tool("scan_dependencies", {"include_packages": False}))
    py = scan.get("python_imports", [])
    r_pkgs = scan.get("r_packages", [])
    if not py and not r_pkgs:
        console.print("[yellow]nothing to plan — no imports found in code/ or script/[/yellow]")
        return

    console.print(f"[bold]python deps:[/bold] {', '.join(py) if py else '(none)'}")
    console.print(f"[bold]R deps:[/bold]      {', '.join(r_pkgs) if r_pkgs else '(none)'}")

    try:
        env_input = console.input(
            "[yellow]target env (existing name, or 'new:<name>'): [/yellow]"
        ).strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return
    if not env_input:
        console.print("[dim]cancelled[/dim]")
        return

    create_new = env_input.startswith("new:")
    target_env = env_input.split(":", 1)[1].strip() if create_new else env_input
    if not target_env:
        console.print("[red]empty env name[/red]")
        return

    py_ver = "3.11"
    if create_new:
        try:
            v = console.input("[yellow]python version [3.11]: [/yellow]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]cancelled[/dim]")
            return
        if v:
            py_ver = v

    plan_result = ToolRegistry.call_tool("plan_environment", {
        "python_packages": py,
        "r_packages": r_pkgs,
        "target_env": target_env,
        "python_version": py_ver,
        "create_new": create_new,
    })
    parsed = json.loads(plan_result)
    if parsed.get("error"):
        console.print(f"[red]{parsed['error']}[/red]")
        return

    _show_plan_panels(parsed.get("plans", {}))
    _prompt_and_apply_plan(parsed, target_env)


def _maybe_make_recorder(
    cli_record: bool,
    ws: Path,
    model: str,
    mode: str,
) -> TrajectoryRecorder | None:
    """Create a recorder if --record or RM_RECORD=1; otherwise None."""
    if not cli_record and os.environ.get("RM_RECORD", "").lower() not in ("1", "true", "yes", "on"):
        return None
    keep = int(os.environ.get("RM_RECORD_KEEP", "50"))
    rec = TrajectoryRecorder(workspace=ws, model=model, mode=mode, retention_keep=keep)
    rec.start()
    return rec


def _build_system_prompt(mode: str, workspace: Path) -> str:
    """Compose the system prompt: base/mode prompt + MEMORY.md (if any)."""
    base = BASE_SYSTEM_PROMPT if mode == "base" else writing_prompt_for(mode)
    return inject_into_system_prompt(base, load_memory(workspace))


def _make_client(mode: str = "base", workspace: Path | None = None) -> ResearchLLMClient:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        console.print("[bold red]error:[/bold red] OPENAI_API_KEY is not set.")
        console.print("Create a .env file or export the variable. See .env.example.")
        sys.exit(1)

    ws = workspace or get_workspace()
    system_prompt = _build_system_prompt(mode, ws)
    client = ResearchLLMClient(
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        model=os.getenv("RM_MODEL") or None,
        system_prompt=system_prompt,
    )
    client.set_excluded_tools(excluded_tools_for(mode))
    return client


def _do_chat(client: ResearchLLMClient, user_input: str) -> str:
    expanded, notes = expand_at_refs(user_input, get_workspace())
    for n in notes:
        console.print(f"[dim]{n}[/dim]")
    response = client.chat(expanded, on_tool_call=_on_tool_call)
    console.print()
    console.print(Markdown(response))
    return response


def _has_user_turns(messages: list[dict]) -> bool:
    return any(m.get("role") == "user" for m in messages)


def _find_last_user_idx(messages: list[dict]) -> int | None:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return i
    return None


def _ask_save_on_exit(client: ResearchLLMClient, ws: Path, mode: str) -> None:
    """Prompt the user to manually save the conversation before exiting."""
    if not _has_user_turns(client.messages):
        return
    if not sys.stdin.isatty():
        return
    try:
        choice = console.input(
            "[yellow]save this conversation before exiting? (y/N): [/yellow]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if choice not in ("y", "yes"):
        return
    try:
        name = console.input("[yellow]name (leave blank for timestamp): [/yellow]").strip()
    except (EOFError, KeyboardInterrupt):
        return
    sp = manual_save(ws, name, client.messages, model=client.model, mode=mode)
    console.print(f"[green]saved → {sp.relative_to(ws)}[/green]")


def _print_sessions(ws: Path) -> None:
    info = list_sessions(ws)
    table = Table(title="Sessions", show_header=True, header_style="bold cyan")
    table.add_column("Name", style="green")
    table.add_column("Updated", style="dim")
    table.add_column("Model", style="dim")
    table.add_column("Mode", style="dim")
    table.add_column("Turns", justify="right")
    for slot in info["auto"]:
        if slot is None:
            continue
        table.add_row(
            slot["slot"], slot["updated_at"][:19], slot["model"],
            slot["mode"], str(slot["turns"]),
        )
    for s in info["saved"]:
        table.add_row(
            s["name"], s["updated_at"][:19], s["model"],
            s["mode"], str(s["turns"]),
        )
    console.print(table)


def _maybe_offer_resume(client: ResearchLLMClient, ws: Path, mode: str) -> tuple[Path | None, str]:
    """Prompt the user whether to resume the freshest auto-save.

    Returns ``(slot_path_or_None_if_skipped, mode_after)``. The mode may
    change if the resumed session was in a different writing mode.
    Skipped silently when stdin isn't a TTY (batch / piped input).
    """
    if not sys.stdin.isatty():
        return None, mode
    if os.environ.get("RM_NO_RESUME", "").lower() in ("1", "true", "yes", "on"):
        return None, mode
    cand = freshest_resumable_slot(ws)
    if cand is None:
        return None, mode

    age = humanize_age(cand["age_s"])
    preview = cand["last_user"] or "(no preview)"
    console.print(
        f"[yellow]found a recent session:[/yellow] [bold]{cand['name']}[/bold] "
        f"[dim]({cand['turns']} turns, {age}, mode={cand['mode']})[/dim]"
    )
    console.print(f"[dim]  last user turn: {preview}[/dim]")
    try:
        choice = console.input(
            "[bold yellow]resume? (y/N): [/bold yellow]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]starting fresh[/dim]")
        return None, mode
    if choice not in ("y", "yes"):
        console.print("[dim]starting fresh[/dim]")
        return None, mode

    new_mode = cand["mode"]
    if new_mode != mode:
        client.set_system_prompt(_build_system_prompt(new_mode, ws))
        client.set_excluded_tools(excluded_tools_for(new_mode))

    client.messages = sanitize_history(cand["messages"])
    console.print(
        f"[green]resumed {cand['name']}[/green] [dim]({cand['turns']} turns)[/dim]"
    )
    return cand["path"], new_mode


def _run_interactive(mode: str, record: bool = False) -> None:
    _print_banner()
    ws = set_workspace(Path.cwd())
    _maybe_generate_python_requirements(ws)
    client = _make_client(mode=mode, workspace=ws)

    auto_slot = pick_auto_slot(ws)
    recorder = _maybe_make_recorder(record, ws, client.model, mode)
    if recorder is not None:
        client.set_recorder(recorder)

    # Single PromptSession for the whole REPL — keeps history across turns
    # and gives us proper CJK-aware line editing + `@<path>` Tab completion.
    prompt_session = make_session(console, get_workspace, history_dir=ws)

    console.print(f"[dim]model: {client.model}[/dim]")
    if client.base_url:
        console.print(f"[dim]base_url: {client.base_url}[/dim]")
    console.print(f"[dim]workspace: {ws}[/dim]")
    console.print(f"[dim]mode: {mode}[/dim]")
    if load_memory(ws):
        console.print(f"[dim]memory: MEMORY.md loaded into system prompt[/dim]")
    console.print(f"[dim]session auto-save: slot {auto_slot}[/dim]")
    if recorder is not None:
        console.print(f"[dim]recording: trajectories/{recorder.session_id}[/dim]")
    seeded = external_access.allowed_dirs()
    if seeded:
        console.print(f"[dim]external read paths: {', '.join(seeded)}[/dim]")
    console.print("[dim]Type a message, or /help for commands. "
                  "Tab completes `@<path>` and /commands.[/dim]\n")

    # Offer to resume the freshest recent session (only when stdin is a TTY).
    _, mode = _maybe_offer_resume(client, ws, mode)

    while True:
        try:
            user_input = read_input(prompt_session, console, "[bold green]you > [/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            _ask_save_on_exit(client, ws, mode)
            if recorder is not None:
                recorder.close(outcome=None)
            console.print("[dim]bye[/dim]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if cmd in ("/quit", "/exit", "/q"):
                _ask_save_on_exit(client, ws, mode)
                if recorder is not None:
                    recorder.close(outcome=None)
                console.print("[dim]bye[/dim]")
                break
            if cmd == "/tools":
                _print_tools()
                continue
            if cmd in ("/reset", "/clear"):
                client.reset()
                auto_slot = pick_auto_slot(ws)
                console.print(f"[dim]conversation reset (new auto-save slot: {auto_slot})[/dim]")
                continue
            if cmd == "/help":
                _print_help()
                continue
            if cmd == "/workspace":
                console.print(f"[dim]{ws}[/dim]")
                continue
            if cmd == "/sessions":
                _print_sessions(ws)
                continue
            if cmd == "/save":
                if not _has_user_turns(client.messages):
                    console.print("[dim]nothing to save (no conversation yet)[/dim]")
                    continue
                sp = manual_save(ws, arg, client.messages, model=client.model, mode=mode)
                console.print(f"[green]saved → {sp.relative_to(ws)}[/green]")
                continue
            if cmd == "/remember":
                if not arg:
                    console.print("[yellow]usage: /remember <fact>[/yellow] "
                                  "[dim](saved to MEMORY.md, loaded next session)[/dim]")
                    continue
                # Optional ``title :: fact`` syntax for explicit headers.
                if "::" in arg:
                    title, _, body = arg.partition("::")
                    title, body = title.strip(), body.strip()
                else:
                    title, body = "", arg
                res = append_memory(ws, fact=body, title=title or None, category="user")
                if res.get("ok"):
                    console.print(
                        f"[green]{res['action']}[/green] [dim]MEMORY.md → "
                        f"§{res['title']}[/dim]"
                    )
                    # Re-inject so the *current* session also sees the new fact.
                    client.system_prompt = _build_system_prompt(mode, ws)
                    if client.messages and client.messages[0].get("role") == "system":
                        client.messages[0]["content"] = client.system_prompt
                else:
                    console.print(f"[red]{res.get('error', 'failed')}[/red]")
                continue
            if cmd == "/memory":
                mem = load_memory(ws)
                if not mem:
                    console.print("[dim]MEMORY.md is empty or missing[/dim]")
                else:
                    console.print(Panel(
                        Markdown(mem), title="MEMORY.md", border_style="cyan"
                    ))
                continue
            if cmd in ("/good", "/bad"):
                if recorder is None:
                    console.print("[dim]not recording — run with --record or RM_RECORD=1[/dim]")
                    continue
                label = cmd[1:]
                recorder.on_user_label(label, target="last_response", note=arg or None)
                console.print(f"[dim]labelled last response: {label}[/dim]")
                continue
            if cmd == "/outcome":
                if recorder is None:
                    console.print("[dim]not recording — run with --record or RM_RECORD=1[/dim]")
                    continue
                if arg not in ("success", "partial", "fail"):
                    console.print("[yellow]usage: /outcome success|partial|fail[/yellow]")
                    continue
                recorder.on_user_label("outcome", target="session", note=arg)
                console.print(f"[dim]session outcome: {arg}[/dim]")
                continue
            if cmd == "/redo":
                last_user_idx = _find_last_user_idx(client.messages)
                if last_user_idx is None:
                    console.print("[dim]no prior user turn to redo[/dim]")
                    continue
                if recorder is not None:
                    rejected_msgs = client.messages[last_user_idx + 1:]
                    if rejected_msgs:
                        recorder.on_user_label(
                            "redo_reject",
                            target="last_response",
                            note=json.dumps({"n_messages": len(rejected_msgs)}),
                        )
                last_user = client.messages[last_user_idx].get("content", "")
                client.messages = client.messages[:last_user_idx]
                try:
                    _do_chat(client, last_user)
                    auto_save(ws, auto_slot, client.messages, model=client.model, mode=mode)
                except Exception as e:  # noqa: BLE001
                    console.print(f"[bold red]error:[/bold red] {e}")
                continue
            if cmd == "/branch":
                if not _has_user_turns(client.messages):
                    console.print("[dim]nothing to branch (no conversation yet)[/dim]")
                    continue
                branch_name = arg or f"branch_{datetime.datetime.now().strftime('%Y%m%dT%H%M%S')}"
                sp = manual_save(ws, branch_name, client.messages, model=client.model, mode=mode)
                console.print(
                    f"[green]branched → {sp.relative_to(ws)}[/green] "
                    "[dim](current session continues; reload the branch with /load)[/dim]"
                )
                if recorder is not None:
                    recorder.on_user_label("branch", target="session", note=branch_name)
                continue
            if cmd == "/load":
                if not arg:
                    console.print("[yellow]usage: /load <name> (e.g. 'auto-0' or a saved name)[/yellow]")
                    continue
                data = load_session(ws, arg)
                if data is None:
                    console.print(f"[red]session not found: {arg}[/red]")
                    continue
                client.messages = sanitize_history(data.get("messages", []))
                loaded_mode = data.get("mode", mode)
                if loaded_mode != mode:
                    client.system_prompt = _build_system_prompt(loaded_mode, ws)
                    mode = loaded_mode
                client.set_excluded_tools(excluded_tools_for(mode))
                auto_slot = pick_auto_slot(ws)
                turns = len([m for m in client.messages if m.get("role") == "user"])
                console.print(
                    f"[green]loaded session '{arg}'[/green] "
                    f"[dim]({turns} turns, mode={loaded_mode})[/dim]"
                )
                continue
            if cmd == "/allow":
                if not arg:
                    console.print("[yellow]usage: /allow <directory>[/yellow]")
                    continue
                try:
                    resolved = external_access.add_allowed_dir(arg)
                    console.print(f"[green]approved[/green] {resolved}")
                except (FileNotFoundError, NotADirectoryError) as e:
                    console.print(f"[red]{e}[/red]")
                continue
            if cmd == "/allowed":
                dirs = external_access.allowed_dirs()
                if not dirs:
                    console.print("[dim](no external directories approved)[/dim]")
                else:
                    for d in dirs:
                        console.print(f"[dim]  {d}[/dim]")
                continue
            if cmd == "/deny":
                if not arg:
                    console.print("[yellow]usage: /deny <directory>[/yellow]")
                    continue
                if external_access.remove_allowed_dir(arg):
                    console.print(f"[green]revoked[/green] {arg}")
                else:
                    console.print(f"[dim]not in whitelist: {arg}[/dim]")
                continue
            if cmd == "/package":
                _run_package_command(arg, ws)
                continue
            if cmd == "/env":
                _run_env_command(arg, ws)
                continue
            if cmd == "/mode":
                if arg not in ("base", "article", "blog", "book"):
                    console.print("[yellow]usage: /mode <base|article|blog|book>[/yellow]")
                    continue
                client.set_system_prompt(_build_system_prompt(arg, ws))
                client.set_excluded_tools(excluded_tools_for(arg))
                mode = arg
                excluded = sorted(client.excluded_tools)
                msg = f"[dim]switched to mode: {arg}"
                if excluded:
                    msg += f" (hidden tools: {', '.join(excluded)})"
                msg += "[/dim]"
                console.print(msg)
                continue
            console.print(f"[yellow]unknown command: {cmd}[/yellow]")
            continue

        try:
            _do_chat(client, user_input)
            auto_save(ws, auto_slot, client.messages, model=client.model, mode=mode)
        except KeyboardInterrupt:
            console.print("\n[yellow]interrupted[/yellow]")
        except Exception as e:  # noqa: BLE001
            console.print(f"[bold red]error:[/bold red] {e}")


def _run_oneshot(question: str, mode: str, output: str = "", record: bool = False) -> None:
    ws = set_workspace(Path.cwd())
    _maybe_generate_python_requirements(ws)
    client = _make_client(mode=mode, workspace=ws)
    recorder = _maybe_make_recorder(record, ws, client.model, mode)
    if recorder is not None:
        client.set_recorder(recorder)
    expanded, notes = expand_at_refs(question, ws)
    for n in notes:
        console.print(f"[dim]{n}[/dim]")
    try:
        response = client.chat(expanded, on_tool_call=_on_tool_call)
    finally:
        if recorder is not None:
            recorder.close(outcome=None)
    console.print()
    console.print(Markdown(response))
    if output:
        Path(output).write_text(response, encoding="utf-8")
        console.print(f"[dim]saved to {output}[/dim]")


def _batch_worker(args: tuple) -> dict:
    """Worker for batch mode (top-level for ProcessPoolExecutor pickling)."""
    idx, question, workspace, mode = args
    os.environ["RM_WORKSPACE"] = workspace
    # Re-import in worker context
    from research_manager.context import set_workspace as _set_ws
    from research_manager.llm.client import ResearchLLMClient as _Client
    from research_manager.llm.prompts import (
        BASE_SYSTEM_PROMPT as _BASE,
        excluded_tools_for as _excl,
        writing_prompt_for as _wp,
    )
    from research_manager.memory import inject_into_system_prompt as _inject
    from research_manager.memory import load_memory as _load_mem
    from research_manager.tools import ToolRegistry as _Reg  # noqa: F401
    _set_ws(workspace)
    prompt = _BASE if mode == "base" else _wp(mode)
    prompt = _inject(prompt, _load_mem(Path(workspace)))
    client = _Client(system_prompt=prompt)
    client.set_excluded_tools(_excl(mode))
    try:
        response = client.chat(question)
        return {"idx": idx, "ok": True, "response": response, "question": question}
    except Exception as e:
        return {"idx": idx, "ok": False, "error": str(e), "question": question}


def _run_batch(file_path: str, output_dir: str, workers: int, mode: str) -> None:
    text = Path(file_path).read_text(encoding="utf-8")
    questions = [q.strip() for q in text.split("\n---\n") if q.strip()]
    if not questions:
        console.print("[yellow]no questions found (use --- as a separator on its own line)[/yellow]")
        return

    if not output_dir:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = f"output/batch_{ts}"
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    workspace = str(Path.cwd().resolve())
    console.print(f"[dim]running {len(questions)} questions with {workers} worker(s) → {out}[/dim]")
    summary: list[dict] = []
    payloads = [(i, q, workspace, mode) for i, q in enumerate(questions, start=1)]
    if workers <= 1:
        results = [_batch_worker(p) for p in payloads]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_batch_worker, payloads))
    for r in sorted(results, key=lambda x: x["idx"]):
        q_id = f"q{r['idx']:03d}"
        path = out / f"{q_id}.md"
        if r["ok"]:
            path.write_text(f"# Question\n\n{r['question']}\n\n# Response\n\n{r['response']}\n", encoding="utf-8")
        else:
            path.write_text(f"# Question\n\n{r['question']}\n\n# Error\n\n{r['error']}\n", encoding="utf-8")
        summary.append({"id": q_id, "ok": r["ok"], "path": str(path)})
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]done[/green] — {sum(1 for s in summary if s['ok'])}/{len(summary)} succeeded")


def _run_clean(path: str) -> None:
    target = Path(path).resolve()
    if not target.exists():
        console.print(f"[red]error:[/red] path does not exist: {target}")
        sys.exit(1)

    entries = [e for e in target.iterdir() if e.name not in (".env", ".env.example")]
    if not entries:
        console.print("[dim]nothing to clean[/dim]")
        return

    dirs = sorted(e for e in entries if e.is_dir())
    files = sorted(e for e in entries if not e.is_dir())

    console.print(f"[bold yellow]will delete everything under:[/bold yellow] {target}\n")
    if dirs:
        console.print("[bold]directories:[/bold]")
        for d in dirs:
            console.print(f"  [red]{d.name}/[/red]")
    if files:
        console.print("[bold]files:[/bold]")
        for f in files:
            console.print(f"  [red]{f.name}[/red]")
    console.print(f"\n[dim](.env and .env.example will be preserved)[/dim]")

    try:
        answer = console.input("\n[bold yellow]confirm? (y/N): [/bold yellow]").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]cancelled[/dim]")
        return

    if answer not in ("y", "yes"):
        console.print("[dim]cancelled[/dim]")
        return

    import shutil
    for entry in entries:
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    console.print(f"[green]cleaned[/green] {len(dirs)} directories, {len(files)} files")


def _run_init(path: str, force: bool) -> None:
    target = Path(path).resolve()
    result = init_workspace(target, force=force)
    console.print(f"[green]initialized workspace[/green] at {result['workspace']}")
    if result["created"]:
        console.print(f"[dim]created: {', '.join(result['created'])}[/dim]")
    if result["existed"]:
        console.print(f"[dim]existed: {', '.join(result['existed'])}[/dim]")
    env_file = target / ".env"
    if not env_file.exists():
        console.print(
            "\n[yellow]hint:[/yellow] copy .env.example to .env and set your OPENAI_API_KEY "
            "before running the agent."
        )
    if result["created"]:
        _maybe_offer_r_setup_after_init()


def _run_validate(path: str) -> None:
    target = Path(path or ".").resolve()
    result = validate_workspace(target)
    if result["ok"]:
        console.print(f"[green]✓ workspace OK[/green]: {result['workspace']}")
    else:
        console.print(f"[red]✗ workspace incomplete[/red]: missing {result['missing']}")


def _maybe_offer_r_setup_after_init() -> None:
    if not sys.stdin.isatty():
        return
    try:
        choice = console.input(
            "[bold yellow]configure R in a conda environment now? (y/N): [/bold yellow]"
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]skipped R setup[/dim]")
        return
    if choice not in ("y", "yes"):
        console.print("[dim]skipped R setup; run `easy-research setup-r` later if needed[/dim]")
        return

    args = argparse.Namespace(
        env="",
        r_version=DEFAULT_R_VERSION,
        r_package=DEFAULT_R_PACKAGE,
        dry_run=False,
        yes=False,
        timeout=1800,
    )
    _run_setup_r(args)


def _run_setup_r(args) -> None:
    conda = find_conda()
    default_env = current_conda_env(conda)
    env_name = (args.env or default_env).strip()
    if not env_name:
        console.print("[red]empty conda environment name[/red]")
        return

    if not args.env and sys.stdin.isatty() and not args.yes:
        try:
            chosen = console.input(f"[yellow]conda env [{default_env}]: [/yellow]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]cancelled[/dim]")
            return
        if chosen:
            env_name = chosen

    r_version = (args.r_version or DEFAULT_R_VERSION).strip()
    r_package = (args.r_package or DEFAULT_R_PACKAGE).strip()

    status = inspect_r_setup(env_name, conda, r_package)
    if status.conda_exe is None:
        console.print("[red]conda not found on PATH. Install conda first or set CONDA_EXE.[/red]")
        return

    console.print(f"[dim]conda: {status.conda_exe}[/dim]")
    console.print(f"[dim]env: {env_name}[/dim]")
    if status.r_installed:
        console.print(f"[green]R detected[/green] [dim]version {status.r_version or 'unknown'}[/dim]")
    else:
        console.print("[yellow]R not detected in this env[/yellow]")
        if status.r_error:
            console.print(f"[dim]{status.r_error[-300:]}[/dim]")
    if status.tidyverse_installed:
        console.print(f"[green]{r_package} detected[/green]")
    else:
        console.print(f"[yellow]{r_package} not detected in this env[/yellow]")
    if status.repos_configured:
        console.print("[green]R package repositories configured[/green]")
    else:
        console.print("[yellow]R package repositories not configured in this env[/yellow]")

    use_active_rscript = env_name == os.environ.get("CONDA_DEFAULT_ENV", "").strip()
    commands = build_r_setup_commands(
        env_name,
        r_version=r_version,
        r_package=r_package,
        system_deps=status.missing_system_deps,
        install_r=not status.r_installed,
        configure_repos=not status.repos_configured,
        install_package=not status.tidyverse_installed,
        use_active_rscript=use_active_rscript,
    )
    if not commands:
        console.print("[green]R environment is already ready.[/green]")
        return

    console.print("\n[bold]planned commands:[/bold]")
    for command in commands:
        console.print(f"  {format_command(command, status.conda_exe)}")

    if args.dry_run:
        return

    execute = bool(args.yes)
    if not execute:
        if not sys.stdin.isatty():
            console.print("[dim]non-interactive session; rerun with --yes to execute or --dry-run to print only[/dim]")
            return
        try:
            choice = console.input("[bold yellow]execute these commands? (y/N): [/bold yellow]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]cancelled[/dim]")
            return
        execute = choice in ("y", "yes")
    if not execute:
        console.print("[dim]cancelled[/dim]")
        return

    ok = True
    current_task = None

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )

    def on_step_start(idx: int, command: list[str]) -> None:
        nonlocal current_task
        rendered = format_command(command, status.conda_exe)
        current_task = progress.add_task(f"step {idx + 1}/{len(commands)}", total=1)
        progress.console.print(f"[bold]running step {idx + 1}/{len(commands)}:[/bold] {rendered}")

    def on_output(idx: int, line: str) -> None:
        if line.strip():
            progress.console.print(f"[dim]{line}[/dim]")

    def on_step_finish(idx: int, step: dict[str, str | int | bool]) -> None:
        if current_task is not None:
            progress.update(current_task, completed=1)
        marker = "[green]✓[/green]" if step["ok"] else "[red]✗[/red]"
        progress.console.print(f"{marker} {step['command']}")

    with progress:
        results = execute_commands(
            commands,
            conda_exe=status.conda_exe,
            timeout=args.timeout,
            on_step_start=on_step_start,
            on_output=on_output,
            on_step_finish=on_step_finish,
        )

    for step in results:
        if not step["ok"]:
            ok = False
            stderr = str(step.get("stderr_tail") or "")
            stdout = str(step.get("stdout_tail") or "")
            if stderr:
                console.print(f"[dim red]{stderr}[/dim red]")
            elif stdout:
                console.print(f"[dim red]{stdout}[/dim red]")
            break
    if ok:
        final = inspect_r_setup(env_name, status.conda_exe, r_package)
        if final.r_installed and final.tidyverse_installed:
            console.print(f"[green]R setup complete[/green] [dim]env={env_name}[/dim]")
        else:
            console.print("[yellow]commands finished, but final verification did not pass[/yellow]")


def _run_configure_r_repos(args) -> None:
    conda = find_conda()
    if not conda:
        console.print("[red]conda not found on PATH. Install conda first or set CONDA_EXE.[/red]")
        return
    env_name = (args.env or current_conda_env(conda)).strip()
    if not env_name:
        console.print("[red]empty conda environment name[/red]")
        return
    try:
        use_active_rscript = env_name == os.environ.get("CONDA_DEFAULT_ENV", "").strip()
        profile = configure_r_repositories(conda, env_name, use_active_rscript=use_active_rscript)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        console.print(f"[red]failed to configure R package repositories:[/red] {e}")
        return
    console.print(f"[green]R package repositories configured[/green] [dim]{profile}[/dim]")


def _resolve_trajectory_id(ws: Path, session_id: str) -> Path | None:
    root = ws / ".research_manager_sessions" / "trajectories"
    if not root.exists():
        return None
    if session_id == "latest":
        dirs = [d for d in root.iterdir() if d.is_dir()]
        if not dirs:
            return None
        return max(dirs, key=lambda d: d.stat().st_mtime)
    candidate = root / session_id
    return candidate if candidate.is_dir() else None


def _run_sessions_subcommand(args) -> None:
    sub = getattr(args, "sessions_cmd", None) or "list"
    ws = Path(getattr(args, "path", ".")).resolve()
    if sub == "list":
        _print_sessions(ws)
        traj_root = ws / ".research_manager_sessions" / "trajectories"
        if traj_root.exists():
            traj = sorted(traj_root.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
            if traj:
                table = Table(title="Trajectories", show_header=True, header_style="bold cyan")
                table.add_column("Session ID", style="green")
                table.add_column("Started", style="dim")
                table.add_column("Steps", justify="right")
                table.add_column("Outcome")
                for d in traj:
                    meta_p = d / "meta.json"
                    if not meta_p.exists():
                        continue
                    try:
                        meta = json.loads(meta_p.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    table.add_row(
                        d.name,
                        (meta.get("started_at", "") or "")[:19],
                        str(meta.get("n_steps", "")),
                        str(meta.get("outcome") or "—"),
                    )
                console.print(table)
        return
    if sub == "trace":
        target = _resolve_trajectory_id(ws, args.session_id)
        if target is None:
            console.print(f"[red]trajectory not found: {args.session_id}[/red]")
            return
        _print_trajectory_trace(target)
        return
    if sub == "analyze":
        target = _resolve_trajectory_id(ws, args.session_id)
        if target is None:
            console.print(f"[red]trajectory not found: {args.session_id}[/red]")
            return
        from research_manager.recording.analysis import analyze_trajectory
        report = analyze_trajectory(target)
        console.print_json(data=report)
        return
    if sub == "prune":
        traj_root = ws / ".research_manager_sessions" / "trajectories"
        if not traj_root.exists():
            console.print("[dim]no trajectories to prune[/dim]")
            return
        dirs = sorted(
            (d for d in traj_root.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
        )
        excess = max(0, len(dirs) - args.keep)
        from research_manager.recording.recorder import _rmtree
        for d in dirs[:excess]:
            _rmtree(d)
        console.print(f"[green]pruned {excess} trajectory directories (kept {len(dirs) - excess})[/green]")
        return
    console.print("[yellow]usage: easy-research sessions [list|trace <id>|analyze <id>|prune][/yellow]")


def _print_trajectory_trace(traj_dir: Path) -> None:
    meta_p = traj_dir / "meta.json"
    events_p = traj_dir / "events.jsonl"
    if not meta_p.exists() or not events_p.exists():
        console.print(f"[red]incomplete trajectory at {traj_dir}[/red]")
        return
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        console.print(f"[red]meta unreadable: {e}[/red]")
        return

    console.print(Panel(
        f"session_id : {meta.get('session_id', '')}\n"
        f"model      : {meta.get('model', '')}\n"
        f"mode       : {meta.get('mode', '')}\n"
        f"started_at : {meta.get('started_at', '')}\n"
        f"ended_at   : {meta.get('ended_at') or '—'}\n"
        f"n_steps    : {meta.get('n_steps', '')}\n"
        f"outcome    : {meta.get('outcome') or '—'}\n"
        f"git_commit : {meta.get('git_commit') or '—'}",
        title="trajectory",
        border_style="cyan",
    ))

    counts: dict[str, int] = {}
    tool_calls: list[tuple[int, str, float]] = []
    user_labels: list[tuple[str, str]] = []
    with events_p.open(encoding="utf-8") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type", "")
            counts[t] = counts.get(t, 0) + 1
            if t == "tool_call_end":
                tool_calls.append((ev.get("step", -1), ev.get("name", ""), ev.get("duration_ms", 0.0)))
            elif t == "user_label":
                user_labels.append((ev.get("label", ""), ev.get("note") or ""))

    console.print(f"[dim]event counts: {counts}[/dim]")
    if tool_calls:
        table = Table(title="Tool calls", show_header=True, header_style="bold cyan")
        table.add_column("Step", justify="right")
        table.add_column("Tool", style="green")
        table.add_column("Duration (ms)", justify="right", style="dim")
        for step, name, ms in tool_calls:
            table.add_row(str(step), name, f"{ms:.1f}")
        console.print(table)
    if user_labels:
        console.print("[bold]user labels:[/bold]")
        for label, note in user_labels:
            console.print(f"  [magenta]{label}[/magenta] {note}")


def main() -> None:
    # Search for .env starting from the current working directory upward,
    # not from the cli.py module location (which is where dotenv defaults to
    # when invoked from an installed console script).
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path)
    external_access.init_from_env()

    parser = argparse.ArgumentParser(
        prog="easy-research",
        description="LLM agent for managing research projects.",
    )
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Create the standard workspace layout")
    p_init.add_argument("path", nargs="?", default=".", help="Workspace path (default: current dir)")
    p_init.add_argument("--force", action="store_true", help="Reset state file if present")

    # validate
    p_val = sub.add_parser("validate", help="Validate workspace structure")
    p_val.add_argument("path", nargs="?", default=".", help="Workspace path (default: current dir)")

    # clean
    p_clean = sub.add_parser("clean", help="Remove all files and directories in the workspace")
    p_clean.add_argument("path", nargs="?", default=".", help="Workspace path (default: current dir)")

    # setup-r
    p_setup_r = sub.add_parser("setup-r", help="Configure R and tidyverse in a conda environment")
    p_setup_r.add_argument("--env", default="", help="Conda environment name (default: current env)")
    p_setup_r.add_argument("--r-version", default=DEFAULT_R_VERSION, help=f"R version for conda install (default: {DEFAULT_R_VERSION})")
    p_setup_r.add_argument("--r-package", default=DEFAULT_R_PACKAGE, help=f"R package to install with install.packages (default: {DEFAULT_R_PACKAGE})")
    p_setup_r.add_argument("--dry-run", action="store_true", help="Print planned commands without executing")
    p_setup_r.add_argument("--yes", action="store_true", help="Execute without interactive confirmation")
    p_setup_r.add_argument("--timeout", type=int, default=1800, help="Timeout per install command in seconds")

    # Internal maintenance command used by setup-r planned commands.
    p_config_repos = sub.add_parser("configure-r-repos", help=argparse.SUPPRESS)
    p_config_repos.add_argument("-n", "--env", default="", help=argparse.SUPPRESS)

    # batch
    p_batch = sub.add_parser("batch", help="Run a batch of questions from a file")
    p_batch.add_argument("file", help="File with questions separated by lines containing only ---")
    p_batch.add_argument("-o", "--output", default="", help="Output directory")
    p_batch.add_argument("-w", "--workers", type=int, default=1, help="Parallel workers")
    p_batch.add_argument("-m", "--mode", default="base", choices=["base", "article", "blog", "book"])

    # sessions: list / trace / prune trajectory recordings
    p_sess = sub.add_parser("sessions", help="Inspect saved sessions and trajectory recordings")
    sess_sub = p_sess.add_subparsers(dest="sessions_cmd")
    p_sess_list = sess_sub.add_parser("list", help="List sessions and recorded trajectories")
    p_sess_list.add_argument("path", nargs="?", default=".", help="Workspace path (default: current dir)")
    p_sess_trace = sess_sub.add_parser("trace", help="Print a trajectory summary")
    p_sess_trace.add_argument("session_id", help="Trajectory session id (or 'latest')")
    p_sess_trace.add_argument("path", nargs="?", default=".", help="Workspace path")
    p_sess_prune = sess_sub.add_parser("prune", help="Prune old trajectory recordings")
    p_sess_prune.add_argument("--keep", type=int, default=50, help="How many recent trajectories to keep")
    p_sess_prune.add_argument("path", nargs="?", default=".", help="Workspace path")
    p_sess_analyze = sess_sub.add_parser(
        "analyze", help="Run Tier 2 derived-signal analysis over a trajectory"
    )
    p_sess_analyze.add_argument("session_id", help="Trajectory session id (or 'latest')")
    p_sess_analyze.add_argument("path", nargs="?", default=".", help="Workspace path")

    # default args (chat / oneshot)
    parser.add_argument("question", nargs="?", default=None, help="A single question; omit for REPL.")
    parser.add_argument("-f", "--file", default=None, help="Read question from a file.")
    parser.add_argument("-o", "--output", default="", help="Save response to this path.")
    parser.add_argument("-m", "--mode", default="base", choices=["base", "article", "blog", "book"],
                        help="Writing mode (default: base).")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-save script proposals without prompting (for batch/CI sessions).")
    parser.add_argument("--record", action="store_true",
                        help="Record a trajectory under .research_manager_sessions/trajectories/.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args()

    global _AUTO_APPROVE_PROPOSALS
    _AUTO_APPROVE_PROPOSALS = bool(getattr(args, "auto_approve", False))

    if args.command == "init":
        _run_init(args.path, force=args.force)
        return
    if args.command == "validate":
        _run_validate(args.path)
        return
    if args.command == "clean":
        _run_clean(args.path)
        return
    if args.command == "setup-r":
        _run_setup_r(args)
        return
    if args.command == "configure-r-repos":
        _run_configure_r_repos(args)
        return
    if args.command == "batch":
        _run_batch(args.file, output_dir=args.output, workers=args.workers, mode=args.mode)
        return
    if args.command == "sessions":
        _run_sessions_subcommand(args)
        return

    if args.file:
        question = Path(args.file).read_text(encoding="utf-8").strip()
        if question:
            _run_oneshot(question, mode=args.mode, output=args.output, record=args.record)
        return
    if args.question:
        _run_oneshot(args.question, mode=args.mode, output=args.output, record=args.record)
        return
    if not sys.stdin.isatty():
        question = sys.stdin.read().strip()
        if question:
            _run_oneshot(question, mode=args.mode, output=args.output, record=args.record)
        return
    _run_interactive(mode=args.mode, record=args.record)


if __name__ == "__main__":
    main()
