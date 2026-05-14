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
from rich.syntax import Syntax
from rich.table import Table

from research_manager import __version__
from research_manager.context import get_workspace, set_workspace
from research_manager.llm.client import ResearchLLMClient
from research_manager.llm.prompts import BASE_SYSTEM_PROMPT, writing_prompt_for
from research_manager.sessions import auto_save, list_sessions, load_session, manual_save, pick_auto_slot
from research_manager.tools import ToolRegistry  # noqa: F401  (triggers tool registration)
from research_manager.tools import external_access
from research_manager.workspace.manager import init_workspace, validate_workspace

console = Console()

_AUTO_APPROVE_PROPOSALS = False


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


def _print_help() -> None:
    console.print(
        "[dim]Commands:\n"
        "  /tools          - list registered tools\n"
        "  /mode <kind>    - switch writing mode (base, article, blog, book)\n"
        "  /workspace      - show current workspace path\n"
        "  /allow <dir>    - approve a directory for external file reads\n"
        "  /allowed        - show approved external directories\n"
        "  /deny <dir>     - revoke a previously approved directory\n"
        "  /sessions       - list saved and auto-saved sessions\n"
        "  /save [name]    - save current conversation (permanent)\n"
        "  /load <name>    - load a session (e.g. 'auto-0' or a saved name)\n"
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


def _print_proposal_preview(args: dict, parsed: dict) -> None:
    lang = parsed.get("language", "text")
    syntax_lang = {"python": "python", "r": "r", "shell": "bash"}.get(lang, "text")
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
                title=f"proposal {parsed.get('proposal_id', '')} → script/{parsed.get('target_filename', '')}",
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
            f"[bold yellow]save proposal {proposal_id} → script/{target}? "
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
                f"[yellow]script/{target_name} already exists — (o)verwrite / (r)ename / (c)ancel? [/yellow]"
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


def _make_client(mode: str = "base") -> ResearchLLMClient:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        console.print("[bold red]error:[/bold red] OPENAI_API_KEY is not set.")
        console.print("Create a .env file or export the variable. See .env.example.")
        sys.exit(1)

    system_prompt = BASE_SYSTEM_PROMPT if mode == "base" else writing_prompt_for(mode)
    return ResearchLLMClient(
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        model=os.getenv("RM_MODEL") or None,
        system_prompt=system_prompt,
    )


def _do_chat(client: ResearchLLMClient, user_input: str) -> str:
    response = client.chat(user_input, on_tool_call=_on_tool_call)
    console.print()
    console.print(Markdown(response))
    return response


def _has_user_turns(messages: list[dict]) -> bool:
    return any(m.get("role") == "user" for m in messages)


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


def _run_interactive(mode: str) -> None:
    _print_banner()
    client = _make_client(mode=mode)
    ws = set_workspace(Path.cwd())

    auto_slot = pick_auto_slot(ws)

    console.print(f"[dim]model: {client.model}[/dim]")
    if client.base_url:
        console.print(f"[dim]base_url: {client.base_url}[/dim]")
    console.print(f"[dim]workspace: {ws}[/dim]")
    console.print(f"[dim]mode: {mode}[/dim]")
    console.print(f"[dim]session auto-save: slot {auto_slot}[/dim]")
    seeded = external_access.allowed_dirs()
    if seeded:
        console.print(f"[dim]external read paths: {', '.join(seeded)}[/dim]")
    console.print("[dim]Type a message, or /help for commands.[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold green]you > [/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            _ask_save_on_exit(client, ws, mode)
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
            if cmd == "/load":
                if not arg:
                    console.print("[yellow]usage: /load <name> (e.g. 'auto-0' or a saved name)[/yellow]")
                    continue
                data = load_session(ws, arg)
                if data is None:
                    console.print(f"[red]session not found: {arg}[/red]")
                    continue
                client.messages = data.get("messages", [])
                loaded_mode = data.get("mode", mode)
                if loaded_mode != mode:
                    new_prompt = BASE_SYSTEM_PROMPT if loaded_mode == "base" else writing_prompt_for(loaded_mode)
                    client.system_prompt = new_prompt
                    mode = loaded_mode
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
            if cmd == "/mode":
                if arg not in ("base", "article", "blog", "book"):
                    console.print("[yellow]usage: /mode <base|article|blog|book>[/yellow]")
                    continue
                new_prompt = BASE_SYSTEM_PROMPT if arg == "base" else writing_prompt_for(arg)
                client.set_system_prompt(new_prompt)
                mode = arg
                console.print(f"[dim]switched to mode: {arg}[/dim]")
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


def _run_oneshot(question: str, mode: str, output: str = "") -> None:
    set_workspace(Path.cwd())
    client = _make_client(mode=mode)
    response = client.chat(question, on_tool_call=_on_tool_call)
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
    from research_manager.llm.prompts import BASE_SYSTEM_PROMPT as _BASE, writing_prompt_for as _wp
    from research_manager.tools import ToolRegistry as _Reg  # noqa: F401
    _set_ws(workspace)
    prompt = _BASE if mode == "base" else _wp(mode)
    client = _Client(system_prompt=prompt)
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


def _run_validate(path: str) -> None:
    target = Path(path or ".").resolve()
    result = validate_workspace(target)
    if result["ok"]:
        console.print(f"[green]✓ workspace OK[/green]: {result['workspace']}")
    else:
        console.print(f"[red]✗ workspace incomplete[/red]: missing {result['missing']}")


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

    # batch
    p_batch = sub.add_parser("batch", help="Run a batch of questions from a file")
    p_batch.add_argument("file", help="File with questions separated by lines containing only ---")
    p_batch.add_argument("-o", "--output", default="", help="Output directory")
    p_batch.add_argument("-w", "--workers", type=int, default=1, help="Parallel workers")
    p_batch.add_argument("-m", "--mode", default="base", choices=["base", "article", "blog", "book"])

    # default args (chat / oneshot)
    parser.add_argument("question", nargs="?", default=None, help="A single question; omit for REPL.")
    parser.add_argument("-f", "--file", default=None, help="Read question from a file.")
    parser.add_argument("-o", "--output", default="", help="Save response to this path.")
    parser.add_argument("-m", "--mode", default="base", choices=["base", "article", "blog", "book"],
                        help="Writing mode (default: base).")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Auto-save script proposals without prompting (for batch/CI sessions).")
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
    if args.command == "batch":
        _run_batch(args.file, output_dir=args.output, workers=args.workers, mode=args.mode)
        return

    if args.file:
        question = Path(args.file).read_text(encoding="utf-8").strip()
        if question:
            _run_oneshot(question, mode=args.mode, output=args.output)
        return
    if args.question:
        _run_oneshot(args.question, mode=args.mode, output=args.output)
        return
    if not sys.stdin.isatty():
        question = sys.stdin.read().strip()
        if question:
            _run_oneshot(question, mode=args.mode, output=args.output)
        return
    _run_interactive(mode=args.mode)


if __name__ == "__main__":
    main()
