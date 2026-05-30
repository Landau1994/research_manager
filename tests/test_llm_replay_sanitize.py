"""Tests for replay-safety of saved conversations.

The DeepSeek API rejects multi-turn requests when ``reasoning_content`` is
echoed back inside an assistant message that also carries ``tool_calls``.
The error message it returns is misleading ("tool_calls must be followed
by tool messages"), so this test pins down the actual offender.
"""

from __future__ import annotations

from research_manager.llm.client import (
    _REPLAY_DROP_KEYS,
    _clean_assistant_for_replay,
    sanitize_history,
)


def test_clean_strips_deepseek_reasoning() -> None:
    raw = {
        "role": "assistant",
        "content": "ok",
        "reasoning_content": "long internal chain of thought...",
        "tool_calls": [
            {
                "id": "call_0",
                "type": "function",
                "function": {"name": "do_x", "arguments": "{}"},
            }
        ],
    }
    out = _clean_assistant_for_replay(raw)
    assert "reasoning_content" not in out
    assert out["content"] == "ok"
    assert out["tool_calls"][0]["id"] == "call_0"


def test_clean_drops_null_openai_only_fields() -> None:
    raw = {
        "role": "assistant",
        "content": "hi",
        "function_call": None,
        "audio": None,
        "annotations": None,
        "refusal": None,
        "tool_calls": None,
    }
    out = _clean_assistant_for_replay(raw)
    for k in _REPLAY_DROP_KEYS:
        assert k not in out
    assert out == {"role": "assistant", "content": "hi"}


def test_clean_drops_empty_tool_calls_list() -> None:
    raw = {"role": "assistant", "content": "hi", "tool_calls": []}
    out = _clean_assistant_for_replay(raw)
    assert "tool_calls" not in out


def test_clean_keeps_non_null_tool_calls() -> None:
    tcs = [{"id": "x", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    raw = {"role": "assistant", "content": None, "tool_calls": tcs}
    out = _clean_assistant_for_replay(raw)
    assert out["tool_calls"] == tcs
    # Empty content is required to keep the API happy when only tool_calls is set.
    assert out.get("content", "") == ""


def test_sanitize_history_only_touches_assistant() -> None:
    msgs = [
        {"role": "system", "content": "sys", "extra": "keep"},
        {"role": "user", "content": "u", "reasoning_content": "user-side junk"},
        {
            "role": "assistant",
            "content": "a",
            "reasoning_content": "drop",
            "tool_calls": [{"id": "c0", "type": "function",
                            "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c0", "content": "result"},
    ]
    out = sanitize_history(msgs)
    # system / user / tool are pass-through (extra keys preserved)
    assert out[0]["extra"] == "keep"
    assert "reasoning_content" in out[1]  # user-side intentionally untouched
    assert "reasoning_content" not in out[2]
    assert out[3]["tool_call_id"] == "c0"
