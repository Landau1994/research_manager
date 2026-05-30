"""LLM client with tool-calling support via OpenAI-compatible API."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from openai import OpenAI

from research_manager.llm.prompts import BASE_SYSTEM_PROMPT
from research_manager.tools.registry import ToolRegistry


def _format_tool_result_md(func_name: str, result: str) -> str | None:
    """Format a tool result as a Markdown blockquote for streaming display.

    Returns None for errors/empty so they don't appear in the final document.
    """
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        text = str(result).strip()
        if not text:
            return None
        if len(text) > 800:
            text = text[:797] + "..."
        return f"> 🔧 `{func_name}`:\n> ```\n> " + text.replace("\n", "\n> ") + "\n> ```"

    if isinstance(data, dict) and data.get("error"):
        return None

    text = json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else str(data)
    if len(text) > 800:
        text = text[:797] + "..."
    return f"> 🔧 `{func_name}`:\n> ```\n> " + text.replace("\n", "\n> ") + "\n> ```"


# Fields that ``ChatCompletionMessage.model_dump()`` emits but that are unsafe
# (or just noisy) to replay back to the API:
#
# - ``reasoning_content``: DeepSeek-only "thinking" output. DeepSeek's docs are
#   explicit that this must NOT be sent back in subsequent multi-turn
#   requests, otherwise the API returns a 400. The error message it returns
#   is misleading ("tool_calls must be followed by tool messages") because
#   the validator gets confused when ``reasoning_content`` co-exists with
#   ``tool_calls`` on a replayed assistant message.
# - ``function_call``, ``audio``, ``annotations``, ``refusal``: standard
#   OpenAI fields that come back as ``None`` for typical tool-calling
#   replies. Some OpenAI-compatible providers strict-validate these and
#   reject ``function_call: null`` alongside non-null ``tool_calls``.
#
# We strip them before appending to ``self.messages`` so the persisted /
# replayed conversation contains only the OpenAI-standard subset.
_REPLAY_DROP_KEYS = (
    "reasoning_content",
    "function_call",
    "audio",
    "annotations",
    "refusal",
)


def _clean_assistant_for_replay(message_dict: dict) -> dict:
    """Return a copy of an assistant message safe to send back to the API."""
    out = {
        k: v for k, v in message_dict.items()
        if k not in _REPLAY_DROP_KEYS and v is not None
    }
    # Drop empty tool_calls so the API doesn't see a no-op tool block.
    tcs = out.get("tool_calls")
    if isinstance(tcs, list) and not tcs:
        out.pop("tool_calls", None)
    # Make sure role survives even if it's somehow missing.
    out.setdefault("role", "assistant")
    # An assistant message must have at least content or tool_calls.
    if "tool_calls" not in out and out.get("content") in (None, ""):
        out["content"] = ""
    return out


def sanitize_history(messages: list[dict]) -> list[dict]:
    """Clean a saved conversation in-place so it can be safely replayed.

    Strips the same fields as :func:`_clean_assistant_for_replay` from every
    assistant message. ``user``/``system``/``tool`` messages are passed
    through unchanged. Returns the input list (mutated) for chainability.
    """
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "assistant":
            messages[i] = _clean_assistant_for_replay(m)
    return messages


class ResearchLLMClient:
    """LLM client with tool-calling for research project management."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url or os.getenv(
            "OPENAI_BASE_URL", "https://api.deepseek.com/beta",
        )
        self.model = model or os.getenv("RM_MODEL", "deepseek-v4-pro")

        self.max_iterations = int(os.getenv("RM_MAX_ITERATIONS", "30"))
        self.max_tokens = int(os.getenv("RM_MAX_TOKENS", "8192"))
        self.temperature = float(os.getenv("RM_TEMPERATURE", "0.0"))
        self.reasoning_effort = os.getenv("RM_REASONING_EFFORT", "high")

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        self.system_prompt = system_prompt or BASE_SYSTEM_PROMPT
        self.excluded_tools: frozenset[str] = frozenset()
        self.recorder: Any = None
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt}
        ]

    def reset(self) -> None:
        """Reset conversation history, keeping the current system prompt."""
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def set_system_prompt(self, prompt: str) -> None:
        """Replace the system prompt and reset history."""
        self.system_prompt = prompt
        self.reset()

    def set_recorder(self, recorder: Any) -> None:
        """Attach (or detach with None) a TrajectoryRecorder. See research_manager.recording."""
        self.recorder = recorder
        try:
            from research_manager.recording import set_active_recorder
            set_active_recorder(recorder)
        except Exception:
            pass

    def _maybe_record_counterfactual(
        self,
        request_id: str,
        chosen_message: dict,
        request_kwargs: dict,
    ) -> None:
        """Tier 4: shadow-sample one alternate completion and record it (no execution)."""
        if os.environ.get("RM_RECORD_COUNTERFACTUALS", "").lower() not in ("1", "true", "yes", "on"):
            return
        if self.recorder is None:
            return
        try:
            cf_temp = float(os.environ.get("RM_COUNTERFACTUAL_TEMP", "0.7"))
        except ValueError:
            cf_temp = 0.7
        cf_kwargs = dict(request_kwargs)
        cf_kwargs["temperature"] = cf_temp
        cf_kwargs["n"] = 1
        try:
            from research_manager.recording.recorder import _canonical_bytes, _sha256_bytes
            chosen_hash = _sha256_bytes(_canonical_bytes(chosen_message))
        except Exception:
            chosen_hash = ""
        try:
            cf_response = self.client.chat.completions.create(**cf_kwargs)
            cf_msg = cf_response.choices[0].message.model_dump()
        except Exception:
            return
        try:
            self.recorder.on_counterfactual(
                request_id=request_id,
                chosen_message_hash=chosen_hash,
                rejected_message=cf_msg,
                temperature=cf_temp,
            )
        except Exception:
            pass

    def set_excluded_tools(self, names: frozenset[str] | set[str] | list[str]) -> None:
        """Hide the given tool names from the schemas sent to the LLM.

        This is a mode-gating mechanism: the article-only helpers are not even
        visible to the LLM when the user is in `blog` / `book` / `base` mode,
        so the model cannot call them by mistake.
        """
        self.excluded_tools = frozenset(names)

    def get_tools(self) -> list[dict]:
        schemas = ToolRegistry.get_all_schemas()
        if not self.excluded_tools:
            return schemas
        return [s for s in schemas if s["function"]["name"] not in self.excluded_tools]

    def chat(
        self,
        user_message: str,
        on_tool_call: Callable[[str, dict, str], None] | None = None,
        on_response: Callable[[str], None] | None = None,
    ) -> str:
        """Send a message and run the tool-calling loop until the model stops.

        Args:
            user_message: The user's message.
            on_tool_call: callback(tool_name, args, result) for each tool invocation.
            on_response: callback(content) for each text chunk from the model.

        Returns:
            The final Markdown response (LLM text + formatted tool results).
        """
        self.messages.append({"role": "user", "content": user_message})
        if self.recorder is not None:
            try:
                self.recorder.on_user_message(user_message)
            except Exception:
                pass

        collected_parts: list[str] = []
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1

            extra_kwargs: dict[str, Any] = {}
            if "deepseek-v4" in self.model and self.reasoning_effort != "none":
                extra_kwargs["reasoning_effort"] = self.reasoning_effort
                extra_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

            tools = self.get_tools()
            request_kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": self.messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                **extra_kwargs,
            }
            if tools:
                request_kwargs["tools"] = tools
                request_kwargs["tool_choice"] = "auto"

            req_id = None
            if self.recorder is not None:
                try:
                    req_id = self.recorder.on_llm_request(
                        messages=self.messages,
                        model=self.model,
                        temperature=self.temperature,
                        tools_count=len(tools) if tools else 0,
                        extra=extra_kwargs,
                    )
                except Exception:
                    req_id = None

            t0 = time.monotonic()
            response = self.client.chat.completions.create(**request_kwargs)
            latency_ms = (time.monotonic() - t0) * 1000.0

            choice = response.choices[0]
            message = choice.message
            message_dict = message.model_dump()
            # Replay-safe copy goes into ``self.messages`` so the next API
            # request never sees ``reasoning_content`` etc. The full dump
            # still flows to the recorder + counterfactual sampler below
            # so we don't lose any signal for offline analysis.
            self.messages.append(_clean_assistant_for_replay(message_dict))

            if self.recorder is not None and req_id is not None:
                try:
                    usage = getattr(response, "usage", None)
                    usage_dict = usage.model_dump() if usage is not None else None
                    self.recorder.on_llm_response(
                        request_id=req_id,
                        message=message_dict,
                        finish_reason=choice.finish_reason,
                        usage=usage_dict,
                        latency_ms=latency_ms,
                    )
                except Exception:
                    pass

            if self.recorder is not None and req_id is not None:
                self._maybe_record_counterfactual(req_id, message_dict, request_kwargs)

            if message.content:
                collected_parts.append(message.content)
                if on_response:
                    on_response(message.content)

            if choice.finish_reason == "length":
                self.messages.append({
                    "role": "user",
                    "content": "Continue from where you were cut off; do not repeat.",
                })
                continue

            if not message.tool_calls:
                return "\n\n".join(collected_parts)

            for tool_call in message.tool_calls:
                func_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                step_idx = None
                if self.recorder is not None:
                    try:
                        step_idx = self.recorder.on_tool_call_start(
                            call_id=tool_call.id,
                            name=func_name,
                            args=arguments,
                        )
                    except Exception:
                        step_idx = None

                t_tool = time.monotonic()
                result = ToolRegistry.call_tool(func_name, arguments)
                tool_duration_ms = (time.monotonic() - t_tool) * 1000.0

                if self.recorder is not None and step_idx is not None:
                    try:
                        self.recorder.on_tool_call_end(
                            call_id=tool_call.id,
                            name=func_name,
                            result=result,
                            duration_ms=tool_duration_ms,
                            step_idx=step_idx,
                            error=None,
                        )
                    except Exception:
                        pass

                if on_tool_call:
                    on_tool_call(func_name, arguments, result)

                formatted = _format_tool_result_md(func_name, result)
                if formatted is not None:
                    collected_parts.append(formatted)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

        collected_parts.append(
            f"\n\n> ⚠️ Reached max tool iterations ({self.max_iterations}). "
            f"Set `RM_MAX_ITERATIONS` to raise the cap."
        )
        return "\n\n".join(collected_parts)
