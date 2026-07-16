---
name: hermes-ollama
description: Configure Hermes Agent to use local Ollama models for inference and sub-agent delegation. Covers provider setup, delegation routing, GPU verification, and troubleshooting.
tags: [hermes, ollama, local-models, delegation, gpu, sub-agents, configuration]
author: agent
version: 1.0.0
---

# Hermes + Ollama Local Models

Use when setting up, testing, or troubleshooting Hermes Agent with Ollama local models — especially for sub-agent delegation and GPU utilization.

## Ollama as a Provider

Ollama serves an OpenAI-compatible API at `http://127.0.0.1:11434/v1`. Register it in `providers` section of `config.yaml`:

```yaml
providers:
  ollama-launch:
    api: http://127.0.0.1:11434/v1
    default_model: <model-name>
    models:
      - <model-1>
      - <model-2>
    name: Ollama
```

## Changing the Delegation Model

Sub-agents spawned via `delegate_task` can be configured to use a local Ollama model:

```bash
# Set delegation model
hermes config set delegation.model qwen3-coder:30b

# Set delegation provider
hermes config set delegation.provider ollama-launch
```

Resulting config section:
```yaml
delegation:
  model: qwen3-coder:30b
  provider: ollama-launch
  reasoning_effort: low
  ...
```

## ⚠️ Critical: Sub-agent Transport

**Sub-agents inherit the parent's provider/transport by default.** The `delegation.model` and `delegation.provider` settings in config are NOT used for the sub-agent's own reasoning loop — sub-agents always use the same model as the parent session.

This applies regardless of `delegation.api_mode`, `base_url`, or any other delegation config setting. The delegation config values are used for other delegation modes (ACP transport), not the default Hermes sub-agent transport.

```yaml
# These settings do NOT change what model the sub-agent thinks with:
delegation:
  model: qwen3-coder:30b         # ❌ Ignored for sub-agent reasoning
  provider: ollama-launch        # ❌ Ignored for sub-agent reasoning
  api_mode: chat_completions     # ❌ No effect on sub-agent model
  base_url: http://localhost:... # ❌ No effect on sub-agent model
```

### Workaround: Spawn Independent Agent Processes

To use a DIFFERENT model for sub-tasks, spawn a new Hermes process via terminal instead of `delegate_task`:

```bash
# One-shot sub-task with different model
terminal(command='hermes chat -q "do this task" --provider ollama-launch --model qwen2.5-coder:14b', timeout=300)

# Background long-running task
terminal(command='hermes chat -q "build feature X" --provider opencode-go --model deepseek-v4-pro', background=true, notify_on_complete=true)
```

### base_url in Delegation Config

The `base_url` setting is relevant only for specific delegation modes (not the default parent-inheritance mode):

```bash
# Ollama local — can be set but won't affect sub-agent reasoning
hermes config set delegation.base_url http://127.0.0.1:11434/v1

# Cloud provider — clear base_url
hermes config set delegation.base_url ''
```

### Metadata Quirk: delegate_task Results Always Show Parent Model

Even when the sub-agent is correctly using a different model/provider via delegation config, the `delegate_task` result metadata **always reports the parent session's model** (`model: big-pickle` in the parent's report). This does NOT mean the child used the parent's model — it's a bookkeeping limitation. To verify which model the child *actually* used:

1. Check GPU/Ollama activity during the task (nvidia-smi, ollama ps)
2. Have the sub-agent self-report: `curl` to the Ollama API and capture `total_duration`, or inspect its own provider config

### Session Restart Required

Configuration changes to `delegation.*` (model, provider, api_mode, base_url) may **require a new session** (`/reset` or restart `hermes`) before they take effect on newly spawned sub-agents. Without a restart, sub-agents may continue using the parent's transport regardless of delegation config changes.

**Signs your delegation model is NOT being used:**
- `nvidia-smi` shows 0 MiB VRAM during sub-agent activity
- `ollama ps` shows no model loaded while delegate_task is running
- Delegation tasks complete quickly but GPU remains idle

**To verify:** Run a direct Ollama API call and check GPU, then compare:
```bash
# Direct Ollama test (should show GPU activity)
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"<model>","prompt":"hello","stream":false}'
nvidia-smi      # ← check VRAM usage
ollama ps       # ← check loaded model

# Then test delegate_task with same model
delegate_task(goal="run nvidia-smi")
# If VRAM is 0 MiB while direct curl shows >0, delegation model is NOT being used
```

## Starting Ollama

```bash
# Start Ollama server in background
terminal(command="ollama serve", background=true)
sleep 3
ollama list    # verify running
```

**Troubleshooting:** If port 11434 is already in use, check existing Ollama process:
```bash
netstat -ano | grep 11434
tasklist //FI "PID eq <PID>"
```

## Checking Available Ollama Models

```bash
ollama list
```

## Checking GPU Utilization During Inference

```bash
# Real-time GPU memory/usage
nvidia-smi

# What model is currently loaded in Ollama
ollama ps

# Model loaded on GPU? Look for:
# - nvidia-smi: llama-server.exe process with MiB used
# - ollama ps: shows "XX%/YY% CPU/GPU" ratio
```

## Testing Sub-agent GPU Usage

```python
delegate_task(
    goal="Run nvidia-smi and check GPU memory usage",
    context="Test whether this sub-agent uses GPU for its own LLM inference",
    toolsets=["terminal"]
)
```

Compare the VRAM reported by the sub-agent vs VRAM when you make a direct Ollama API call. If they match (idle VRAM), the sub-agent is using the parent's provider, not Ollama.

## Pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Expecting delegation config to change sub-agent model | Sub-agent uses parent model regardless of delegation.model/provider/api_mode | This is expected behavior — delegate_task always inherits parent transport. Use `terminal("hermes chat -q --provider X --model Y")` to spawn independent agents with different models |
| Ollama not running | curl to 11434 hangs or connection refused | `ollama serve` (background) |
| Model not pulled | ollama list shows nothing | `ollama pull <model>` |
| 18GB model on 8GB VRAM | Low GPU layer ratio (87/13%), slower inference | Use a smaller model or higher quant |
| Port conflict | "bind: address already in use" | Kill existing Ollama or use different port |
| Metadata shows parent model | `delegate_task` result says `model: big-pickle` even when child uses parent model | Sub-agent task results always report parent model in metadata |
| `base_url` still set after switching to cloud provider | Irrelevant for default transport mode | Can clear but doesn't affect sub-agent model choice |

## Verification

After setup:
1. `curl http://127.0.0.1:11434/api/tags` → returns model list
2. `curl http://127.0.0.1:11434/api/generate -d '{"model":"<model>","prompt":"hi","stream":false}'` → returns response
3. `nvidia-smi` → VRAM usage > 0 with llama-server process
4. `ollama ps` → model loaded with GPU/cpu ratio
5. To use a DIFFERENT model for a sub-task: `terminal("hermes chat -q 'task' --provider X --model Y")`

> ⚠️ Note: `delegate_task` sub-agents always use the parent session's model, regardless of delegation config settings. See the `Critical: Sub-agent Transport` section above.
