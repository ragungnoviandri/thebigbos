"""Session management — parent-child session trees with subagent support."""

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.provider import Message, ToolResult


@dataclass
class Session:
    """A single conversation session."""
    id: str
    title: str = ""
    parent_id: str | None = None
    messages: list[Message] = field(default_factory=list)
    summary: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    is_subagent: bool = False
    subagent_name: str = ""

    def add_message(self, msg: Message) -> None:
        self.messages.append(msg)
        self.updated_at = time.time()

    def to_llm_format(self) -> list[Message]:
        """Return messages in provider-agnostic format, filtering out reasoning/thinking."""
        return [m for m in self.messages if m.role != "reasoning"]


class SessionManager:
    """Manages sessions, including parent-child subagent relationships."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions: dict[str, Session] = {}
        self.active_session_id: str | None = None
        self._subagent_sessions: dict[str, list[str]] = {}  # parent_id -> [child_ids]

    def create_session(self, parent_id: str | None = None,
                       is_subagent: bool = False,
                       subagent_name: str = "",
                       session_id: str | None = None) -> Session:
        """Create a new session, optionally as child of another.

        If session_id is provided, use it directly (for restoring persisted sessions).
        Otherwise a random ID is generated.
        """
        if session_id is None:
            session_id = str(uuid.uuid4())[:8]
        session = Session(
            id=session_id,
            parent_id=parent_id,
            is_subagent=is_subagent,
            subagent_name=subagent_name,
        )
        self.sessions[session_id] = session
        self.active_session_id = session_id

        if parent_id:
            if parent_id not in self._subagent_sessions:
                self._subagent_sessions[parent_id] = []
            self._subagent_sessions[parent_id].append(session_id)

        return session

    def register_session(self, session_id: str,
                         parent_id: str | None = None,
                         is_subagent: bool = False,
                         subagent_name: str = "") -> Session:
        """Register a session with an explicit ID (e.g., from DB).
        If a session with this ID already exists in memory, return it
        and make it active. Otherwise create a new one.
        """
        if session_id in self.sessions:
            self.active_session_id = session_id
            return self.sessions[session_id]
        return self.create_session(
            parent_id=parent_id,
            is_subagent=is_subagent,
            subagent_name=subagent_name,
            session_id=session_id,
        )

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    @property
    def active(self) -> Session | None:
        if self.active_session_id:
            return self.sessions.get(self.active_session_id)
        return None

    def switch_session(self, session_id: str) -> Session | None:
        """Switch active session."""
        if session := self.sessions.get(session_id):
            self.active_session_id = session_id
        return session

    def get_subagent_sessions(self, parent_id: str) -> list[Session]:
        """Get all child subagent sessions."""
        child_ids = self._subagent_sessions.get(parent_id, [])
        return [self.sessions[cid] for cid in child_ids if cid in self.sessions]

    def get_session_tree(self) -> dict[str, Any]:
        """Build a tree of all sessions."""
        roots = [s for s in self.sessions.values() if s.parent_id is None]
        return {
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title or f"Session {s.id}",
                    "children": self._build_tree(s.id),
                    "message_count": len(s.messages),
                    "is_active": s.id == self.active_session_id,
                }
                for s in roots
            ]
        }

    def _build_tree(self, parent_id: str) -> list[dict[str, Any]]:
        children = self._subagent_sessions.get(parent_id, [])
        return [
            {
                "id": cid,
                "title": self.sessions[cid].title or f"Subagent {cid}",
                "children": self._build_tree(cid),
                "message_count": len(self.sessions[cid].messages),
            }
            for cid in children if cid in self.sessions
        ]

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions with basic info."""
        return [
            {
                "id": s.id,
                "title": s.title or "Untitled",
                "parent_id": s.parent_id,
                "message_count": len(s.messages),
                "is_active": s.id == self.active_session_id,
                "created_at": s.created_at,
            }
            for s in self.sessions.values()
        ]
