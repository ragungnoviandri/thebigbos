# de BigBos

AI-powered CLI assistant with persistent memory, soul, skills, git integration, and multi-model support. Built with Python + Textual TUI — inspired by OpenCode.

```
   ____        ____  _       ____
  |  _ \  ___ | __ )(_) __ _| __ )  ___  ___
  | | | |/ _ \|  _ \| |/ _` |  _ \ / _ \/ __|
  | |_| |  __/| |_) | | (_| | |_) | (_) \__ \
  |____/ \___||____/|_|\__, |____/ \___/|___/
                       |___/
```

## Features

| Feature | Description |
|---------|-------------|
| **Multi-Model** | OpenAI, Anthropic, OpenCode (Go + Zen), DeepSeek, OpenRouter, Groq, Together AI, Ollama + Custom |
| **Add Provider** | One-click preset (Anthropic, Groq, DeepSeek, Together, OpenRouter, OpenCode Zen, Ollama) or custom endpoint + API key |
| **Git Integration** | Built-in sidebar: branch, status, diff viewer, commit with custom message dialog |
| **Persistent Memory** | 3-layer: short-term (context), medium-term (SQLite summaries), long-term (embeddings) |
| **Soul/Personality** | Configurable persona, tone, greeting, constraints |
| **Skills System** | Markdown-based SKILL.md lazy-loading |
| **Subagents** | Built-in explore, planner, reviewer agents with isolated context |
| **Session Management** | Persistent sessions, rename, auto-import from OpenCode & Hermes |
| **Textual TUI** | Rich terminal UI with multi-line chat input, sidebar, status bar |
| **Tools** | bash, read, write, edit, glob, grep, webfetch, todowrite + custom tools |
| **Context Compaction** | Auto-summarize when approaching token limit |
| **Reasoning Support** | DeepSeek V4, o1/o3, Claude extended thinking |
| **Cross-Platform** | Windows, Linux, macOS |

## Installation

### One-Liner (Recommended)

```bash
# Windows
powershell -c "irm https://raw.githubusercontent.com/ragungnoviandri/deBigBos/main/install.ps1 | iex"

# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/ragungnoviandri/deBigBos/main/install.sh | bash
```

The installer handles everything: clone repo, setup Python venv, install dependencies, add to PATH.

### For Developers (Editable)

```bash
git clone https://github.com/ragungnoviandri/deBigBos
cd deBigBos
pip install -e .
deBigBos setup
```

Editable mode — code changes take effect immediately. No need to reinstall.

### Quick Start

```bash
# 1. Interactive setup — pick model + API key
deBigBos setup

# 2. Start chatting (TUI mode)
deBigBos

# 3. Or headless
deBigBos run "bikin hello world"
```

## Commands

### CLI Commands

| Command | Description |
|---------|-------------|
| `deBigBos` | Start interactive TUI |
| `deBigBos chat` | Start TUI (explicit) |
| `deBigBos run "query"` | Headless single query |
| `deBigBos setup` | Interactive model + API key setup |
| `deBigBos configure` | View or change config |
| `deBigBos init` | Initialize `.bigbos/` in project |
| `deBigBos install` | Install global config |
| `deBigBos import hermes` | Import sessions from Hermes |
| `deBigBos import opencode` | Import sessions from OpenCode |
| `deBigBos import all --dry-run` | Preview import |
| `deBigBos sessions list` | List all sessions |
| `deBigBos sessions rename <id> <title>` | Rename a session |
| `deBigBos version` | Show version + git info |
| `deBigBos update` | Check for and install updates |
| `deBigBos update --check` | Check for updates only |
| `deBigBos uninstall` | Remove de BigBos (keeps config) |

### TUI Keybindings

| Key | Action |
|-----|--------|
| `Ctrl + S` | Session picker |
| `Ctrl + R` | Rename session |
| `Ctrl + M` | List models |
| `Ctrl + H` | Show help |
| `Ctrl + C` | Copy selected text |
| `Ctrl + Q` | Quit |
| `Esc` | Focus chat input |
| `Enter` | Send message |
| `Shift + Enter` | Newline in chat |

### Chat Commands

| Command | Description |
|---------|-------------|
| `/model <id>` | Switch active model |
| `/provider <name>` | Switch active provider |
| `/agent <name> <task>` | Spawn subagent |
| `/remember key:value` | Store persistent fact |
| `/recall <query>` | Search memories |
| `/learn <name> [tags:t1,t2]` | Save conversation as reusable SKILL.md |
| `/learn-suggest` | Auto-detect teachable moment |
| `/rename <title>` | Rename current session |
| `/clear` | Clear screen |
| `/compact` | Compact long conversation context |
| `/help` | Show help |

## Configuration

Config files are merged from multiple locations (highest to lowest priority):

1. `.bigbos/config.json` — Per-project overrides
2. `deBigBos.json` — Project config
3. `~/.config/deBigBos/config.json` — Global config

### Example `deBigBos.json`

```json
{
  "active_provider": "opencode-go",
  "active_model": "deepseek-v4-pro",
  "providers": {
    "opencode-go": {
      "api_key": "${OPENCODE_GO_API_KEY}",
      "base_url": "https://opencode.ai/zen/go/v1"
    }
  },
  "soul": {
    "name": "de BigBos",
    "persona": "A sharp, witty AI assistant. Direct and concise.",
    "tone": "casual but professional",
    "greeting": "Yo! de BigBos here. What are we building today?"
  },
  "memory": {
    "compaction_threshold": 0.8,
    "vector_search_k": 5
  }
}
```

## Supported Providers

| Provider | Models | Setup |
|----------|--------|-------|
| **OpenCode Go** | deepseek-v4-pro, qwen3.5, kimi-k2, glm5, minimax-m3 | `OPENCODE_GO_API_KEY` ($10/mo) |
| **OpenCode Zen** | deepseek-v4-pro, deepseek-v4-flash, qwen-plus, qwen-max | `OPENCODE_ZEN_API_KEY` |
| **OpenAI** | gpt-4o, o3-mini, o1 | `OPENAI_API_KEY` |
| **Anthropic** | claude-sonnet-4, claude-opus | `ANTHROPIC_API_KEY` |
| **DeepSeek** | deepseek-chat, deepseek-coder | `DEEPSEEK_API_KEY` |
| **OpenRouter** | All models via router | `OPENROUTER_API_KEY` |
| **Groq** | llama-3.1, mixtral, gemma | `GROQ_API_KEY` |
| **Together AI** | Llama 3.1 405B, Mixtral 8x22B | `TOGETHER_API_KEY` |
| **Ollama** | llama3.1, qwen2.5, deepseek-r1 | Local, free |

### Adding a Custom Provider

Click the **"+"** button next to the provider dropdown in the sidebar, or use the preset picker:

1. Pick a preset (Anthropic, Groq, DeepSeek, Together, OpenRouter, OpenCode Zen, Ollama) — fields auto-fill
2. Or choose **Custom...** — enter any OpenAI-compatible endpoint
3. API key can be literal or `${ENV_VAR}` reference
4. Click **"Add Provider"** — immediately available

Any unknown provider falls back to OpenAI-compatible protocol (supports any `/v1/chat/completions` endpoint).

## Skills

Create a `SKILL.md` file in `.bigbos/skills/<name>/`:

```markdown
---
name: my-skill
description: Custom skill for specific tasks
---

# My Skill

When asked about X, follow these steps:
1. Check for Y
2. Verify Z
3. Return findings
```

Load on-demand via `/skills` or `skill` tool in chat.

## Subagents

Built-in subagents:

| Agent | Description | Tools |
|-------|-------------|-------|
| `explore` | Codebase explorer | read, glob, grep, webfetch |
| `planner` | Task planning | read, glob, grep, todowrite |
| `reviewer` | Code review | read, glob, grep |

Usage:
```bash
# From TUI
/agent reviewer review semua file Python

# Or configure your own in deBigBos.json
```

## Directory Layout

```
~/.config/deBigBos/              # Global user config (persists across updates)
├── config.json                   # Model, provider, API keys, soul
├── skills/                       # User SKILL.md files
├── agents/                       # Custom subagent definitions
└── tools/                        # Custom tool JSON

~/.local/share/deBigBos/         # App installation
├── repo/                         # Git repository (pulled from GitHub)
│   └── deBigBos/                # Source code
├── venv/                         # Python virtual environment
├── bin/                          # Wrapper scripts
│   ├── deBigBos                 # Shell wrapper
│   └── deBigBos.bat             # Windows wrapper
└── versions/                     # Version history for rollback

<project>/.bigbos/                # Per-project data
├── memory.db                     # Session history (SQLite)
└── config.json                   # Project-level overrides
```

## Project Structure

```
deBigBos/
├── deBigBos/                  # Main package
│   ├── main.py                 # CLI entry point
│   ├── config/                 # Config & auth
│   │   ├── manager.py          # ConfigLoader (multi-source merge)
│   │   └── auth.py             # API key storage (auth.json)
│   ├── models/                 # Multi-model providers
│   │   ├── registry.py         # ProviderRegistry (runtime registration)
│   │   ├── openai_provider.py  # OpenAI + OpenAI-compatible
│   │   ├── anthropic_provider.py
│   │   ├── opencode_provider.py
│   │   └── ollama_provider.py
│   ├── core/                   # Brain & memory
│   │   ├── agent.py            # Main agent loop + subagent spawning
│   │   ├── soul.py             # Personality engine
│   │   ├── memory.py           # SQLite persistent memory
│   │   ├── skills.py           # SKILL.md loader
│   │   └── session.py          # Session management
│   ├── tools/                  # Built-in tools
│   │   ├── bash_tool.py
│   │   ├── file_tools.py       # read, write, edit, glob, grep
│   │   ├── git_utils.py        # Git status, diff, commit helpers
│   │   ├── web_tool.py
│   │   └── todo_tool.py
│   └── tui/                    # Textual terminal UI
│       ├── app.py              # BigBosApp
│       ├── screens/home.py     # Chat + sidebar + commit dialog + add-provider dialog
│       ├── screens/welcome.py  # Welcome/splash screen
│       ├── dialogs.py          # Modal dialogs
│       ├── theme.py            # Theme management
│       ├── plugin.py           # Plugin system
│       └── keymap.py           # Keybinding registry
├── .debigbos/                    # User config (auto-created)
│   ├── skills/
│   ├── agents/
│   └── tools/
├── install.py                  # Installer
├── deBigBos.json              # Default config
└── pyproject.toml
```

## Architecture

```
User Input
    │
    ▼
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│  Textual     │───▶│  BigBosAgent │───▶│  Provider    │
│  TUI         │    │              │    │  (OpenAI,    │
│  - ChatInput │    │  - System    │    │   Anthropic, │
│    (TextArea)│    │    Prompt    │    │   OpenCode,  │
│  - Sidebar   │    │  - Memory    │    │   Ollama,    │
│    (Git)+    │    │  - Skills    │    │   Custom...) │
│  - StatusBar │    │  - Tools     │    └──────┬───────┘
│  - Tool Log  │    │  - Session   │           │
└──────────────┘    │  - Subagents │    ┌──────▼───────┐
                    └──────┬───────┘    │  Model API   │
                           │            │  Response    │
                    ┌──────▼───────┐    └──────────────┘
                    │  Memory DB   │
                    │  (SQLite)    │
                    │  - Sessions  │
                    │  - Messages  │
                    │  - Facts     │
                    │  - Embeddings│
                    └──────────────┘
```

## Requirements

- Python 3.10+
- API key for at least one provider (or Ollama for local)
- Optional: `sentence-transformers` for long-term memory embeddings

## License

MIT — [github.com/ragungnoviandri/deBigBos](https://github.com/ragungnoviandri/deBigBos)
