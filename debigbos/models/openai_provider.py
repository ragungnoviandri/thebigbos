"""OpenAI provider implementation."""

import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from ..config.manager import ProviderConfig
from .provider import Message, ModelOptions, ModelProvider, ModelResponse, ToolCall


class OpenAIProvider(ModelProvider):
    """OpenAI-compatible provider (OpenAI, Azure, etc.)."""

    name = "openai"

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        options: ModelOptions | None = None,
    ) -> ModelResponse:
        opts = options or ModelOptions(model=self.config.default_model)
        formatted = self._format_messages(messages, opts.model)

        kwargs: dict[str, Any] = {
            "model": opts.model,
            "messages": formatted,
            "max_tokens": opts.max_tokens,
        }

        # Skip temp/top_p for reasoning models (DeepSeek V4, o1/o3, etc.)
        is_reasoning = any(r in opts.model.lower() for r in ("o1", "o3", "o4", "v4-pro", "v4-flash", "r1", "kimi-k2", "qwen3.5"))
        if not is_reasoning:
            kwargs["temperature"] = opts.temperature
            kwargs["top_p"] = opts.top_p

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if opts.reasoning_effort and opts.model.startswith(("o1", "o3", "o4")):
            kwargs["reasoning_effort"] = opts.reasoning_effort

        # ── API Logging (lazy import to avoid circular deps) ──
        from ..core.api_logger import get_logger
        logger = get_logger()
        base = str(self.client.base_url).rstrip("/")
        logger.log_request(
            provider="openai",
            model=opts.model,
            method="POST",
            url=f"{base}/chat/completions",
            headers={"Authorization": "***", "Content-Type": "application/json"},
            body={"model": opts.model, "messages": formatted,
                   "max_tokens": opts.max_tokens, "tools": tools},
            call_ref=kwargs,
        )

        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.log_response(
                provider="openai", model=opts.model, status_code=0,
                error=str(e), usage={}, call_ref=kwargs,
            )
            return ModelResponse(
                content=f"[API Error] {e}",
                finish_reason="error",
            )

        if isinstance(response, str):
            return ModelResponse(
                content=f"[API Error] Unexpected response: {response[:500]}",
                finish_reason="error",
            )

        choice = response.choices[0]

        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        # Some reasoning models return content=None with reasoning_content
        content = choice.message.content or ""
        reasoning_content = ""
        if hasattr(choice.message, "reasoning_content") and choice.message.reasoning_content:
            reasoning_content = choice.message.reasoning_content

        # ── API Logging ──
        logger.log_response(
            provider="openai",
            model=opts.model,
            status_code=200,
            body={
                "content": content,
                "reasoning_content": reasoning_content,
                "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in tool_calls],
            },
            usage={
                "input": response.usage.prompt_tokens if response.usage else 0,
                "output": response.usage.completion_tokens if response.usage else 0,
                "total": response.usage.total_tokens if response.usage else 0,
            },
            call_ref=kwargs,
        )

        return ModelResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage={
                "input": response.usage.prompt_tokens if response.usage else 0,
                "output": response.usage.completion_tokens if response.usage else 0,
                "total": response.usage.total_tokens if response.usage else 0,
            },
        )

    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        options: ModelOptions | None = None,
    ) -> AsyncIterator[str]:
        opts = options or ModelOptions(model=self.config.default_model)
        formatted = self._format_messages(messages, opts.model)

        kwargs: dict[str, Any] = {
            "model": opts.model,
            "messages": formatted,
            "max_tokens": opts.max_tokens,
            "temperature": opts.temperature,
            "top_p": opts.top_p,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def _format_messages(self, messages: list[Message], model_name: str | None = None) -> list[dict[str, Any]]:
        """Convert internal messages to OpenAI format.

        DeepSeek upstream requires reasoning_content on every assistant message.
        Reasoning role messages are folded into the preceding assistant's reasoning_content.
        """
        formatted: list[dict[str, Any]] = []
        is_deepseek = "deepseek" in (model_name or self.config.default_model).lower()

        for m in messages:
            msg: dict[str, Any] = {"role": m.role}

            # ——— reasoning role → fold into previous assistant ———
            if m.role == "reasoning":
                if formatted and formatted[-1].get("role") == "assistant":
                    existing = formatted[-1].get("reasoning_content", "") or ""
                    formatted[-1]["reasoning_content"] = (existing + "\n" + m.content).strip()
                else:
                    formatted.append(msg)
                continue

            # ——— content handling ———
            if m.role == "assistant" and m.tool_calls:
                msg["content"] = m.content or None
            elif m.role == "tool":
                msg["content"] = m.content or "(empty)"
                if m.tool_call_id:
                    msg["tool_call_id"] = m.tool_call_id
                formatted.append(msg)
                continue
            elif m.content:
                msg["content"] = m.content
            else:
                continue

            # ——— reasoning_content on assistant messages ———
            if m.role == "assistant":
                if m.reasoning_content:
                    msg["reasoning_content"] = m.reasoning_content
                elif is_deepseek:
                    # DeepSeek upstream requires reasoning_content on ALL assistant msgs
                    msg["reasoning_content"] = ""

            # ——— tool_calls ———
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]

            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.name:
                msg["name"] = m.name
            formatted.append(msg)
        return formatted

    def count_tokens(self, messages: list[Message]) -> int:
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model("gpt-4o")
            total = 0
            for m in messages:
                total += len(enc.encode(m.content)) + 4
            return total
        except Exception:
            return super().count_tokens(messages)

    async def fetch_models(self) -> list[str]:
        """Fetch available models from the OpenAI-compatible /v1/models endpoint."""
        try:
            models = await self.client.models.list()
            # Filter to chat-capable models only (exclude embeddings, audio, etc.)
            chat_models = []
            for m in models.data:
                mid = m.id
                # Skip non-chat models
                if any(skip in mid for skip in ["embedding", "tts", "whisper", "dall-e", "babbage", "davinci"]):
                    continue
                chat_models.append(mid)
            return sorted(chat_models)
        except Exception:
            return []
