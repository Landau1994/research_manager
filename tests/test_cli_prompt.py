"""Smoke tests for the REPL prompt module (Tab completion)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("prompt_toolkit")
from prompt_toolkit.document import Document  # noqa: E402

from research_manager.cli_prompt import _AtPathCompleter  # noqa: E402


def _make_ws(tmp_path: Path) -> Path:
    (tmp_path / "script").mkdir()
    (tmp_path / "script" / "foo.py").write_text("")
    (tmp_path / "script" / "bar.py").write_text("")
    (tmp_path / "data").mkdir()
    (tmp_path / "README.md").write_text("")
    (tmp_path / ".hidden").write_text("")
    return tmp_path


def _comps(c: _AtPathCompleter, text: str) -> list[tuple[str, int]]:
    d = Document(text=text, cursor_position=len(text))
    return [(x.text, x.start_position) for x in c.get_completions(d, None)]


def test_at_completes_top_level_dir(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    c = _AtPathCompleter(workspace_getter=lambda: ws)
    assert _comps(c, "look at @scr") == [("script/", -3)]


def test_at_descends_into_subdir(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    c = _AtPathCompleter(workspace_getter=lambda: ws)
    out = sorted(_comps(c, "@script/"))
    assert out == [("script/bar.py", -7), ("script/foo.py", -7)]


def test_at_filters_subdir_by_prefix(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    c = _AtPathCompleter(workspace_getter=lambda: ws)
    assert _comps(c, "@script/f") == [("script/foo.py", -8)]


def test_email_like_does_not_trigger(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    c = _AtPathCompleter(workspace_getter=lambda: ws)
    assert _comps(c, "email me at user@host") == []


def test_no_at_no_completion(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    c = _AtPathCompleter(workspace_getter=lambda: ws)
    assert _comps(c, "hello world") == []


def test_dotfile_only_with_explicit_dot(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    c = _AtPathCompleter(workspace_getter=lambda: ws)
    # Plain `@` shouldn't include `.hidden`
    plain = [name for name, _ in _comps(c, "@")]
    assert ".hidden" not in plain
    # But `@.` should
    explicit = [name for name, _ in _comps(c, "@.")]
    assert ".hidden" in explicit


def test_slash_command_completion(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    c = _AtPathCompleter(workspace_getter=lambda: ws)
    out = sorted(name for name, _ in _comps(c, "/se"))
    assert out == ["/sessions"]


def test_slash_completion_only_first_token(tmp_path: Path) -> None:
    """Once the user has typed past the slash command, Tab should not
    keep offering /commands."""
    ws = _make_ws(tmp_path)
    c = _AtPathCompleter(workspace_getter=lambda: ws)
    assert _comps(c, "/load some") == []
