"""LLM client with tool-calling support via OpenAI-compatible API."""

from __future__ import annotations

import json
import os
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

            response = self.client.chat.completions.create(**request_kwargs)

            choice = response.choices[0]
            message = choice.message
            self.messages.append(message.model_dump())

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

                result = ToolRegistry.call_tool(func_name, arguments)

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
