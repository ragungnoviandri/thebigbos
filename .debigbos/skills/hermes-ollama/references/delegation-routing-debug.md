# Delegation Model Routing — Debug Session

Date: 2026-06-16
Host: Legion-Pro-5-6IRX9 (Windows 10)
GPU: NVIDIA GeForce RTX 4070 Laptop GPU (8 GB VRAM)
Hermes Profile: default
Main Provider: opencode-zen (model: big-pickle)
Delegation Provider: ollama-launch → opencode-go

## CRITICAL FINDING: delegate_task Always Uses Parent Model

**No matter what delegation config is set (api_mode, model, provider, base_url), `delegate_task` sub-agents always inherit the parent's model for their reasoning loop.** The delegation config settings are relevant only for ACP transport mode or other delegation mechanisms, NOT the default Hermes sub-agent transport.

### Workaround
To use a different model for sub-tasks, spawn a new Hermes process via terminal:
```bash
terminal(command='hermes chat -q "your task here" --provider opencode-go --model deepseek-v4-pro', timeout=300)
```

## Initial Config

```yaml
delegation:
  api_key: ''
  api_mode: ''           # ← This doesn't affect sub-agent reasoning
  base_url: ''
  model: qwen2.5-coder:14b
  provider: ollama-launch
```

## Observed Behavior — Ollama Local

| Test | What Happened | GPU VRAM | Duration | Notes |
|------|--------------|----------|----------|-------|
| delegate_task (echo) | Sub-agent ran terminal, returned result | 0 MiB | 7.5s | Sub-agent used parent model (big-pickle) for reasoning |
| delegate_task (file ops) | Sub-agent read/wrote in project dir | 0 MiB | 102s | No GPU activity despite delegation config set to ollama |
| delegate_task with explicit model param | Sub-agent reported GPU state | 0 MiB | 8.8s | Metadata always shows parent `model: big-pickle` |
| Direct curl to Ollama API | Model loaded on GPU via llama-server.exe | **5953 MiB** | 8.0s | 87% GPU / 13% CPU layers — only works with direct API calls |

**Key insight:** GPU works great when you make direct API calls to Ollama. But delegate_task sub-agents never route their own LLM calls through Ollama — they use the parent session's model/provider.

## Attempt 1: Set api_mode + base_url for Ollama

```yaml
delegation:
  api_mode: chat_completions
  base_url: http://127.0.0.1:11434/v1
  model: qwen3-coder:30b
  provider: ollama-launch
```

**Result:** No change. GPU still 0 MiB during delegate_task. Sub-agents still used parent model. Even with api_mode set and Ollama running, the sub-agent's reasoning never touched Ollama.

## Attempt 2: Sub-agent Making Direct Ollama API Calls

When the sub-agent was asked to `curl` Ollama's API directly from within the sub-agent:
- ✅ It worked — model loaded on GPU (5953 MiB)
- ✅ Fast response (2.2s)
- But this was the sub-agent running a terminal command, not using Ollama for its own reasoning

## Final Config: OpenCode Go Cloud Provider

After testing confirmed local GPU models wouldn't route through delegate_task, switched to cloud API:

```bash
hermes config set delegation.provider opencode-go
hermes config set delegation.model deepseek-v4-pro
hermes config set delegation.base_url ''
```

Final config doesn't matter much since sub-agents still use parent model anyway. But user prefers the config to be set to opencode-go/deepseek-v4-pro for when delegation config IS honored.

### Env Vars Available
- `OPENCODE_GO_API_KEY` — stored in `~/.hermes/.env`
- `OPENCODE_GO_BASE_URL` — optional override, default is OpenCode Go API endpoint