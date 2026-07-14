"""OpenCode Go provider — dedicated implementation using httpx directly."""

import json
from typing import Any, AsyncIterator

import httpx

from ..config.manager import ProviderConfig
from .provider import Message, ModelOptions, ModelProvider, ModelResponse, ToolCall


class OpencodeGoProvider(ModelProvider):
    """OpenCode Go provider via https://opencode.ai/zen/go/v1."""

    name = "opencode-go"

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.base_url = (config.base_url or "https://opencode.ai/zen/go/v1").rstrip("/")
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout),
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        options: ModelOptions | None = None,
    ) -> ModelResponse:
        opts = options or ModelOptions(model=self.config.default_model)
        body: dict[str, Any] = {
            "model": opts.model,
            "messages": self._format_messages(messages),
            "max_tokens": opts.max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        url = f"{self.base_url}/chat/completions"
        try:
            response = await self.client.post(url, json=body)
            data = response.json()
        except Exception as e:
            return ModelResponse(content=f"[API Error] {e}", finish_reason="error")

        if response.status_code != 200:
            error_msg = data.get("error", {}).get("message", str(data))
            return ModelResponse(
                content=f"[API Error] {error_msg}",
                finish_reason="error",
            )

        choice = data["choices"][0]
        msg = choice.get("message", {})

        content = msg.get("content") or ""
        reasoning_content = msg.get("reasoning_content") or ""

        tool_calls = []
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=func.get("name", ""), arguments=args))

        usage = data.get("usage", {})
        return ModelResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            usage={
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            },
        )

    async def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        options: ModelOptions | None = None,
    ) -> AsyncIterator[str]:
        opts = options or ModelOptions(model=self.config.default_model)
        body: dict[str, Any] = {
            "model": opts.model,
            "messages": self._format_messages(messages),
            "max_tokens": opts.max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        url = f"{self.base_url}/chat/completions"
        try:
            async with self.client.stream("POST", url, json=body) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                            # Some reasoning models put text in reasoning_content, not content
                            reasoning = delta.get("reasoning_content", "")
                            if reasoning and not content:
                                pass  # Don't yield reasoning — it's internal thinking
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            yield f"\n[Error: {e}]"

    def _format_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        formatted = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
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
