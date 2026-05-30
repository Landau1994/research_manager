"""Project-level memory: ``MEMORY.md`` reader + writer + auto-resume picker.

Two complementary mechanisms:

1. **MEMORY.md** — a Markdown file in the workspace root. If present, its
   contents are appended to the system prompt at REPL startup (and on
   ``/mode`` switches), so the model carries durable project facts across
   sessions. The file is plain text the user can edit by hand; the
   ``/remember`` REPL command (and the ``remember_fact`` LLM-callable tool)
   write to it through ``append_memory()`` with a stable header format.

2. **Auto-resume** — at REPL start we look at the freshest auto-save slot
   (``.research_manager_sessions/auto/slot_*.json``). If it has user
   turns and was written recently we prompt the user to resume. This
   is intentionally explicit: silently resuming an old conversation is
   surprising; never offering is annoying.

Both pieces are pure helpers — the CLI wires them in. Keeping the logic
out of ``cli.py`` keeps it testable.
"""

from __future__ import annotations

import datetime
import re
import time
from pathlib import Path

MEMORY_FILENAME = "MEMORY.md"

# How recent (seconds) must an auto-save be before we offer to resume it.
# 7 days is the same window Claude Code uses for cron auto-expiry; reusing
# the constant means a user who walks away for >1 week starts fresh by
# default, but a returning daily user always sees the prompt.
AUTO_RESUME_MAX_AGE_S = 7 * 24 * 3600

_MEMORY_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


# ---------- MEMORY.md read / inject ----------

def memory_path(workspace: Path) -> Path:
    return workspace / MEMORY_FILENAME


def load_memory(workspace: Path) -> str:
    """Return the contents of ``<workspace>/MEMORY.md`` or empty string."""
    p = memory_path(workspace)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def inject_into_system_prompt(base_prompt: str, memory_text: str) -> str:
    """Append memory to a system prompt under a stable header.

    Idempotent: if ``memory_text`` is empty the prompt is returned
    unchanged; if it already ends in a memory block we replace it
    rather than stack a new one.
    """
    marker = "\n\n## Project Memory (from MEMORY.md)\n\n"
    if marker in base_prompt:
        base_prompt = base_prompt.split(marker, 1)[0]
    if not memory_text:
        return base_prompt
    return (
        base_prompt
        + marker
        + "These are durable facts the user has saved about this project. "
        + "Treat them as authoritative context. They were written across previous sessions; "
        + "if a fact contradicts the current conversation, ask before overriding.\n\n"
        + memory_text.strip()
        + "\n"
    )


# ---------- MEMORY.md append ----------

def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9一-鿿]+", "-", s)
    return s.strip("-")[:60] or "note"


def append_memory(
    workspace: Path,
    fact: str,
    title: str | None = None,
    category: str = "project",
) -> dict:
    """Append a fact to ``MEMORY.md``, returning a small status dict.

    Format (stable across sessions so re-loads are deterministic):

        ## <title> <!-- id: <slug> · category: <cat> · added: <iso-date> -->

        <fact body>

    If a section with the same title already exists, ``fact`` is *appended
    inside* that section rather than duplicating the header. This keeps
    the file from growing one new heading per minor edit.
    """
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    p = memory_path(workspace)

    fact = fact.strip()
    if not fact:
        return {"ok": False, "error": "empty fact"}

    title = (title or _derive_title(fact)).strip()
    slug = _slugify(title)
    iso_date = datetime.date.today().isoformat()

    existing = p.read_text(encoding="utf-8") if p.exists() else ""

    # If the file is fresh, drop a tiny preamble explaining what it's for.
    if not existing.strip():
        existing = (
            "# Project Memory\n\n"
            "Durable facts about this project. The agent loads this file at\n"
            "startup and treats it as authoritative context. Edit by hand or\n"
            "use `/remember <fact>` in the REPL.\n"
        )

    # Try to find an existing section with the same title.
    section_match = _find_section_bounds(existing, title)
    if section_match is not None:
        start, end = section_match
        section = existing[start:end]
        # Append the new fact under the existing header.
        appended = section.rstrip() + f"\n\n- {iso_date}: {fact}\n\n"
        new_text = existing[:start] + appended + existing[end:]
        action = "appended"
    else:
        header = (
            f"\n## {title} <!-- id: {slug} · category: {category} · added: {iso_date} -->\n\n"
        )
        new_text = existing.rstrip() + "\n" + header + fact + "\n"
        action = "added"

    p.write_text(new_text, encoding="utf-8")
    return {
        "ok": True,
        "path": str(p),
        "action": action,
        "title": title,
        "slug": slug,
        "size_bytes": len(new_text.encode("utf-8")),
    }


def _derive_title(fact: str) -> str:
    """Pull a 1-line title from the start of ``fact``."""
    first_line = fact.splitlines()[0].strip()
    if len(first_line) <= 60:
        return first_line
    # Trim on a word boundary if possible.
    cut = first_line[:60].rsplit(" ", 1)[0]
    return cut or first_line[:60]


def _find_section_bounds(text: str, title: str) -> tuple[int, int] | None:
    """Return (start, end) byte offsets of a ``## <title>`` section.

    Section ends at the next ``##`` heading (or EOF).
    """
    title_norm = title.strip().lower()
    sections = list(_MEMORY_HEADER_RE.finditer(text))
    for i, m in enumerate(sections):
        # Strip trailing HTML comment tail before comparing.
        head = m.group(1).split("<!--", 1)[0].strip().lower()
        if head != title_norm:
            continue
        start = m.start()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        return (start, end)
    return None


# ---------- auto-resume picker ----------

def freshest_resumable_slot(workspace: Path) -> dict | None:
    """Return metadata for the freshest auto-save slot worth resuming.

    "Worth resuming" means: it exists, it has at least one user turn, and
    it was updated within ``AUTO_RESUME_MAX_AGE_S``. Returns None when no
    slot qualifies — the REPL then starts fresh without prompting.
    """
    from research_manager.sessions import _read_session_file, _slot_path
    from research_manager.sessions import _NUM_SLOTS

    candidates: list[dict] = []
    now = time.time()
    for i in range(_NUM_SLOTS):
        sp = _slot_path(workspace, i)
        if not sp.exists():
            continue
        data = _read_session_file(sp)
        if not data:
            continue
        msgs = data.get("messages", [])
        n_user = sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "user")
        if n_user == 0:
            continue
        try:
            updated_ts = datetime.datetime.fromisoformat(
                data.get("updated_at", "")
            ).timestamp()
        except (ValueError, TypeError):
            updated_ts = sp.stat().st_mtime
        age_s = now - updated_ts
        if age_s > AUTO_RESUME_MAX_AGE_S:
            continue
        candidates.append({
            "slot": i,
            "name": f"auto-{i}",
            "path": sp,
            "updated_at": data.get("updated_at", ""),
            "age_s": age_s,
            "turns": n_user,
            "model": data.get("model", ""),
            "mode": data.get("mode", "base"),
            "messages": msgs,
            "last_user": _last_user_preview(msgs),
        })
    if not candidates:
        return None
    candidates.sort(key=lambda c: c["age_s"])
    return candidates[0]


def _last_user_preview(messages: list[dict], n: int = 80) -> str:
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = (m.get("content") or "").strip().splitlines()
            if not content:
                continue
            line = content[0]
            return line[:n] + ("…" if len(line) > n else "")
    return ""


def humanize_age(age_s: float) -> str:
    if age_s < 60:
        return f"{int(age_s)}s ago"
    if age_s < 3600:
        return f"{int(age_s / 60)}m ago"
    if age_s < 86400:
        return f"{int(age_s / 3600)}h ago"
    return f"{int(age_s / 86400)}d ago"
