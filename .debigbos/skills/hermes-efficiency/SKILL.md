---
name: hermes-efficiency
description: "Optimize Hermes Agent context consumption, reasoning effort, and performance settings"
tags: [configuration, context, performance, optimization]
author: agent
version: 1.0.0
---

# Hermes Efficiency

When the user asks to reduce context consumption ("cari setingan km supaya g makan konteks banyak"), optimize reasoning effort ("klo ngatur resoning... jangan panjang2"), or complains about verbose/wasteful responses, use this guide.

## Reasoning Effort (Most Impactful)

Controls how deeply the model thinks before responding — directly affects token usage per turn.

```bash
# Set via config (takes effect on /reset or new session)
hermes config set agent.reasoning_effort low
hermes config set agent.reasoning_effort medium   # default
hermes config set agent.reasoning_effort high

# In-session (takes effect immediately)
# Type in chat:  /reasoning low
```

**Available levels:** `none` | `minimal` | `low` | `medium` | `high` | `xhigh`

**Critical trade-off:** Lower effort = fewer reasoning tokens = less context consumed. But complex tasks suffer:
- `low` — OK for simple Q&A, chat, quick verifications
- `medium` — balanced everyday use
- `high`/`xhigh` — complex debugging, multi-step planning, critical code

**When the user switches tasks, recommend they switch effort level too.** E.g., "Naikin ke `medium`/`high` dulu pas debugging, turunin lagi ke `low` untuk obrolan ringan."

## Dynamic Reasoning Effort Per Task Type

User (Big) confirmed this proven strategy:

| Task Type | Reasoning Effort | Rationale |
|-----------|-----------------|-----------|
| Writing code (features, boilerplate) | `low` | Fast, irit konteks, cukup ikut pola yang ada |
| Simple Q&A / chat | `low` | No deep analysis needed |
| Everyday work | `medium` | Balanced default |
| Debugging / root cause analysis | `medium` or `high` | Butuh reasoning chain, trace error, koneksi antar file |
| Code review / security audit | `medium` or `high` | Need thorough analysis |
| Multi-step planning | `high` | Complex orchestration |

**Switch mid-session:** Just type `/reasoning low` or `/reasoning medium` in chat. Takes effect immediately.

**Key insight:** `low` works for ~80% of work. Only bump up for the 20% that needs deep analysis. The most impactful single context-saving technique.

## What Does NOT Save Context

| Setting | Misconception | Reality |
|---------|--------------|---------|
| `display.show_reasoning: false` | "gak kelihatan berarti gak makan konteks" | Visual-only — reasoning tokens still in history, still eat context |
| `/reasoning hide` | Same as above | Likely strips reasoning from display only; check implementation |

## ⚠️ Key Terminology: Threshold vs Target Ratio

Users often confuse these two. Clarify immediately:

| Term | What it means | User says | Actual setting |
|------|--------------|-----------|----------------|
| **threshold** | WHEN compression starts (as % of context window) | "auto compress di 80%" | `compression.threshold: 0.8` → compress when 80% full |
| **target_ratio** | HOW MUCH is kept AFTER compression | "kompres sampai sisa 20%" | `compression.target_ratio: 0.2` → keep 20% |

**Signal: user says "naikin auto compress jadi 80%"** — ask clarifying: *"Maksud lo threshold (kapan mulai kompres) atau target_ratio (seberapa kecil setelah kompres)?"* 99% of the time they mean **threshold** (kapan mulai).

## Context Compression Settings

Auto-compresses conversation history when nearing the token limit:

| Config key | Default | Aggressive (more irit) | Relaxed (more context) | Notes |
|-----------|---------|----------------------|----------------------|-------|
| `compression.threshold` | `0.5` | `0.35` | `0.7-0.8` | When to START compressing. Lower = compress earlier (more irit, less data loss per compression event). Higher = compress later (more context before compression, but bigger single loss). |
| `compression.target_ratio` | `0.2` | `0.1` | `0.3` | How much to KEEP after compression. Lower = more aggressive removal. Higher = retains more context. |
| `compression.protect_first_n` | `3` | `10` | `3` | Number of initial messages ALWAYS kept intact during compression. Raise to protect project goals, instructions, system context. |
| `compression.protect_last_n` | `20` | `30` | `20` | Number of most recent messages ALWAYS kept intact. Raise to protect ongoing discussion during compression events. |

**Trade-off to explain to the user:**
- **Lower threshold (0.35)**: Compression kicks in earlier, so your context stays less full. But you lose older context more frequently.
- **Higher threshold (0.7-0.8)**: Compression waits longer, so you retain more recent conversation before it triggers. Better for session-switchers who want maximum context before losing anything.
- **User's preferred setup (Agung/Rutan Jakarta)**: `threshold: 0.7`, `target_ratio: 0.1`, `protect_first_n: 10`, `protect_last_n: 30`. This means: compress only at 70% full, but when it does, keep only 10% of the middle (while protecting 10 first and 30 last messages). Good balance.
- **`target_ratio` of 0.2 is already aggressive**: Going to 0.3 means you keep 50% more history after compression, but your session fills up faster and compresses more often.
- **`protect_first_n` + `protect_last_n` don't change when compression triggers**: They only ensure critical context (goals, recent chat) survives the compression event. Raising them makes compression safer.

## Memory Hygiene

Memory is injected into EVERY turn — keep it compact:

```
memory.memory_char_limit: 2200    # current limit (adequate)
memory.user_char_limit: 1375     # current limit (adequate)
```

Don't save task progress, commit SHAs, PR numbers, or temporary state to memory — those bloat context across sessions. Save only: user preferences, environment facts, durable conventions.

## Tool Output Limits

```
tool_output.max_bytes: 50000      # cap at 50KB
tool_output.max_lines: 2000       # max lines per tool result
tool_output.max_line_length: 2000 # truncate long lines
```

## In-Session Habits (Agent Behavior)

- Keep responses concise and focused (this is already a system instruction)
- Don't repeat info the user already knows
- Don't dump full file contents unless asked
- Use `todo` for multi-step tracking instead of repeating the plan in every message

## When Changes Take Effect

| Setting Category | Needs `/reset`? | Notes |
|-----------------|----------------|-------|
| `compression.*` (threshold, target_ratio, protect_*) | **No** ✅ | Takes effect **immediately** — next compression event uses new values. No restart needed. |
| `agent.reasoning_effort` | **Yes** ❌ | Config file change needs `/reset`. But `/reasoning low` chat command works instantly. |
| `delegation.*` | **Yes** ❌ | Must restart session or open new one. |
| `provider` / `model` | **Yes** ❌ | Full session restart required. |
| Memory (user/memory) | **No** ✅ | Injected every turn instantly. |

**User's (Agung) preferred compression setup:**
```
threshold: 0.7       # compress at 70% full
target_ratio: 0.1    # keep only 10% when compressing
protect_first_n: 10  # protect first 10 messages (goals, instructions)
protect_last_n: 30   # protect last 30 messages (current discussion)
```

Rationale: Lo wants maximum context before compression triggers (70%), but when it does, compress aggressively (10%) to free up space — while protecting the critical bookends (goals + recent chat).
