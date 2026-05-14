"""CLI entry point for the research manager agent."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from research_manager import __version__
from research_manager.context import set_workspace
from research_manager.llm.client import ResearchLLMClient
from research_manager.llm.prompts import BASE_SYSTEM_PROMPT, writing_prompt_for
from research_manager.tools import ToolRegistry  # noqa: F401  (triggers tool registration)
from research_manager.workspace.manager import init_workspace, validate_workspace

console = Console()


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
        "  /reset          - clear conversation\n"
        "  /help           - show this help\n"
        "  /quit           - exit[/dim]"
    )


def _on_tool_call(name: str, args: dict, result: str) -> None:
    args_preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
    if len(args) > 3:
        args_preview += ", ..."
    console.print(f"[dim cyan]→ tool[/dim cyan] [bold]{name}[/bold]([dim]{args_preview}[/dim])")


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


def _run_interactive(mode: str) -> None:
    _print_banner()
    client = _make_client(mode=mode)
    ws = set_workspace(Path.cwd())

    console.print(f"[dim]model: {client.model}[/dim]")
    if client.base_url:
        console.print(f"[dim]base_url: {client.base_url}[/dim]")
    console.print(f"[dim]workspace: {ws}[/dim]")
    console.print(f"[dim]mode: {mode}[/dim]")
    console.print("[dim]Type a message, or /help for commands.[/dim]\n")

    while True:
        try:
            user_input = console.input("[bold green]you > [/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if cmd in ("/quit", "/exit", "/q"):
                console.print("[dim]bye[/dim]")
                break
            if cmd == "/tools":
                _print_tools()
                continue
            if cmd in ("/reset", "/clear"):
                client.reset()
                console.print("[dim]conversation reset[/dim]")
                continue
            if cmd == "/help":
                _print_help()
                continue
            if cmd == "/workspace":
                console.print(f"[dim]{ws}[/dim]")
                continue
            if cmd == "/mode":
                if arg not in ("base", "article", "blog", "book"):
                    console.print("[yellow]usage: /mode <base|article|blog|book>[/yellow]")
                    continue
                new_prompt = BASE_SYSTEM_PROMPT if arg == "base" else writing_prompt_for(arg)
                client.set_system_prompt(new_prompt)
                console.print(f"[dim]switched to mode: {arg}[/dim]")
                continue
            console.print(f"[yellow]unknown command: {cmd}[/yellow]")
            continue

        try:
            _do_chat(client, user_input)
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


def _run_init(path: str, force: bool) -> None:
    target = Path(path).resolve()
    result = init_workspace(target, force=force)
    console.print(f"[green]initialized workspace[/green] at {result['workspace']}")
    if result["created"]:
        console.print(f"[dim]created: {', '.join(result['created'])}[/dim]")
    if result["existed"]:
        console.print(f"[dim]existed: {', '.join(result['existed'])}[/dim]")


def _run_validate(path: str) -> None:
    target = Path(path or ".").resolve()
    result = validate_workspace(target)
    if result["ok"]:
        console.print(f"[green]✓ workspace OK[/green]: {result['workspace']}")
    else:
        console.print(f"[red]✗ workspace incomplete[/red]: missing {result['missing']}")


def main() -> None:
    load_dotenv()

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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args()

    if args.command == "init":
        _run_init(args.path, force=args.force)
        return
    if args.command == "validate":
        _run_validate(args.path)
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
