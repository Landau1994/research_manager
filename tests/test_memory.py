"""Tests for the project-memory module."""

from __future__ import annotations

import json
from pathlib import Path

from research_manager.memory import (
    AUTO_RESUME_MAX_AGE_S,
    MEMORY_FILENAME,
    append_memory,
    freshest_resumable_slot,
    inject_into_system_prompt,
    load_memory,
)


def test_load_memory_missing(tmp_path: Path) -> None:
    assert load_memory(tmp_path) == ""


def test_load_memory_present(tmp_path: Path) -> None:
    (tmp_path / MEMORY_FILENAME).write_text("# hi\n\n- one fact\n", encoding="utf-8")
    assert load_memory(tmp_path).startswith("# hi")


def test_inject_empty_is_noop() -> None:
    base = "you are a helper."
    assert inject_into_system_prompt(base, "") == base


def test_inject_appends_block() -> None:
    base = "you are a helper."
    out = inject_into_system_prompt(base, "- fact a\n- fact b")
    assert out.startswith(base)
    assert "MEMORY.md" in out
    assert "fact a" in out
    assert "fact b" in out


def test_inject_is_idempotent() -> None:
    base = "you are a helper."
    once = inject_into_system_prompt(base, "- fact a")
    twice = inject_into_system_prompt(once, "- fact b")
    # The block should be replaced, not duplicated.
    assert twice.count("Project Memory (from MEMORY.md)") == 1
    assert "fact a" not in twice
    assert "fact b" in twice


def test_append_memory_creates_file(tmp_path: Path) -> None:
    res = append_memory(tmp_path, "use conda env raretools for all R scripts")
    assert res["ok"]
    assert res["action"] == "added"
    p = tmp_path / MEMORY_FILENAME
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    assert "# Project Memory" in text
    assert "use conda env raretools" in text


def test_append_memory_explicit_title(tmp_path: Path) -> None:
    res = append_memory(tmp_path, "stick with rpy2 < 4.0", title="R/Python bridge")
    assert res["ok"]
    text = (tmp_path / MEMORY_FILENAME).read_text(encoding="utf-8")
    assert "## R/Python bridge" in text


def test_append_memory_dedupes_section(tmp_path: Path) -> None:
    append_memory(tmp_path, "v1", title="conventions")
    append_memory(tmp_path, "v2", title="conventions")
    text = (tmp_path / MEMORY_FILENAME).read_text(encoding="utf-8")
    # Only one section header, both bodies present.
    assert text.count("## conventions") == 1
    assert "v1" in text and "v2" in text


def test_append_memory_rejects_empty(tmp_path: Path) -> None:
    res = append_memory(tmp_path, "   ")
    assert not res["ok"]


def _write_slot(workspace: Path, slot: int, messages: list[dict], age_s: float) -> Path:
    """Helper: write a session JSON to slot ``slot`` aged ``age_s`` seconds."""
    import datetime
    from research_manager.sessions import _slot_path
    sp = _slot_path(workspace, slot)
    sp.parent.mkdir(parents=True, exist_ok=True)
    iso = (datetime.datetime.now() - datetime.timedelta(seconds=age_s)).isoformat(
        timespec="seconds"
    )
    sp.write_text(json.dumps({
        "version": 1,
        "created_at": iso,
        "updated_at": iso,
        "model": "deepseek-v4-pro",
        "mode": "base",
        "messages": messages,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return sp


def test_freshest_resumable_picks_recent(tmp_path: Path) -> None:
    _write_slot(tmp_path, 0, [{"role": "user", "content": "old"}], age_s=3600 * 5)
    _write_slot(tmp_path, 1, [{"role": "user", "content": "fresh"}], age_s=60)
    cand = freshest_resumable_slot(tmp_path)
    assert cand is not None
    assert cand["slot"] == 1
    assert "fresh" in cand["last_user"]


def test_freshest_resumable_skips_too_old(tmp_path: Path) -> None:
    _write_slot(
        tmp_path, 0,
        [{"role": "user", "content": "ancient"}],
        age_s=AUTO_RESUME_MAX_AGE_S + 60,
    )
    assert freshest_resumable_slot(tmp_path) is None


def test_freshest_resumable_skips_no_user_turns(tmp_path: Path) -> None:
    _write_slot(tmp_path, 0, [{"role": "system", "content": "sys"}], age_s=10)
    assert freshest_resumable_slot(tmp_path) is None


def test_freshest_resumable_none_when_empty(tmp_path: Path) -> None:
    assert freshest_resumable_slot(tmp_path) is None
