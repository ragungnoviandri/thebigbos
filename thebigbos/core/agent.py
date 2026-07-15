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

        # Set tool registry mode from config
        self.tools.mode = self.config.mode

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

        # Don't auto-load last session — user picks or starts fresh
        # _load_previous_session() is kept for manual use but not called here

        # Auto-load last session if configured
        if self.config.memory.auto_load_session:
            sessions = self.memory.list_sessions(limit=1)
            if sessions:
                self.continue_session(sessions[0]["id"])

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
                    self.memory.set_session_source(session_id, "opencode")
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
                    self.memory.set_session_source(session_id, "hermes")
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
            read_only=True,
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
            # Register with correct ID — no more mismatched dict keys
            session = self.sessions.register_session(sid)
            session.title = sessions[0].get("title", "")
            session.summary = sessions[0].get("summary", "")
            # Load just last 5 messages for context preview
            msgs = self.memory.load_messages(sid, limit=5)
            for m in msgs:
                session.messages.append(self._db_to_message(m))
            # Sanitize incomplete tool-call sequences from abrupt kills
            session.messages = self._sanitize_messages(session.messages)

    def start_session(self) -> Session:
        """Start a new conversation session."""
        session = self.sessions.create_session()
        self.memory.create_session(session.id)
        self.state = AgentState()
        self._emit("session_started", json.dumps({"id": session.id}))
        return session

    def continue_session(self, session_id: str) -> Session | None:
        """Continue a session — load from TheBigBos DB or external source on demand.

        Resume mode (from memory.resume_mode config):
          - "clean": user + assistant messages only, tool outputs skipped
          - "full": all messages including tool outputs

        Uses register_session() so the session ID is properly tracked.
        """
        # If already loaded in memory, just switch
        if session_id in self.sessions.sessions:
            self.sessions.switch_session(session_id)
            self._emit("session_loaded", json.dumps({"id": session_id, "messages": len(self.sessions.active.messages) if self.sessions.active else 0}))
            return self.sessions.active

        # Register session with correct ID (no more mismatched dict keys!)
        session = self.sessions.register_session(session_id)

        # Try loading from TheBigBos DB first
        msgs = self.memory.load_messages(session_id, limit=10000)
        if msgs:
            load_limit = self.config.memory.session_load_limit
            resume_mode = self.config.memory.resume_mode

            if resume_mode == "clean":
                # Smart resume: user + assistant only, skip tool outputs
                filtered = [m for m in msgs if m["role"] in ("user", "assistant")]
                skipped = len(msgs) - len(filtered)
            else:
                # "full" — load everything
                filtered = msgs
                skipped = 0

            total = len(filtered)
            if total > load_limit:
                recent = filtered[-load_limit:]
            else:
                recent = filtered

            # Build messages
            for m in recent:
                session.messages.append(self._db_to_message(m))

            # Sanitize incomplete tool-call sequences from DB corruption
            session.messages = self._sanitize_messages(session.messages)

            # Prepend session summary as system context
            summary = self.memory.get_session_summary(session_id)
            parts = []
            if summary:
                parts.append(f"[Session summary] {summary}")
            if total > load_limit:
                parts.append(f"[Showing last {load_limit} of {total} messages]")
            if skipped > 0:
                parts.append(f"[{skipped} tool/reasoning messages hidden — use /resume full to load all]")
            if parts:
                session.messages.insert(0, Message(role="system", content="\n".join(parts)))

            # Store metadata
            session.metadata["_total_db_messages"] = len(msgs)
            session.metadata["_skipped_tool_msgs"] = skipped
            session.metadata["_loaded_count"] = len(recent)
            session.metadata["_resume_mode"] = resume_mode

        elif session_id.startswith("opencode-"):
            self._load_opencode_session(session_id, session)
        elif session_id.startswith("hermes-"):
            self._load_hermes_session(session_id, session)

        # Set title from sessions table
        sessions_list = self.memory.list_sessions(limit=200)
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
                # Sanitize in case external DB has corrupted sequences
                session.messages = self._sanitize_messages(session.messages)
                break

    def _load_hermes_session(self, session_id: str, session: Session) -> None:
        """Load messages from Hermes DB for a session."""
        import sqlite3
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

    def _db_to_message(self, m: dict) -> Message:
        """Convert a DB message dict to a proper Message with ToolCall objects."""
        tool_calls = []
        for tc in m.get("tool_calls", []):
            if isinstance(tc, dict):
                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("name", tc.get("function", {}).get("name", "")),
                    arguments=tc.get("arguments", tc.get("function", {}).get("arguments", {})),
                ))
            else:
                tool_calls.append(tc)
        return Message(
            role=m["role"],
            content=m["content"],
            tool_calls=tool_calls,
            tool_call_id=m.get("tool_call_id"),
            name=m.get("name"),
        )

    def _sanitize_messages(self, messages: list[Message]) -> list[Message]:
        """Strip incomplete tool-call sequences caused by abrupt process kills.

        If an assistant message has tool_calls but no matching tool result follows
        (process was killed mid-execution), remove the dangling assistant message
        and any orphaned tool results to keep the API happy.
        """
        if not messages:
            return messages

        # Remove leading tool/orphan messages (no preceding assistant context)
        while messages and messages[0].role == "tool":
            messages.pop(0)

        cleaned: list[Message] = []
        pending_tool_ids: set[str] = set()

        for msg in messages:
            if msg.role == "assistant" and msg.tool_calls:
                # Track expected tool result IDs
                for tc in msg.tool_calls:
                    pending_tool_ids.add(tc.id)
                cleaned.append(msg)
            elif msg.role == "tool" and msg.tool_call_id:
                if msg.tool_call_id in pending_tool_ids:
                    pending_tool_ids.discard(msg.tool_call_id)
                    cleaned.append(msg)
                # else: orphaned tool result — skip it
            else:
                # user, system, or assistant without tool_calls
                # If there are pending tool calls when we hit a non-tool message,
                # the sequence was interrupted — strip the dangling assistant message
                if pending_tool_ids:
                    # Find and remove the last assistant message with pending tool_calls
                    for i in range(len(cleaned) - 1, -1, -1):
                        if cleaned[i].role == "assistant" and cleaned[i].tool_calls:
                            tc_ids = {tc.id for tc in cleaned[i].tool_calls}
                            if tc_ids & pending_tool_ids:
                                cleaned.pop(i)
                                break
                    pending_tool_ids.clear()
                cleaned.append(msg)

        # If messages end with pending tool calls (abrupt end), strip the last assistant
        if pending_tool_ids:
            for i in range(len(cleaned) - 1, -1, -1):
                if cleaned[i].role == "assistant" and cleaned[i].tool_calls:
                    tc_ids = {tc.id for tc in cleaned[i].tool_calls}
                    if tc_ids & pending_tool_ids:
                        cleaned.pop(i)
                        break

        return cleaned

    # ——— Main agent loop ———

    async def chat(self, user_input: str, stream: bool = True, fresh: bool = False) -> str:
        """Process a user message through the agent loop. Returns final response text."""
        if not self.sessions.active or fresh:
            self.start_session()

        session = self.sessions.active
        self.state.step_count = 0
        self.state.messages = []

        # Always refresh system prompt so mode changes take effect
        system_prompt = self._build_system_prompt()
        if session.messages and session.messages[0].role == "system":
            session.messages[0] = Message(role="system", content=system_prompt)
        else:
            session.messages.insert(0, Message(role="system", content=system_prompt))

        # Inject relevant memories
        try:
            relevant = self.memory.recall(user_input, k=self.config.memory.vector_search_k)
            if relevant:
                mem_text = "\n".join(f"- {m.summary or m.content[:200]}" for m in relevant[:3])
                session.add_message(Message(role="system", content=f"Relevant context from past:\n{mem_text}"))
        except Exception:
            pass  # Memory recall is optional

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
            reasoning_effort="medium" if self.config.active_model.startswith(("o1", "o3", "o4")) else None,
            thinking_budget=self.config.reasoning_budget if any(
                r in self.config.active_model.lower() for r in ("deepseek", "sonnet", "opus", "claude")
            ) else None,
            max_tokens=4096,
        )

        # Main loop
        final_response = ""
        max_steps = self.config.max_tool_steps

        while self.state.step_count < max_steps:
            self.state.step_count += 1

            # Re-emit thinking before each model call (after tool execution)
            if self.state.step_count > 1:
                self._emit("thinking", "")

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

            # Emit & save reasoning first (if any)
            if response.reasoning_content:
                self._emit("reasoning", response.reasoning_content)
                if self.config.memory.save_reasoning:
                    self.memory.save_message(session.id, "reasoning", response.reasoning_content)

            # Don't save error responses as history — they poison resumed sessions
            if response.content and response.finish_reason != "error":
                self.memory.save_message(session.id, "assistant", response.content,
                                         tool_calls=[{"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                                                     for tc in response.tool_calls] if response.tool_calls else None)
                self._emit("response", response.content)
                final_response += response.content
            elif response.content and response.finish_reason == "error":
                self._emit("response", response.content)
                final_response += response.content

            # If no tool calls, we're done
            if not response.tool_calls:
                break

            # Execute tools
            tools_this_step = [{"name": tc.name, "args": tc.arguments} for tc in response.tool_calls]
            self._emit("tool_executing", json.dumps(tools_this_step))

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

            # Emit summary after all tools in this step finish
            self._emit("tool_done", json.dumps(tools_this_step))

        # All steps done — signal completion
        self._emit("done", "")

        # Update session title if first exchange
        if self.state.step_count > 0 and not session.title:
            session.title = user_input[:50] + ("..." if len(user_input) > 50 else "")
            self.memory.update_session_title(session.id, session.title)

        # Auto-summarize and store medium-term memory
        await self._auto_summarize(session)

        self.soul.adjust_mood(0.02)
        return final_response.strip() or "(thinking...)"

    async def stream_chat(self, user_input: str) -> AsyncIterator[str]:
        """Streamed chat — yields reasoning first (dim/italic), then content in sentence chunks."""
        if not self.sessions.active:
            self.start_session()

        session = self.sessions.active
        self.state.step_count = 0

        self._emit("thinking", "")

        # Always refresh system prompt so mode changes take effect
        system_prompt = self._build_system_prompt()
        if session.messages and session.messages[0].role == "system":
            session.messages[0] = Message(role="system", content=system_prompt)
        else:
            session.messages.insert(0, Message(role="system", content=system_prompt))

        session.add_message(Message(role="user", content=user_input))
        self.memory.save_message(session.id, "user", user_input)

        provider = self.providers.active
        if not provider:
            yield "Error: No active provider configured."
            return

        self._emit("api_info", json.dumps({
            "provider": self.config.active_provider or provider.name,
            "model": self.config.active_model,
            "endpoint": getattr(provider, "base_url", "unknown"),
        }))

        tool_schemas = self.tools.get_schemas()
        options = ModelOptions(
            model=self.config.active_model,
            reasoning_effort="medium" if self.config.active_model.startswith(("o1", "o3", "o4")) else None,
            thinking_budget=self.config.reasoning_budget if any(
                r in self.config.active_model.lower() for r in ("deepseek", "sonnet", "opus", "claude")
            ) else None,
            max_tokens=4096,
        )

        max_steps = self.config.max_tool_steps
        while self.state.step_count < max_steps:
            self.state.step_count += 1

            # Re-emit thinking before each model call (e.g., after tools execute)
            if self.state.step_count > 1:
                self._emit("thinking", "")

            try:
                response = await provider.chat(session.to_llm_format(), tool_schemas, options)
            except Exception as e:
                self._emit("api_error", str(e)[:200])
                yield f"\n[Error: {e}]"
                break

            # Check for API-level errors in the response
            if response.finish_reason == "error":
                self._emit("api_error", response.content[:200])
                yield response.content
                break  # Don't save error responses — they poison resumed sessions

            # —— Reasoning first (emitted via event, NOT yielded to avoid double render) ——
            if response.reasoning_content:
                self._emit("reasoning", response.reasoning_content[:800])
                if self.config.memory.save_reasoning:
                    self.memory.save_message(session.id, "reasoning", response.reasoning_content)

            # —— Content ——
            if response.content:
                self.memory.save_message(session.id, "assistant", response.content,
                                         tool_calls=[{"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                                                     for tc in response.tool_calls] if response.tool_calls else None)
                yield response.content
            elif not response.reasoning_content:
                # Neither content nor reasoning — model might have returned empty
                yield "\n[yellow](empty response from model)[/yellow]\n"

            assistant_msg = Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            session.add_message(assistant_msg)

            if not response.tool_calls:
                break

            for tc in response.tool_calls:
                self._emit("tool_executing", json.dumps([{"name": tc.name, "args": tc.arguments}]))
                result = await self.tools.execute(tc.name, tc.arguments)
                self.memory.save_message(session.id, "tool", result, tool_call_id=tc.id, name=tc.name)
                session.add_message(Message(role="tool", content=result, tool_call_id=tc.id, name=tc.name))

            # Emit summary after tools finish
            tools_summary = [{"name": tc.name, "args": tc.arguments} for tc in response.tool_calls]
            self._emit("tool_done", json.dumps(tools_summary))

        # All steps done — signal completion
        self._emit("done", "")

        if not session.title:
            session.title = user_input[:50] + ("..." if len(user_input) > 50 else "")
            self.memory.update_session_title(session.id, session.title)

        # Auto-summarize and store medium-term memory
        await self._auto_summarize(session)

    # ——— System prompt ———

    def _build_system_prompt(self) -> str:
        """Build the complete system prompt."""
        facts = self.memory.get_all_facts()
        facts_text = ""
        if facts:
            facts_text = "\n".join(f"- {k}: {v}" for k, v in facts.items())

        skills_prompt = self.skills.get_skill_prompt()

        # Mode-dependent tool constraints
        if self.config.mode == "plan":
            mode_rule = (
                "\n\n## Mode: PLAN (read-only)\n"
                "You are in **plan mode**. You can read files, search code, browse the web, "
                "and have conversations. You can suggest changes and provide code examples, "
                "but you **cannot write, edit, or execute** any files.\n"
                "- Use `read`, `glob`, `grep`, `webfetch` freely\n"
                "- You can suggest edits but the user must apply them manually\n"
                "- Be helpful, conversational, and thorough in your analysis"
            )
        else:
            mode_rule = (
                "\n\n## Mode: BUILD (read/write)\n"
                "You are in **build mode**. Full access — read, write, edit, execute, "
                "and delegate to subagents. Build with swagger."
            )

        tools_prompt = "\n\n## Available Tools\n"
        for t in self.tools.get_all():
            tools_prompt += f"\n- **{t.name}**: {t.description}"

        subagent_prompt = ""
        if self.config.agents:
            subagent_prompt = "\n\n## Subagents\nYou can delegate complex tasks to subagents by calling the `task` tool.\n"
            for name, agent_cfg in self.config.agents.items():
                subagent_prompt += f"\n- **{name}**: {agent_cfg.description}"

        extra = f"{skills_prompt}\n{mode_rule}\n{tools_prompt}\n{subagent_prompt}"
        return self.soul.build_system_prompt(extra_context=extra, facts=facts_text)

    # ——— Context compaction ———

    def _should_compact(self) -> bool:
        """Check if context needs compaction based on actual model context window."""
        session = self.sessions.active
        if not session:
            return False
        provider = self.providers.active
        if not provider:
            return False

        token_count = provider.count_tokens(session.to_llm_format())
        context_window = provider.get_context_window(self.config.active_model)
        threshold = int(context_window * self.config.memory.compaction_threshold)

        # Also trigger if messages exceed max_short_term
        if len(session.messages) > self.config.memory.max_short_term:
            return True

        return token_count > threshold

    async def _compact_context(self) -> None:
        """Compact the conversation context: keep system prompt + last N, summarize rest."""
        session = self.sessions.active
        if not session or len(session.messages) < 10:
            return

        provider = self.providers.active
        if not provider:
            return

        # Preserve system message(s) at the top
        system_msgs = [m for m in session.messages if m.role == "system"]
        non_system = [m for m in session.messages if m.role != "system"]

        keep = min(10, len(non_system) - 4)  # keep last 4-10 non-system messages
        to_summarize = non_system[:-keep]
        recent = non_system[-keep:]

        if not to_summarize:
            return

        # Build summary input — include user, assistant, reasoning, and compact tools
        summary_parts = []
        for m in to_summarize:
            if m.role == "user":
                summary_parts.append(f"[user]: {m.content[:200]}")
            elif m.role == "assistant":
                summary_parts.append(f"[assistant]: {m.content[:300]}")
            elif m.role == "reasoning":
                summary_parts.append(f"[think]: {m.content[:200]}")
            elif m.role == "tool":
                # Compact tool: just show name + first 80 chars
                tool_name = getattr(m, 'name', '') or 'tool'
                summary_parts.append(f"[tool]: {tool_name}({m.content[:80]})")
        summary_input = "\n".join(summary_parts)

        summary_msg = Message(
            role="user",
            content=f"Summarize this conversation concisely, preserving key decisions, code changes, and learnings:\n\n{summary_input}",
        )

        # Use a lightweight model for summarization; fallback to active model
        summary_model = self.config.active_model
        try:
            response = await provider.chat(
                [summary_msg], [],
                ModelOptions(model=summary_model, max_tokens=800),
            )
            self.state.compacted_summary = response.content
            self.state.is_compacted = True

            # Rebuild: system + summary + recent
            compacted = Message(
                role="system",
                content=f"[Context compacted — {len(to_summarize)} messages summarized]\n\n{response.content}",
            )
            session.messages = system_msgs + [compacted] + recent

            self.memory.save_session_summary(session.id, response.content)
            self._emit("compacted", f"Compacted {len(to_summarize)} → {len(response.content.split())} words")
        except Exception as e:
            self._emit("compacted", f"Compaction failed: {e}")

    async def _auto_summarize(self, session: Session) -> None:
        """Auto-generate medium-term summary for the session."""
        if len(session.messages) < 6:
            return
        provider = self.providers.active
        if not provider:
            return

        try:
            convo_parts = []
            for m in session.messages[-30:]:
                if m.role == "user":
                    convo_parts.append(f"[user]: {m.content[:200]}")
                elif m.role == "assistant" and not m.tool_calls:
                    convo_parts.append(f"[assistant]: {m.content[:250]}")
                elif m.role == "reasoning" and self.config.memory.save_reasoning:
                    convo_parts.append(f"[think]: {m.content[:150]}")
                elif m.role == "tool":
                    tool_name = getattr(m, 'name', '') or 'tool'
                    convo_parts.append(f"[tool]: {tool_name}({m.content[:60]})")
            convo = "\n".join(convo_parts)
            response = await provider.chat(
                [Message(
                    role="user",
                    content=f"Summarize this conversation in 2-3 sentences:\n\n{convo}",
                )],
                [],
                ModelOptions(model=self.config.active_model, max_tokens=200),
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
            model=agent_cfg.model or self.config.active_model,
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

    # ——— Skill learning ———

    async def learn_skill(self, topic: str, context: str = "", tags: str = "") -> str:
        """Auto-generate & persist a SKILL.md from conversation context.

        Uses the LLM to extract knowledge from recent messages and format it
        as a proper skill file with frontmatter.
        """
        session = self.sessions.active
        if not session or not session.messages:
            return "No conversation context to learn from."

        provider = self.providers.active
        if not provider:
            return "No active provider. Set one in config."

        # Gather context — the last conversation is our "lesson"
        if context:
            lesson_text = context
        else:
            recent = [m for m in session.messages[-12:] if m.role in ("user", "assistant") and m.content]
            lesson_text = "\n\n".join(
                f"**{m.role}**: {m.content[:1000]}" for m in recent
            )

        tag_hint = f"\nUse these tags in metadata: [{tags}]." if tags else ""

        prompt = f"""You just had this conversation:

{lesson_text}

Based on what was discussed, create a SKILL.md file that captures the knowledge
the AI assistant demonstrated or learned. The skill should be reusable — when
loaded later, it should help the assistant perform a similar task.

Extract the core technique, workflow, or knowledge into a well-structured
Markdown skill file with YAML frontmatter.

---FORMAT---
---
name: {topic}
description: "Brief one-line summary"
version: 1.0.0
author: TheBigBos
license: MIT
metadata:
  tags: [tag1, tag2]
---

# Title

Detailed step-by-step instructions. Include:
- When to use this skill
- Prerequisites
- Step-by-step process
- Common pitfalls or tips
- Example usage
---END---

{tag_hint}

Return ONLY the skill content, no extra commentary."""

        try:
            response = await provider.chat(
                [Message(role="user", content=prompt)], [],
                ModelOptions(model=self.config.active_model, max_tokens=4000),
            )
            raw = response.content.strip()

            # Strip any markdown code fences the LLM might wrap
            if raw.startswith("```"):
                # Find first newline after opening fence
                fence_end = raw.find("\n")
                raw = raw[fence_end + 1:]
                if raw.endswith("```"):
                    raw = raw[:-3]

            raw = raw.strip()

            # Parse frontmatter to extract name & description
            import re
            name = topic
            description = ""
            fm_match = re.match(r'^---\s*\n(.*?)\n---', raw, re.DOTALL)
            if fm_match:
                for line in fm_match.group(1).split("\n"):
                    if ":" in line:
                        k, _, v = line.partition(":")
                        k = k.strip().lower()
                        v = v.strip().strip('"').strip("'")
                        if k == "name":
                            name = v
                        elif k == "description":
                            description = v

            # Extract tags from frontmatter metadata
            tag_list = None
            if fm_match:
                tag_match = re.search(r'tags:\s*\[(.+?)\]', fm_match.group(1))
                if tag_match:
                    tag_list = [t.strip().strip('"').strip("'") for t in tag_match.group(1).split(",")]

            skill = self.skills.create_skill(name, description, raw, author="TheBigBos", tags=tag_list)
            if skill:
                self._emit("skill_learned", json.dumps({"name": skill.name, "description": skill.description}))
                return f"[green]Skill '{skill.name}' saved![/green]\n  📁 {skill.path}\n  📝 {skill.description}"
            else:
                return "[yellow]Failed to save skill.[/yellow]"

        except Exception as e:
            return f"[red]Error generating skill: {e}[/red]"

    async def suggest_skill(self) -> str | None:
        """Analyze conversation — if there's a teachable moment, return a skill suggestion.
        Returns None if nothing worth learning.
        """
        session = self.sessions.active
        if not session or len(session.messages) < 6:
            return None

        provider = self.providers.active
        if not provider:
            return None

        recent = [m for m in session.messages[-10:] if m.role in ("user", "assistant") and m.content]
        lesson_text = "\n\n".join(f"**{m.role}**: {m.content[:500]}" for m in recent)

        prompt = f"""Analyze this conversation and decide if there's reusable knowledge worth
capturing as a skill. A "skill" is a repeatable workflow or knowledge domain.

{lesson_text}

Return JSON with the schema:
{{"should_learn": true/false, "topic": "suggested-skill-name", "description": "one-liner", "tags": ["tag1"]}}

If nothing new was taught/demonstrated, return {{"should_learn": false}}.
Only return the JSON, no other text."""

        try:
            response = await provider.chat(
                [Message(role="user", content=prompt)], [],
                ModelOptions(model=self.config.active_model, max_tokens=300),
            )
            import re
            result = response.content.strip()
            # Extract JSON block if wrapped
            json_match = re.search(r'\{.+\}', result, re.DOTALL)
            if json_match:
                result = json_match.group(0)

            data = json.loads(result)
            if data.get("should_learn"):
                topic = data.get("topic", "untitled-skill")
                desc = data.get("description", "")
                tags = ", ".join(data.get("tags", []))
                return f"💡 I learned something about **{topic}**!\n   {desc}\n   Type `/learn {topic}` to save it as a reusable skill."
        except Exception:
            pass

        return None

    def shutdown(self) -> None:
        """Clean shutdown."""
        self.memory.close()
