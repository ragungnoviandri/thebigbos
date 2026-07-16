"""Anthropic provider implementation."""

import json
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from ..config.manager import ProviderConfig
from .provider import Message, ModelOptions, ModelProvider, ModelResponse, ToolCall


class AnthropicProvider(ModelProvider):
    """Anthropic Claude provider."""

    name = "anthropic"

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.client = AsyncAnthropic(
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
        system, formatted = self._format_messages(messages)
        anthropic_tools = self._convert_tools(tools)

        kwargs: dict[str, Any] = {
            "model": opts.model,
            "messages": formatted,
            "max_tokens": opts.max_tokens,
            "temperature": opts.temperature,
        }

        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
        if opts.thinking_budget and "sonnet" in opts.model.lower():
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": opts.thinking_budget}

        # ── API Logging (lazy import to avoid circular deps) ──
        from ..core.api_logger import get_logger
        logger = get_logger()
        logger.log_request(
            provider="anthropic",
            model=opts.model,
            method="POST",
            url=str(self.client.base_url) + "/v1/messages",
            headers={"x-api-key": "***", "anthropic-version": "2023-06-01"},
            body={"model": opts.model, "system": system, "messages": formatted,
                   "max_tokens": opts.max_tokens, "tools": anthropic_tools},
            call_ref=kwargs,
        )

        try:
            response = await self.client.messages.create(**kwargs)
        except Exception as e:
            logger.log_response(
                provider="anthropic", model=opts.model, status_code=0,
                error=str(e), usage={}, call_ref=kwargs,
            )
            return ModelResponse(content=f"[API Error] {e}", finish_reason="error")

        tool_calls = []
        text_content = ""
        reasoning_content = ""

        for block in response.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input or {},
                ))
            elif block.type == "thinking":
                reasoning_content += block.thinking
            elif block.type == "redacted_thinking":
                reasoning_content += f"[redacted: {block.data}]"

        # ── API Logging ──
        logger.log_response(
            provider="anthropic",
            model=opts.model,
            status_code=200,
            body={
                "content": text_content,
                "reasoning_content": reasoning_content,
                "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in tool_calls],
            },
            usage={"input": response.usage.input_tokens if response.usage else 0,
                   "output": response.usage.output_tokens if response.usage else 0,
                   "total": (response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0},
            call_ref=kwargs,
        )

        return ModelResponse(
            content=text_content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            finish_reason=response.stop_reason or "end_turn",
            usage={
                "input": response.usage.input_tokens if response.usage else 0,
                "output": response.usage.output_tokens if response.usage else 0,
                "total": (response.usage.input_tokens + response.usage.output_tokens)
                    if response.usage else 0,
            },
        )

    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        options: ModelOptions | None = None,
    ) -> AsyncIterator[str]:
        opts = options or ModelOptions(model=self.config.default_model)
        system, formatted = self._format_messages(messages)
        anthropic_tools = self._convert_tools(tools)

        kwargs: dict[str, Any] = {
            "model": opts.model,
            "messages": formatted,
            "max_tokens": opts.max_tokens,
            "temperature": opts.temperature,
        }
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text

    def _format_messages(self, messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        """Convert to Anthropic format. Returns (system_text, conversation_messages)."""
        system_parts = []
        conversation = []

        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            elif m.role == "tool":
                conversation.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content,
                    }],
                })
            elif m.role == "assistant" and m.tool_calls:
                blocks = [{"type": "text", "text": m.content or ""}]
                for tc in m.tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                conversation.append({"role": "assistant", "content": blocks})
            else:
                conversation.append({"role": m.role, "content": m.content})

        return "\n\n".join(system_parts), conversation

    def _convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-style tool definitions to Anthropic format."""
        anthropic_tools = []
        for tool in tools:
            func = tool.get("function", tool)
            anthropic_tools.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}, "required": []}),
            })
        return anthropic_tools
