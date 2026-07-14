"""BigBos Agent — main conversation loop with tool use, subagents, and memory.

Architecture:
  1. System prompt = soul + skills list + relevant memories + learned facts
  2. User message → inject relevant long-term memories
  3. Model responds (text or tool_calls)
  4. Execute tools → feed results back → repeat (max steps)
  5. Context compaction when approaching token limit
  6. Subagent spawn: pause parent, run child with isolated context
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from ..config.manager import Config, ConfigManager
from ..models.provider import Message, ModelOptions, ModelResponse, ToolCall, ToolResult
from ..models.registry import ProviderRegistry
from .memory import MemoryManager
from .session import Session, SessionManager
from .skills import SkillManager
from .soul import Soul
from ..tools.registry import ToolRegistry


@dataclass
class AgentState:
    """Runtime state for the agent loop."""
    messages: list[Message] = field(default_factory=list)
    step_count: int = 0
    accumulated_cost: float = 0.0
    is_compacted: bool = False
    compacted_summary: str = ""


class BigBosAgent:
    """The main agent that orchestrates conversation, tools, and subagents."""

    def __init__(self, workspace: Path | None = None):
        self.workspace = workspace or Path.cwd()
        self.config_manager = ConfigManager(self.workspace)
        self.config: Config = self.config_manager.load()

        # Core components
        self.soul = Soul(self.config.soul)
        self.memory = MemoryManager(self.workspace, self.config.memory.embedding_model)
        self.skills = SkillManager(self.workspace, self.config.skills.paths)
        self.sessions = SessionManager(self.workspace)
        self.providers = ProviderRegistry(self.config)
        self.tools = ToolRegistry(self.workspace)

        # Runtime state
        self.state = AgentState()
        self._callback: Callable[[str, str], None] | None = None  # (type, data) for TUI
        self._running = False
        self._tool_approval_queue: asyncio.Queue = asyncio.Queue()

    async def initialize(self) -> None:
        """Async initialization — load providers, register tools, scan skills."""
        await self.providers.initialize()

        # Register built-in tools
        from ..tools.bash_tool import BashTool
        from ..tools.file_tools import ReadTool, WriteTool, EditTool, GlobTool, GrepTool
        from ..tools.web_tool import WebFetchTool
        from ..tools.todo_tool import TodoTool

        self.tools.register(ReadTool.definition(self.workspace))
        self.tools.register(WriteTool.definition(self.workspace))
        self.tools.register(EditTool.definition(self.workspace))
        self.tools.register(GlobTool.definition(self.workspace))
        self.tools.register(GrepTool.definition(self.workspace))
        self.tools.register(WebFetchTool.definition(self.workspace))
        self.tools.register(TodoTool.definition(self.workspace))
        self.tools.register(BashTool.definition(self.workspace))

        # Register skill tool (loads SKILL.md on demand)
        self.tools.register(self._skill_tool_definition())

        # Load custom .bigbos/tools/*.json
        self.tools.load_custom_tools()

        # Scan skills
        self.skills.scan()

        # Don't auto-import on startup — load on demand when user opens /sessions
        self._sessions_imported = False

        # Load previous session if exists
        self._load_previous_session()

    def _ensure_sessions_imported(self) -> None:
        """Lazy-load external sessions only when needed."""
        if not self._sessions_imported:
            self._auto_import_sessions()
            self._sessions_imported = True

    def _auto_import_sessions(self) -> None:
        """Quick-import session metadata only (no messages). Messages load on-demand via /switch."""
        import json
        import sqlite3

        project_path = str(self.workspace.resolve()).replace("\\", "/")
        imported = 0

        # Try OpenCode import — metadata only, no messages
        oc_paths = [
            Path.home() / ".local" / "share" / "opencode" / "opencode.db",
            Path.home() / "AppData" / "Local" / "opencode" / "opencode.db",
            Path.home() / "Library" / "Application Support" / "opencode" / "opencode.db",
        ]
        for oc_path in oc_paths:
            if not oc_path.exists():
                continue
            try:
                src = sqlite3.connect(str(oc_path))
                rows = src.execute(
                    "SELECT s.id, s.title, s.time_created, s.summary_diffs, s.tokens_input, s.tokens_output "
                    "FROM session s JOIN project p ON s.project_id = p.id "
                    "WHERE p.worktree = ? OR p.worktree LIKE ?",
                    (project_path, project_path + "/%"),
                ).fetchall()
                src.close()

                for sid, title, created_at, summary, tin, tout in rows:
                    session_id = f"opencode-{sid[:12]}"
                    exists = self.memory.conn.execute(
                        "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    if exists:
                        continue
                    self.memory.create_session(session_id)
                    self.memory.update_session_title(session_id, title or "Untitled")
                    if summary:
                        self.memory.save_session_summary(session_id, summary[:500])
                    imported += 1
            except Exception:
                pass

        # Try Hermes — metadata only
        hermes_paths = [
            Path.home() / "AppData" / "Local" / "hermes" / "state.db",
            Path.home() / ".local" / "share" / "hermes" / "state.db",
            Path.home() / "Library" / "Application Support" / "hermes" / "state.db",
        ]
        for hermes_path in hermes_paths:
            if not hermes_path.exists():
                continue
            try:
                src = sqlite3.connect(str(hermes_path))
                bp = str(self.workspace.resolve())
                fp = bp.replace("\\", "/")
                rows = src.execute(
                    "SELECT id, title, started_at FROM sessions WHERE cwd = ? OR cwd = ?",
                    (bp, fp),
                ).fetchall()
                for sid, title, started_at in rows:
                    session_id = f"hermes-{sid[:12]}"
                    exists = self.memory.conn.execute(
                        "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    if exists:
                        continue
                    self.memory.create_session(session_id)
                    self.memory.update_session_title(session_id, title or "Untitled")
                    imported += 1
                src.close()
            except Exception:
                pass

        if imported:
            self._emit("auto_import", json.dumps({"count": imported, "project": project_path}))

    def _skill_tool_definition(self):
        from ..tools.registry import ToolDefinition

        async def _load_skill(name: str) -> str:
            skill = self.skills.get(name)
            if not skill:
                return f"Skill '{name}' not found. Available: {[s['name'] for s in self.skills.list_skills()]}"
            return skill.truncate_for_prompt()

        return ToolDefinition(
            name="skill",
            description="Load a skill's full instructions on demand. Use when a task matches a skill description.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the skill to load",
                    },
                },
                "required": ["name"],
            },
            handler=_load_skill,
        )

    def on_event(self, callback: Callable[[str, str], None]) -> None:
        """Set callback for TUI events: callback(event_type, data)."""
        self._callback = callback

    def _emit(self, event_type: str, data: str = "") -> None:
        """Emit an event to the TUI."""
        if self._callback:
            self._callback(event_type, data)

    # ——— Session management ———

    def _load_previous_session(self) -> None:
        """Pre-load the most recent session metadata (messages loaded on demand)."""
        sessions = self.memory.list_sessions(limit=1)
        if sessions:
            sid = sessions[0]["id"]
            # Don't load all messages — session picker will handle it
            session = self.sessions.create_session()
            session.id = sid
            session.title = sessions[0].get("title", "")
            session.summary = sessions[0].get("summary", "")
            # Load just last 5 messages for context preview
            msgs = self.memory.load_messages(sid, limit=5)
            for m in msgs:
                session.messages.append(Message(**m))

    def start_session(self) -> Session:
        """Start a new conversation session."""
        session = self.sessions.create_session()
        self.memory.create_session(session.id)
        self.state = AgentState()
        self._emit("session_started", json.dumps({"id": session.id}))
        return session

    def continue_session(self, session_id: str) -> Session | None:
        """Continue a session — load from TheBigBos DB or external source on demand."""
        session = self.sessions.create_session()
        session.id = session_id

        # Try loading from TheBigBos DB first
        msgs = self.memory.load_messages(session_id, limit=100)
        if msgs:
            recent = msgs[-30:]  # Last 30 messages
            for m in recent:
                session.messages.append(Message(**m))
            if len(msgs) > 30:
                summary = self.memory.get_session_summary(session_id)
                if summary:
                    session.messages.insert(0, Message(
                        role="system",
                        content=f"[Previous summary: {summary}]\n[Showing last {len(recent)} of {len(msgs)} messages]"
                    ))
        elif session_id.startswith("opencode-"):
            # Load from OpenCode DB on demand
            self._load_opencode_session(session_id, session)
        elif session_id.startswith("hermes-"):
            # Load from Hermes DB on demand
            self._load_hermes_session(session_id, session)

        # Set title from sessions table
        sessions_list = self.memory.list_sessions(limit=100)
        for s in sessions_list:
            if s["id"] == session_id:
                session.title = s.get("title", "")
                session.summary = s.get("summary", "")
                break

        self._emit("session_loaded", json.dumps({"id": session_id, "messages": len(session.messages)}))
        return session

    def _load_opencode_session(self, session_id: str, session: Session) -> None:
        """Load messages from OpenCode DB for a session."""
        import json, sqlite3
        real_id = session_id.replace("opencode-", "")
        for oc_path in [
            Path.home() / ".local" / "share" / "opencode" / "opencode.db",
            Path.home() / "AppData" / "Local" / "opencode" / "opencode.db",
        ]:
            if not oc_path.exists():
                continue
            src = sqlite3.connect(str(oc_path))
            rows = src.execute("SELECT id FROM session WHERE id LIKE ?", (f"%{real_id}%",)).fetchall()
            if not rows:
                src.close()
                continue
            sid = rows[0][0]
            msgs = src.execute(
                "SELECT m.data, m.id FROM message m WHERE m.session_id = ? ORDER BY m.time_created",
                (sid,),
            ).fetchall()
            loaded = 0
            for msg_data, msg_id in msgs[-30:]:  # Last 30 only
                try:
                    mdata = json.loads(msg_data) if isinstance(msg_data, str) else msg_data
                    role = mdata.get("role", "user")
                    parts = src.execute(
                        "SELECT p.data FROM part p WHERE p.message_id = ? ORDER BY p.time_created",
                        (msg_id,),
                    ).fetchall()
                    parts_text = []
                    for (part_data,) in parts:
                        pdata = json.loads(part_data) if isinstance(part_data, str) else part_data
                        if pdata.get("type") == "text":
                            parts_text.append(pdata.get("text", ""))
                    content = "\n".join(p for p in parts_text if p)
                    if content.strip():
                        session.messages.append(Message(role=role, content=content[:5000]))
                        loaded += 1
                except Exception:
                    continue
            src.close()
            if loaded:
                break

    def _load_hermes_session(self, session_id: str, session: Session) -> None:
        """Load messages from Hermes DB for a session."""
        real_id = session_id.replace("hermes-", "")
        for hp in [
            Path.home() / "AppData" / "Local" / "hermes" / "state.db",
            Path.home() / ".local" / "share" / "hermes" / "state.db",
        ]:
            if not hp.exists():
                continue
            src = sqlite3.connect(str(hp))
            rows = src.execute("SELECT id FROM sessions WHERE id LIKE ?", (f"%{real_id}%",)).fetchall()
            if not rows:
                src.close()
                continue
            sid = rows[0][0]
            msgs = src.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT 30",
                (sid,),
            ).fetchall()
            for role, content in reversed(msgs):
                if content and content.strip():
                    session.messages.append(Message(role=role, content=content[:5000]))
            src.close()
            break

    def switch_session(self, session_id: str) -> Session | None:
        """Switch to a different session."""
        return self.sessions.switch_session(session_id)

    # ——— Main agent loop ———

    async def chat(self, user_input: str, stream: bool = True, fresh: bool = False) -> str:
        """Process a user message through the agent loop. Returns final response text."""
        if not self.sessions.active or fresh:
            self.start_session()

        session = self.sessions.active
        self.state.step_count = 0
        self.state.messages = []

        # Build system prompt
        system_prompt = self._build_system_prompt()
        session.add_message(Message(role="system", content=system_prompt))

        # Inject relevant memories
        relevant = self.memory.recall(user_input, k=self.config.memory.vector_search_k)
        if relevant:
            mem_text = "\n".join(f"- {m.summary or m.content[:200]}" for m in relevant)
            session.add_message(Message(role="system", content=f"Relevant context from past:\n{mem_text}"))

        # Add user message
        user_msg = Message(role="user", content=user_input)
        session.add_message(user_msg)
        self.memory.save_message(session.id, "user", user_input)

        self._emit("thinking", "")

        # Get provider and tool schemas
        provider = self.providers.active
        if not provider:
            return "Error: No active provider configured. Set active_provider in thebigbos.json."

        tool_schemas = self.tools.get_schemas()
        options = ModelOptions(
            model=self.config.active_model,
            reasoning_effort="medium" if self.config.active_model.startswith(("o1", "o3")) else None,
            thinking_budget=self.config.reasoning_budget,
            max_tokens=4096,
        )

        # Main loop
        final_response = ""
        max_steps = self.config.max_tool_steps

        while self.state.step_count < max_steps:
            self.state.step_count += 1

            # Check for context compaction
            if self._should_compact():
                await self._compact_context()

            # Call model
            messages_for_llm = session.to_llm_format()
            try:
                response = await provider.chat(messages_for_llm, tool_schemas, options)
            except Exception as e:
                error_msg = f"Error calling {self.config.active_provider}/{self.config.active_model}: {e}"
                final_response = error_msg
                self._emit("response", error_msg)
                break

            # Save assistant message
            assistant_msg = Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            session.add_message(assistant_msg)

            # Emit reasoning first (if any)
            if response.reasoning_content:
                self._emit("reasoning", response.reasoning_content)

            if response.content:
                self.memory.save_message(session.id, "assistant", response.content)
                self._emit("response", response.content)
                final_response += response.content

            # If no tool calls, we're done
            if not response.tool_calls:
                break

            # Execute tools
            self._emit("tool_executing", json.dumps([
                {"name": tc.name, "args": tc.arguments} for tc in response.tool_calls
            ]))

            for tc in response.tool_calls:
                result = await self.tools.execute(tc.name, tc.arguments)
                self.memory.save_message(
                    session.id, "tool", result,
                    tool_call_id=tc.id, name=tc.name,
                )
                session.add_message(Message(
                    role="tool",
                    content=result,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))
                self._emit("tool_result", json.dumps({"name": tc.name, "result": result[:500]}))

        # Update session title if first exchange
        if self.state.step_count > 0 and not session.title:
            session.title = user_input[:50] + ("..." if len(user_input) > 50 else "")
            self.memory.update_session_title(session.id, session.title)

        # Auto-summarize and store medium-term memory
        await self._auto_summarize(session)

        self.soul.adjust_mood(0.02)
        return final_response.strip() or "(thinking...)"

    async def stream_chat(self, user_input: str) -> AsyncIterator[str]:
        """Streamed version of chat — always starts fresh session for headless."""
        if not self.sessions.active:
            self.start_session()

        session = self.sessions.active
        self.state.step_count = 0

        system_prompt = self._build_system_prompt()
        session.add_message(Message(role="system", content=system_prompt))

        session.add_message(Message(role="user", content=user_input))
        self.memory.save_message(session.id, "user", user_input)

        provider = self.providers.active
        if not provider:
            yield "Error: No active provider configured."
            return

        tool_schemas = self.tools.get_schemas()
        options = ModelOptions(
            model=self.config.active_model,
            reasoning_effort="medium" if self.config.active_model.startswith(("o1", "o3")) else None,
            thinking_budget=self.config.reasoning_budget,
            max_tokens=4096,
        )

        max_steps = self.config.max_tool_steps
        while self.state.step_count < max_steps:
            self.state.step_count += 1

            try:
                response = await provider.chat(session.to_llm_format(), tool_schemas, options)
            except Exception as e:
                yield f"\n[Error: {e}]"
                break

            if response.reasoning_content:
                yield f"\n[dim]thinking: {response.reasoning_content[:300]}...[/dim]\n"

            if response.content:
                yield response.content
                self.memory.save_message(session.id, "assistant", response.content)

            assistant_msg = Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            session.add_message(assistant_msg)

            if not response.tool_calls:
                break

            for tc in response.tool_calls:
                yield f"\n[dim]Tool: {tc.name}...[/dim]\n"
                result = await self.tools.execute(tc.name, tc.arguments)
                self.memory.save_message(session.id, "tool", result, tool_call_id=tc.id, name=tc.name)
                session.add_message(Message(role="tool", content=result, tool_call_id=tc.id, name=tc.name))

        if not session.title:
            session.title = user_input[:50] + ("..." if len(user_input) > 50 else "")
            self.memory.update_session_title(session.id, session.title)

    # ——— System prompt ———

    def _build_system_prompt(self) -> str:
        """Build the complete system prompt."""
        facts = self.memory.get_all_facts()
        facts_text = ""
        if facts:
            facts_text = "\n".join(f"- {k}: {v}" for k, v in facts.items())

        skills_prompt = self.skills.get_skill_prompt()

        tools_prompt = "\n\n## Available Tools\n"
        for t in self.tools.get_all():
            tools_prompt += f"\n- **{t.name}**: {t.description}"

        subagent_prompt = ""
        if self.config.agents:
            subagent_prompt = "\n\n## Subagents\nYou can delegate complex tasks to subagents by calling the `task` tool.\n"
            for name, agent_cfg in self.config.agents.items():
                subagent_prompt += f"\n- **{name}**: {agent_cfg.description}"

        extra = f"{skills_prompt}\n{tools_prompt}\n{subagent_prompt}"
        return self.soul.build_system_prompt(extra_context=extra, facts=facts_text)

    # ——— Context compaction ———

    def _should_compact(self) -> bool:
        """Check if context needs compaction."""
        session = self.sessions.active
        if not session:
            return False
        provider = self.providers.active
        if not provider:
            return False
        token_count = provider.count_tokens(session.to_llm_format())
        # Rough threshold: 80% of context window
        return token_count > 100000 * self.config.memory.compaction_threshold

    async def _compact_context(self) -> None:
        """Compact the conversation context by summarizing older messages."""
        session = self.sessions.active
        if not session or len(session.messages) < 10:
            return

        provider = self.providers.active
        if not provider:
            return

        # Keep last 10 messages, summarize the rest
        to_summarize = session.messages[:-10]
        recent = session.messages[-10:]

        summary_input = "\n".join(
            f"[{m.role}]: {m.content[:300]}" for m in to_summarize
        )

        summary_msg = Message(
            role="user",
            content=f"Summarize this conversation concisely:\n\n{summary_input}",
        )

        try:
            response = await provider.chat(
                [summary_msg], [],
                ModelOptions(model=self.config.small_model, max_tokens=500),
            )
            self.state.compacted_summary = response.content
            self.state.is_compacted = True

            # Replace old messages with summary
            compacted = Message(
                role="system",
                content=f"[Context compacted]\nPrevious conversation summary:\n{response.content}",
            )
            session.messages = [compacted] + recent

            self.memory.save_session_summary(session.id, response.content)
            self._emit("compacted", "")
        except Exception:
            pass

    async def _auto_summarize(self, session: Session) -> None:
        """Auto-generate medium-term summary for the session."""
        if len(session.messages) < 6:
            return
        provider = self.providers.active
        if not provider:
            return

        try:
            convo = "\n".join(
                f"[{m.role}]: {m.content[:200]}" for m in session.messages[-20:]
            )
            response = await provider.chat(
                [Message(
                    role="user",
                    content=f"Summarize this conversation in 2-3 sentences:\n\n{convo}",
                )],
                [],
                ModelOptions(model=self.config.small_model, max_tokens=200),
            )
            session.summary = response.content
            self.memory.save_session_summary(session.id, response.content)
        except Exception:
            pass

    # ——— Subagent delegation ———

    async def spawn_subagent(self, agent_name: str, task: str) -> dict[str, Any]:
        """Spawn a subagent to handle a task in an isolated session.

        The subagent runs with its own session, system prompt, and tool set.
        Returns the subagent's final result.
        """
        agent_cfg = self.config_manager.get_agent_config(agent_name)
        if not agent_cfg:
            return {"error": f"Subagent '{agent_name}' not configured"}

        # Create child session
        parent = self.sessions.active
        child = self.sessions.create_session(
            parent_id=parent.id if parent else None,
            is_subagent=True,
            subagent_name=agent_name,
        )

        # Build subagent system prompt
        system = agent_cfg.system_prompt or f"You are a {agent_name} subagent. {agent_cfg.description}"
        system += "\n\nComplete the assigned task and return your findings."
        system += "\nBe thorough but concise. Use tools as needed."

        child.add_message(Message(role="system", content=system))
        child.add_message(Message(role="user", content=task))
        self.memory.create_session(child.id, parent_id=parent.id if parent else None)

        provider = self.providers.active
        if not provider:
            return {"error": "No active provider"}

        # Filter tools based on agent config
        allowed_tools = agent_cfg.tools or self.tools.get_tool_names()
        tool_schemas = [
            s for s in self.tools.get_schemas()
            if s["function"]["name"] in allowed_tools
        ]

        options = ModelOptions(
            model=agent_cfg.model or self.config.small_model,
            max_tokens=2048,
        )

        result_text = ""
        for step in range(agent_cfg.max_steps):
            response = await provider.chat(child.to_llm_format(), tool_schemas, options)

            if response.content:
                result_text += response.content + "\n"

            child.add_message(Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            ))

            if not response.tool_calls:
                break

            for tc in response.tool_calls:
                tool_result = await self.tools.execute(tc.name, tc.arguments)
                child.add_message(Message(
                    role="tool",
                    content=tool_result,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

        self._emit("subagent_complete", json.dumps({"agent": agent_name, "steps": step + 1}))
        return {
            "agent": agent_name,
            "session_id": child.id,
            "result": result_text.strip(),
            "steps": step + 1,
            "messages": len(child.messages),
        }

    # ——— Memory & facts ———

    async def remember_fact(self, key: str, value: str) -> None:
        """Store a key-value fact persistently."""
        self.memory.set_fact(key, value, "user")

    async def recall_facts(self, query: str) -> list[dict[str, str]]:
        """Search long-term memories."""
        entries = self.memory.recall(query)
        return [
            {"content": e.content, "summary": e.summary, "importance": e.importance}
            for e in entries
        ]

    def shutdown(self) -> None:
        """Clean shutdown."""
        self.memory.close()
