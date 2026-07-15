"""Ollama provider implementation (OpenAI-compatible endpoint)."""

import json
from typing import Any, AsyncIterator

from .provider import Message, ModelOptions, ModelProvider, ModelResponse
from ..config.manager import ProviderConfig


class OllamaProvider(ModelProvider):
    """Ollama local model provider via OpenAI-compatible API."""

    name = "ollama"

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.base_url = (config.base_url or "http://localhost:11434/v1").rstrip("/")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key="ollama",
                base_url=self.base_url,
                timeout=self.config.timeout,
            )
        return self._client

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        options: ModelOptions | None = None,
    ) -> ModelResponse:
        opts = options or ModelOptions(model=self.config.default_model)
        formatted = self._format_messages(messages)

        kwargs: dict[str, Any] = {
            "model": opts.model,
            "messages": formatted,
            "max_tokens": min(opts.max_tokens, 2048),
            "temperature": opts.temperature,
        }
        if tools:
            kwargs["tools"] = tools

        import json

        # ── API Logging (lazy import to avoid circular deps) ──
        from ..core.api_logger import get_logger
        logger = get_logger()
        logger.log_request(
            provider="ollama",
            model=opts.model,
            method="POST",
            url=f"{self.base_url}/chat/completions",
            headers={"Authorization": "ollama", "Content-Type": "application/json"},
            body={"model": opts.model, "messages": formatted,
                   "max_tokens": min(opts.max_tokens, 2048), "tools": tools},
        )

        try:
            response = await self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]

            tool_calls = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

            # ── API Logging ──
            logger.log_response(
                provider="ollama",
                model=opts.model,
                status_code=200,
                body={
                    "content": choice.message.content or "",
                    "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in tool_calls],
                },
                usage={},
                call_ref=kwargs,
            )

            return ModelResponse(
                content=choice.message.content or "",
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason or "stop",
                usage={},
            )
        except Exception as e:
            logger.log_response(
                provider="ollama", model=opts.model, status_code=0,
                error=str(e), usage={}, call_ref=kwargs,
            )
            return ModelResponse(
                content=f"[Ollama Error] {e}",
                finish_reason="error",
            )

    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        options: ModelOptions | None = None,
    ) -> AsyncIterator[str]:
        opts = options or ModelOptions(model=self.config.default_model)

        kwargs: dict[str, Any] = {
            "model": opts.model,
            "messages": self._format_messages(messages),
            "max_tokens": min(opts.max_tokens, 2048),
            "temperature": opts.temperature,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def _format_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        formatted = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role}
            if m.role == "assistant" and m.tool_calls:
                msg["content"] = m.content or None
            elif m.role == "tool":
                msg["content"] = m.content or "(empty)"
                if m.tool_call_id:
                    msg["tool_call_id"] = m.tool_call_id
                if m.name:
                    msg["name"] = m.name
                formatted.append(msg)
                continue
            elif m.content:
                msg["content"] = m.content
            else:
                continue  # skip empty messages

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
