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
            "messages": self._format_messages(messages, opts.model),
            "max_tokens": opts.max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        # Enable thinking for DeepSeek/Claude models
        # NOTE: Some upstreams reject "budget_tokens" inside the thinking block.
        # We send a simple {"type": "enabled"} and rely on max_tokens to cap.
        if opts.thinking_budget and opts.thinking_budget > 0:
            body["thinking"] = {"type": "enabled"}
        if opts.reasoning_effort:
            body["reasoning_effort"] = opts.reasoning_effort

        url = f"{self.base_url}/chat/completions"

        # ── API Logging (lazy import to avoid circular deps) ──
        from ..core.api_logger import get_logger
        logger = get_logger()
        safe_headers = {
            "Authorization": "***",
            "Content-Type": "application/json",
        }
        body_ref = dict(body)  # reference for elapsed time
        logger.log_request(
            provider="opencode-go",
            model=opts.model,
            method="POST",
            url=url,
            headers=safe_headers,
            body=body,
            call_ref=body_ref,
        )

        try:
            response = await self.client.post(url, json=body)
        except httpx.TimeoutException as e:
            logger.log_response(provider="opencode-go", model=opts.model, status_code=0,
                               error=f"Timeout: {e}", usage={}, call_ref=body_ref)
            return ModelResponse(
                content=f"[API Error] Request timed out ({self.config.timeout}s). The provider may be overloaded.",
                finish_reason="error",
            )
        except httpx.ConnectError as e:
            logger.log_response(provider="opencode-go", model=opts.model, status_code=0,
                               error=f"Connect error: {e}", usage={}, call_ref=body_ref)
            return ModelResponse(
                content=f"[API Error] Cannot connect to {self.base_url}. Check network or API status.",
                finish_reason="error",
            )
        except Exception as e:
            logger.log_response(provider="opencode-go", model=opts.model, status_code=0,
                               error=str(e), usage={}, call_ref=body_ref)
            return ModelResponse(content=f"[API Error] {e}", finish_reason="error")

        if response.status_code != 200:
            try:
                data = response.json()
            except Exception:
                data = {}
            error_msg = data.get("error", {}).get("message", response.text[:300] or str(response.status_code))

            logger.log_response(provider="opencode-go", model=opts.model,
                               status_code=response.status_code, error=error_msg, usage={}, call_ref=body_ref)

            # Classify errors for better user guidance
            if response.status_code == 401:
                prefix = "[Auth Error]"
                hint = " — check your API key (deBigBos configure --key opencode-go=YOUR_KEY)"
            elif response.status_code == 403:
                # 403 can be auth OR model-not-available
                if "not supported" in error_msg.lower() or "model" in error_msg.lower():
                    prefix = "[Model Error]"
                    hint = f" — try another model or run `deBigBos models` to list available models"
                else:
                    prefix = "[Auth Error]"
                    hint = " — check your API key (deBigBos configure --key opencode-go=YOUR_KEY)"
            elif response.status_code == 429:
                prefix = "[Rate Limit]"
                hint = " — too many requests, wait and retry"
            elif response.status_code >= 500:
                prefix = "[Upstream Error]"
                hint = " — the provider's upstream is down, try again later"
            elif response.status_code == 400 or response.status_code == 422:
                prefix = "[Payload Error]"
                hint = " — message format issue (try /fix to clean corrupted session)"
            else:
                prefix = "[API Error]"
                hint = ""

            return ModelResponse(
                content=f"{prefix} {error_msg}{hint}",
                finish_reason="error",
            )

        data = response.json()
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

        # ── API Logging ──
        logger.log_response(
            provider="opencode-go",
            model=opts.model,
            status_code=200,
            body={
                "content": content,
                "reasoning_content": reasoning_content,
                "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in tool_calls],
            },
            usage={
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            },
            call_ref=body_ref,
        )

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
            "messages": self._format_messages(messages, opts.model),
            "max_tokens": opts.max_tokens,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        # Enable thinking for DeepSeek/Claude models
        # NOTE: Some upstreams reject "budget_tokens" inside the thinking block.
        # We send a simple {"type": "enabled"} and rely on max_tokens to cap.
        if opts.thinking_budget and opts.thinking_budget > 0:
            body["thinking"] = {"type": "enabled"}
        if opts.reasoning_effort:
            body["reasoning_effort"] = opts.reasoning_effort

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
                            reasoning = delta.get("reasoning_content", "")
                            if reasoning:
                                yield f"[reasoning]{reasoning}[/reasoning]"
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            yield f"\n[Error: {e}]"

    def _format_messages(self, messages: list[Message], model_name: str | None = None) -> list[dict[str, Any]]:
        """Convert internal messages to OpenAI/Anthropic-compatible format.

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
        """Fetch available models from the OpenCode API."""
        url = f"{self.base_url}/models"
        try:
            response = await self.client.get(url)
            if response.status_code == 200:
                data = response.json()
                return [m["id"] for m in data.get("data", [])]
        except Exception:
            pass
        return []
