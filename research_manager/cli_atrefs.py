"""@-reference expansion for REPL input.

Recognises `@<path>` tokens in user messages and expands them inline so the
LLM gets the content (or a pointer to it) without an extra tool round-trip.

Strategy:
- Small text files (<= INLINE_BYTES_LIMIT) get their full contents inlined.
- Large or non-text files become a one-line hint suggesting the LLM call
  `read_text_file` / `read_external_file`.
- Directories become a shallow listing (capped at LIST_ENTRIES_LIMIT) plus a
  hint to call `list_workspace` for full traversal.
- External paths must be in the `external_access` whitelist; otherwise a
  refusal with a `/allow <dir>` hint is emitted (no content shown).

The original `@<path>` token is left untouched in the user message so the
model can still see what the user typed; the resolved content is appended
as a "[Attached:" block at the end.
"""

from __future__ import annotations

import re
from pathlib import Path

from research_manager.tools import external_access


INLINE_BYTES_LIMIT = 50 * 1024
LIST_ENTRIES_LIMIT = 30

_TEXT_EXT = {
    ".md", ".txt", ".rst", ".tex", ".csv", ".tsv", ".log", ".json",
    ".yaml", ".yml", ".py", ".r", ".R", ".sh", ".html", ".xml",
    ".toml", ".cfg", ".ini", ".conf", ".bib", ".lock",
    ".ipynb",  # ipynb is JSON; treat as text
}

# A token is `@` followed by 1+ non-whitespace chars. The token must not
# contain another `@` (avoids email-like `name@host`). To reduce false
# positives further we require the token to actually resolve to an existing
# path; non-existent or whitelist-rejected tokens are left as plain text.
_AT_TOKEN = re.compile(r"@(?P<path>[^\s@]+)")


def _classify(target: Path) -> str:
    if target.is_dir():
        return "dir"
    if target.is_file():
        return "file"
    return "missing"


def _is_text_path(target: Path) -> bool:
    return target.suffix.lower() in _TEXT_EXT or target.suffix == ""


def _resolve(token: str, workspace: Path) -> Path:
    """Resolve `@token`'s path argument relative to workspace and ~."""
    p = Path(token).expanduser()
    if not p.is_absolute():
        p = (workspace / p)
    try:
        return p.resolve()
    except OSError:
        return p


def _read_inline(target: Path) -> tuple[str, bool]:
    """Read up to INLINE_BYTES_LIMIT chars; return (text, truncated)."""
    try:
        size = target.stat().st_size
    except OSError as e:
        return f"[error reading: {e}]", False
    truncated = size > INLINE_BYTES_LIMIT
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            text = f.read(INLINE_BYTES_LIMIT)
    except OSError as e:
        return f"[error reading: {e}]", False
    return text, truncated


def _shallow_listing(target: Path, workspace: Path) -> list[str]:
    """Return up to LIST_ENTRIES_LIMIT entries from the directory (depth 2)."""
    out: list[str] = []
    for child in sorted(target.iterdir()):
        rel = _display_path(child, workspace)
        if child.is_dir():
            out.append(f"{rel}/")
            try:
                grandchildren = sorted(child.iterdir())
            except OSError:
                grandchildren = []
            for gc in grandchildren[:5]:
                out.append(f"  {_display_path(gc, workspace)}{'/' if gc.is_dir() else ''}")
        else:
            out.append(rel)
        if len(out) >= LIST_ENTRIES_LIMIT:
            out.append(f"... ({LIST_ENTRIES_LIMIT}+ entries; use list_workspace for full traversal)")
            break
    return out


def _display_path(p: Path, workspace: Path) -> str:
    try:
        return str(p.relative_to(workspace))
    except ValueError:
        return str(p)


def _is_external(target: Path, workspace: Path) -> bool:
    try:
        target.relative_to(workspace)
        return False
    except ValueError:
        return True


def expand_at_refs(text: str, workspace: Path) -> tuple[str, list[str]]:
    """Return (augmented_text, notes). `text` is unchanged; attachments are
    appended after a separator. `notes` are short status strings suitable
    for the REPL to print (e.g. `[attached] script/foo.py (1.2 KB)`).
    """
    seen: dict[str, Path] = {}
    matches = list(_AT_TOKEN.finditer(text))
    for m in matches:
        raw = m.group("path")
        # strip trailing punctuation that's almost never part of a path
        token = raw.rstrip(".,;:!?)")
        if not token:
            continue
        target = _resolve(token, workspace)
        # only treat as a real reference if the path exists OR maps to a
        # whitelisted external root that exists
        if _classify(target) == "missing":
            continue
        seen.setdefault(token, target)

    if not seen:
        return text, []

    blocks: list[str] = []
    notes: list[str] = []
    for token, target in seen.items():
        block, note = _render_one(token, target, workspace)
        if block:
            blocks.append(block)
        if note:
            notes.append(note)

    if not blocks:
        return text, notes

    augmented = text + "\n\n---\n[Attached references — resolved from `@` tokens]\n\n" + "\n\n".join(blocks)
    return augmented, notes


def _render_one(token: str, target: Path, workspace: Path) -> tuple[str, str]:
    kind = _classify(target)
    rel = _display_path(target, workspace)
    external = _is_external(target, workspace)

    if external and not external_access.is_allowed(target):
        block = (
            f"### `@{token}` → {target}\n"
            f"**External path not in whitelist.** Ask the user to run "
            f"`/allow <directory>` (or set `RM_EXTERNAL_READ_PATHS`), "
            f"then retry. Do not guess the contents."
        )
        return block, f"[external rejected] @{token} (run /allow first)"

    if kind == "dir":
        entries = _shallow_listing(target, workspace)
        listing = "\n".join(entries) if entries else "(empty)"
        tool_hint = (
            "list_workspace" if not external else "list_external_allowed / read_external_file"
        )
        block = (
            f"### `@{token}` → directory `{rel}`\n"
            f"```\n{listing}\n```\n"
            f"_Use `{tool_hint}` to enumerate further or read specific files._"
        )
        return block, f"[attached dir] @{token} ({len(entries)} entries shown)"

    # kind == "file"
    try:
        size = target.stat().st_size
    except OSError as e:
        return f"### `@{token}` → file `{rel}`\n`error: {e}`", f"[error] @{token}: {e}"

    if not _is_text_path(target) or size > INLINE_BYTES_LIMIT:
        tool = "read_external_file" if external else "read_text_file"
        reason = "non-text extension" if not _is_text_path(target) else f"file too large ({_human(size)})"
        block = (
            f"### `@{token}` → file `{rel}`\n"
            f"_{reason} — call `{tool}('{rel if not external else target}', max_chars=...)` to fetch contents._"
        )
        return block, f"[hint] @{token} ({reason})"

    text, truncated = _read_inline(target)
    suffix = target.suffix.lstrip(".") or "text"
    note_size = _human(size) + (" — truncated" if truncated else "")
    block = (
        f"### `@{token}` → file `{rel}` ({note_size})\n"
        f"```{suffix}\n{text}\n```"
    )
    return block, f"[attached] @{token} ({note_size})"


def _human(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"
