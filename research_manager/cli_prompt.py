"""Interactive prompt for the REPL with `@<path>` completion.

Uses prompt_toolkit so that:

- Backspace correctly deletes one *character* (not one byte) on CJK input.
  Python's built-in ``input()`` runs in cooked-mode tty; depending on
  ``IUTF8`` the kernel either erases too few columns or chops a UTF-8
  sequence in half. prompt_toolkit owns the line buffer in raw mode and
  tracks character widths itself.
- Tab inside an ``@<partial>`` token completes against workspace paths
  (and externally whitelisted roots). Outside an ``@`` token, Tab is a
  no-op (we don't want generic command completion).
- Slash commands (``/help``, ``/mode``, …) complete at the start of a line.
- Up/Down browses an in-memory history of past inputs.

Falls back to ``console.input`` if prompt_toolkit is unavailable for some
reason; the fallback keeps the existing behaviour (including the CJK
backspace glitch — but that's the bug we're trying to fix, so the
fallback should be a rare path).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import InMemoryHistory
    _HAS_PT = True
except ImportError:  # pragma: no cover
    _HAS_PT = False


SLASH_COMMANDS = [
    "/help", "/tools", "/mode", "/workspace", "/allow", "/allowed", "/deny",
    "/package", "/env", "/sessions", "/save", "/load", "/branch",
    "/good", "/bad", "/outcome", "/redo", "/reset", "/clear", "/quit",
    "/exit",
]


if _HAS_PT:

    class _AtPathCompleter(Completer):
        """Completes `@<partial>` tokens against the filesystem.

        Resolution rules mirror :mod:`research_manager.cli_atrefs`:
        - relative paths resolve against the workspace,
        - ``~`` is expanded,
        - absolute paths resolve as-is.

        We intentionally don't filter against the external-access whitelist
        here — completing the path is fine; the actual *read* later goes
        through ``cli_atrefs`` which still enforces the whitelist.
        """

        def __init__(self, workspace_getter, extra_roots_getter=None):
            self._workspace_getter = workspace_getter
            self._extra_roots_getter = extra_roots_getter or (lambda: [])

        def get_completions(self, document, complete_event) -> Iterable["Completion"]:
            text = document.text_before_cursor

            # Slash command completion: only when the line starts with `/`
            # and we're still on the first token.
            if text.startswith("/") and " " not in text:
                for cmd in SLASH_COMMANDS:
                    if cmd.startswith(text):
                        yield Completion(cmd, start_position=-len(text))
                return

            # Find the closest `@` to the left of the cursor that is either
            # at the start or preceded by whitespace. Anything else (e.g.
            # `email@host`) is not an attach token.
            at_pos = -1
            for i in range(len(text) - 1, -1, -1):
                ch = text[i]
                if ch.isspace():
                    break
                if ch == "@" and (i == 0 or text[i - 1].isspace()):
                    at_pos = i
                    break
            if at_pos < 0:
                return

            partial = text[at_pos + 1:]
            ws = self._workspace_getter()

            # Decide what directory to list and what prefix to filter by.
            base_dir, prefix, anchor = self._split_partial(partial, ws)
            if base_dir is None:
                return

            try:
                entries = sorted(base_dir.iterdir())
            except OSError:
                return

            for entry in entries:
                name = entry.name
                if not name.startswith(prefix):
                    continue
                # hide dotfiles unless the user typed a leading dot
                if name.startswith(".") and not prefix.startswith("."):
                    continue
                display = name + ("/" if entry.is_dir() else "")
                # The completion replaces just the trailing path component
                # the user is typing — start_position counts back from the
                # cursor. We insert `display` over `prefix`.
                completed = anchor + display
                yield Completion(
                    completed,
                    start_position=-(len(partial)),
                    display=display,
                )

        @staticmethod
        def _split_partial(partial: str, workspace: Path):
            """Return (directory_to_list, name_prefix, anchor_to_keep).

            ``anchor`` is the part of ``partial`` we keep on the left of the
            replacement (e.g. ``script/`` when the user is filling in
            ``script/foo``).
            """
            if partial.startswith("~"):
                expanded = Path(partial).expanduser()
                # If the user already past `~/` we treat it as absolute.
                if "/" in partial:
                    head, _, tail = partial.rpartition("/")
                    base = Path(head).expanduser().resolve() if head else Path("~").expanduser()
                    return base if base.is_dir() else None, tail, head + "/"
                # Just `~<partial>` — list home itself.
                return Path("~").expanduser(), partial.lstrip("~"), "~"

            if partial.startswith("/"):
                head, _, tail = partial.rpartition("/")
                base = Path(head if head else "/").resolve()
                return base if base.is_dir() else None, tail, (head + "/") if head else "/"

            # workspace-relative
            if "/" in partial:
                head, _, tail = partial.rpartition("/")
                base = (workspace / head).resolve()
                return base if base.is_dir() else None, tail, head + "/"
            return workspace, partial, ""


def _ansi_prompt(rich_console, markup: str) -> "ANSI | str":
    """Render a rich-markup prompt string into prompt_toolkit ANSI."""
    if not _HAS_PT:
        return markup
    # Force color codes regardless of stdout TTY state — prompt_toolkit owns
    # the terminal and will emit them itself.
    from rich.console import Console as _RC
    capture_console = _RC(
        force_terminal=True,
        color_system=rich_console.color_system or "truecolor",
        file=rich_console.file,
        width=rich_console.width,
        legacy_windows=False,
    )
    with capture_console.capture() as cap:
        capture_console.print(markup, end="", markup=True, highlight=False)
    return ANSI(cap.get())


def make_session(rich_console, workspace_getter):
    """Create a PromptSession with our completer, or None if unavailable.

    The session is reused for the lifetime of the REPL so command history
    persists across turns. History is in-memory only (we don't want to leak
    research prompts across sessions silently); persistent history can be
    added later as opt-in.
    """
    if not _HAS_PT:
        return None
    completer = _AtPathCompleter(workspace_getter=workspace_getter)
    return PromptSession(
        completer=completer,
        complete_while_typing=False,  # only on Tab — keeps typing snappy
        history=InMemoryHistory(),
        enable_history_search=True,   # Ctrl-R reverse search
        mouse_support=False,
    )


def read_input(session, rich_console, prompt_markup: str) -> str:
    """Read one line of input. Falls back to ``console.input``."""
    if session is None or not _HAS_PT or not os.isatty(0):
        return rich_console.input(prompt_markup)
    return session.prompt(_ansi_prompt(rich_console, prompt_markup))
