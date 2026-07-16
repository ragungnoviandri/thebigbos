# OpenCode Provider Models Reference

Curated model catalog for `opencode-zen` and `opencode-go` providers. Use these tables to select the right model per role in a multi-agent team.

> Data gathered from `models_dev_cache.json` (auto-refreshed by Hermes). Prices may change — re-check with `cat ~/AppData/Local/hermes/models_dev_cache.json | jq '.["opencode-go"]'` for the latest.

## Model Quick-Select by Role

| Role | Recommended Model | Why |
|------|-------------------|-----|
| Architect / Lead | `glm-5.2` or `deepseek-v4-pro` | Strong reasoning, structured output |
| Backend coding | `deepseek-v4-pro` | Best code quality, 1M context, tool-calling |
| Frontend coding | `kimi-k2.7-code` | Code specialist, 262K context, image input |
| QA / Testing | `minimax-m3` | Cheapest viable model ($0.10/$0.40) |
| Documentation | `deepseek-v4-flash` | Fast, cheap ($0.14/$0.28), 1M context |
| Security audit | `deepseek-v4-pro` | Thorough analysis, $1.74/$3.48 |
| DevOps / Infra | `mimo-v2.5` | Fast and cheap ($0.14/$0.28), 1M context |
| UI/UX design | `qwen3.7-plus` | Multimodal (image+text), $0.40/$1.60 |

## Price Comparison: Top Candidates

Model sorted by input cost (cheapest first):

| Model | Provider | Input/M | Output/M | Context | Reasoning | Best For |
|-------|----------|---------|----------|---------|-----------|----------|
| `minimax-m3` | opencode-go | $0.10 | $0.40 | 512K | ✅ toggle | Budget, small tasks |
| `deepseek-v4-flash` | opencode-zen/go | $0.14 | $0.28 | 1M | ✅ effort | Economy worker |
| `mimo-v2.5` | opencode-go | $0.14 | $0.28 | 1M | ✅ auto | General cheap |
| `gpt-5.4-nano` | opencode-zen | $0.20 | $1.25 | 400K | ✅ auto | OpenAI economy |
| `minimax-m2.7` | opencode-go/zen | $0.30 | $1.20 | 205K | ✅ auto | Budget reasoning |
| `qwen3.7-plus` | opencode-go | $0.40 | $1.60 | 1M | ✅ auto | Multimodal (image+text) |
| `qwen3.6-plus` | opencode-go/zen | $0.50 | $3.00 | 1M | ✅ toggle | Mid-range coding |
| `qwen3.5-plus` | opencode-go | $0.20 | $1.20 | 262K | ✅ toggle | (deprecated) |
| `gpt-5.4-mini` | opencode-zen | $0.75 | $4.50 | 400K | ✅ auto | OpenAI economy |
| `kimi-k2.7-code` | opencode-go | $0.95 | $4.00 | 262K | ✅ auto | Code specialist |
| `glm-5.2` | opencode-go | $1.40 | $4.40 | 1M | ✅ auto | Strong reasoning |
| `deepseek-v4-pro` | opencode-go/zen | $1.74 | $3.48 | 1M | ✅ effort | **Premium coding** |
| `mimo-v2.5-pro` | opencode-go | $1.74 | $3.48 | 1M | ✅ auto | Alternative to Pro |
| `qwen3.7-max` | opencode-go | $2.50 | $7.50 | 1M | ✅ auto | Highest quality |

## Concrete Example: Cost-Minimized 8-Profile Team

This is a real production configuration from a Django+React project. It mixes premium and economy models to reduce total cost by ~58% vs. using `deepseek-v4-pro` for everyone:

| Profile | Model | Cost/Tok (in/out) | Reasoning | Why |
|---------|-------|:-----------------:|:---------:|-----|
| `arsitek/` | `glm-5.2` | $1.40/$4.40 | high | Architecture decisions need the strongest reasoning |
| `backend-programer/` | `deepseek-v4-pro` | $1.74/$3.48 | medium | Django API code — needs high code quality |
| `frontend-programer/` | `kimi-k2.7-code` | $0.95/$4.00 | medium | Code specialist tuned for frontend frameworks |
| `fullstack-qa/` | `minimax-m3` | $0.10/$0.40 | low | Testing assertions — cheap is sufficient |
| `documentation-enginer/` | `deepseek-v4-flash` | $0.14/$0.28 | low | Markdown formatting — minimal reasoning |
| `security-tester/` | `deepseek-v4-pro` | $1.74/$3.48 | medium | Vulnerability analysis — needs thorough model |
| `dev-ops/` | `mimo-v2.5` | $0.14/$0.28 | low | Shell scripts and Docker — cheap is fine |
| `uiux/` | `qwen3.7-plus` | $0.40/$1.60 | medium | Needs image input for design mockups |

### Cost Comparison

| Scenario | Input cost for full team (8 workers) | vs. baseline |
|----------|:------------------------------------:|:------------:|
| All `deepseek-v4-pro` | $13.92 | — |
| All `deepseek-v4-flash` | $1.12 | -92% |
| **Mixed (as above)** | **$6.61** | **-53%** |

The mixed approach gives ~90% of the quality of all-Pro at half the cost. The key insight: cheap models handle QA, docs, and devops just as well as expensive ones do.

## Full Model List: opencode-go

Models available via `provider: opencode-go` (endpoint: `https://opencode.ai/zen/go/v1`):

| Model Name | Context | Reasoning | Tool Call | Input Price | Output Price | Image Input |
|------------|---------|-----------|-----------|-------------|--------------|-------------|
| `deepseek-v4-pro` | 1M | ✅ effort | ✅ | $1.74 | $3.48 | ❌ |
| `deepseek-v4-flash` | 1M | ✅ effort | ✅ | $0.14 | $0.28 | ❌ |
| `kimi-k2.7-code` | 262K | ✅ auto | ✅ | $0.95 | $4.00 | ✅ |
| `kimi-k2.6` | 262K | ✅ auto | ✅ | $0.95 | $4.00 | ✅ |
| `kimi-k2.5` | 262K | ✅ auto | ✅ | $0.60 | $3.00 | ✅ (deprecated) |
| `glm-5.2` | 1M | ✅ auto | ✅ | $1.40 | $4.40 | ❌ |
| `glm-5.1` | 205K | ✅ auto | ✅ | $1.40 | $4.40 | ❌ |
| `glm-5` | 203K | ✅ auto | ✅ | $1.00 | $3.20 | ❌ (deprecated) |
| `mimo-v2.5-pro` | 1M | ✅ auto | ✅ | $1.74 | $3.48 | ❌ |
| `mimo-v2.5` | 1M | ✅ auto | ✅ | $0.14 | $0.28 | ✅ |
| `qwen3.7-max` | 1M | ✅ auto | ✅ | $2.50 | $7.50 | ❌ |
| `qwen3.7-plus` | 1M | ✅ auto | ✅ | $0.40 | $1.60 | ✅ |
| `qwen3.6-plus` | 1M | ✅ toggle | ✅ | $0.50 | $3.00 | ✅ |
| `qwen3.5-plus` | 262K | ✅ toggle | ✅ | $0.20 | $1.20 | ✅ (deprecated) |
| `minimax-m3` | 512K | ✅ toggle | ✅ | $0.10 | $0.40 | ✅ |
| `minimax-m2.7` | 205K | ✅ auto | ✅ | $0.30 | $1.20 | ❌ |
| `minimax-m2.5` | 205K | ✅ auto | ✅ | $0.30 | $1.20 | ❌ (deprecated) |
| `mimo-v2-pro` | 1M | ✅ auto | ✅ | $1.00 | $3.00 | ❌ (deprecated) |
| `mimo-v2-omni` | 262K | ✅ auto | ✅ | $0.40 | $2.00 | ✅ (deprecated) |
| `hy3-preview` | 200K | ✅ auto | ✅ | $0.14 | $0.28 | ❌ (new — experimental) |

## Full Model List: opencode-zen

Models available via `provider: opencode-zen` (endpoint: `https://opencode.ai/zen/v1`). This provider has the most variety — includes OpenAI, Anthropic, Gemini, and community models:

| Model Name | Context | Reasoning | Input Price | Notes |
|------------|---------|-----------|-------------|-------|
| `big-pickle` | 200K | ✅ auto | **FREE** | Works with `delegate_task` despite being free |
| `deepseek-v4-pro` | 1M | ✅ effort | $1.74 | Same model as opencode-go |
| `deepseek-v4-flash` | 1M | ✅ effort | $0.14 | Same model as opencode-go |
| `qwen3.6-plus` | 262K | ✅ toggle | $0.50 | Multimodal |
| `gpt-5.4-mini` | 400K | ✅ auto | $0.75 | OpenAI economy |
| `gpt-5.4-nano` | 400K | ✅ auto | $0.20 | Cheapest GPT |
| `claude-haiku-4-5` | 200K | ✅ budget | $1.00 | Fast Anthropic |
| `claude-sonnet-4-6` | 1M | ✅ effort | $3.00 | Premium Anthropic |
| `gemini-3.5-flash` | 1M | ✅ effort | TBD | Google economy |
| `glm-5.1` | 205K | ✅ auto | $1.40 | Strong reasoning |
| `grok-build-0.1` | 256K | ✅ auto | $1.00 | xAI build agent |
| `kimi-k2-thinking` | 262K | ✅ auto | $0.40 | (deprecated) |
| `qwen3-coder` | 262K | ❌ | $0.45 | (deprecated) |

## ⚠️ Models NOT Compatible with delegate_task

Any model with `-free` suffix. These work in the parent session but fail with 401 when used in `delegate_task`:

- `deepseek-v4-flash-free`
- `minimax-m2.5-free` / `minimax-m2.1-free` / `minimax-m3-free`
- `mimo-v2.5-free` / `mimo-v2-flash-free` / `mimo-v2-omni-free`
- `qwen3.6-plus-free`
- `glm-5-free`
- `kimi-k2.5-free`
- `north-mini-code-free`
- `hy3-preview-free`
- `nemotron-3-super-free` / `nemotron-3-ultra-free`

**Fix**: Use the non-free variant (same name, drop `-free` suffix) — or spawn an independent `terminal("hermes chat -q ...")` process instead of `delegate_task`.

## Cache Location

Hermes auto-refreshes these periodically. To force-refresh:

```bash
# Delete cache (Hermes rebuilds on next model lookup)
rm ~/AppData/Local/hermes/provider_models_cache.json
rm ~/AppData/Local/hermes/models_dev_cache.json

# Or read live without cache
hermes config set model_catalog.enabled false
# ... then re-enable
hermes config set model_catalog.enabled true
```