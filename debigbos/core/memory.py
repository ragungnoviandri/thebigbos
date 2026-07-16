"""Persistent memory system — short-term, medium-term, long-term with embeddings.

Architecture:
  - Short-term: In-context messages (last N messages)
  - Medium-term: Session summaries stored in SQLite
  - Long-term: Vector embeddings in ChromaDB for semantic retrieval
"""

import json
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: str = ""
    session_id: str = ""
    content: str = ""
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    importance: float = 1.0
    timestamp: float = field(default_factory=time.time)
    embedding: list[float] | None = None


class MemoryManager:
    """Manages multi-layered persistent memory."""

    def __init__(self, workspace: Path, embedding_model: str = "all-MiniLM-L6-v2"):
        self.workspace = workspace
        self.db_path = workspace / ".debigbos" / "memory.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self._embedder = None
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._init_db()
        return self._conn

    def _init_db(self) -> None:
        """Initialize SQLite tables."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                created_at REAL,
                updated_at REAL,
                parent_id TEXT,
                source TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_calls TEXT DEFAULT '[]',
                tool_call_id TEXT,
                name TEXT,
                reasoning_content TEXT DEFAULT '',
                timestamp REAL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                content TEXT NOT NULL,
                summary TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                importance REAL DEFAULT 1.0,
                timestamp REAL,
                embedding BLOB
            );
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                value TEXT,
                source TEXT,
                created_at REAL
            );
        """)
        # Migration: add reasoning_content column for existing databases
        try:
            self.conn.execute("ALTER TABLE messages ADD COLUMN reasoning_content TEXT DEFAULT ''")
        except Exception:
            pass  # Column already exists
        self.conn.commit()

    def _get_embedder(self):
        """Lazy-load sentence-transformers for embeddings."""
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer(self.embedding_model)
            except ImportError:
                self._embedder = False
        return self._embedder if self._embedder is not False else None

    def embed_text(self, text: str) -> list[float] | None:
        """Generate embedding for text."""
        if embedder := self._get_embedder():
            embedding = embedder.encode(text, show_progress_bar=False)
            return embedding.tolist()
        return None

    # ——— Session management ———

    def create_session(self, session_id: str, parent_id: str | None = None) -> None:
        """Create a new session."""
        now = time.time()
        self.conn.execute(
            "INSERT OR IGNORE INTO sessions (id, created_at, updated_at, parent_id) VALUES (?, ?, ?, ?)",
            (session_id, now, now, parent_id),
        )
        self.conn.commit()

    def update_session_title(self, session_id: str, title: str) -> None:
        """Set session title."""
        self.conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), session_id),
        )
        self.conn.commit()

    def set_session_source(self, session_id: str, source: str) -> None:
        """Set the source label for a session (e.g., 'opencode', 'hermes')."""
        self.conn.execute(
            "UPDATE sessions SET source = ?, updated_at = ? WHERE id = ?",
            (source, time.time(), session_id),
        )
        self.conn.commit()

    def save_session_cost(self, session_id: str, cost: float) -> None:
        """Persist accumulated cost for a session."""
        self.conn.execute(
            "UPDATE sessions SET cost = ?, updated_at = ? WHERE id = ?",
            (cost, time.time(), session_id),
        )
        self.conn.commit()

    def get_session_cost(self, session_id: str) -> float:
        """Get accumulated cost for a session. Returns 0.0 if not found."""
        row = self.conn.execute(
            "SELECT cost FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return float(row[0]) if row else 0.0

    def save_message(self, session_id: str, role: str, content: str,
                     tool_calls: list | None = None, tool_call_id: str | None = None,
                     name: str | None = None, reasoning_content: str | None = None) -> None:
        """Save a single message to the session."""
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, name, reasoning_content, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(tool_calls or []),
             tool_call_id, name, reasoning_content or "", time.time()),
        )
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (time.time(), session_id),
        )
        self.conn.commit()

    def load_messages(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Load messages for a session."""
        rows = self.conn.execute(
            "SELECT role, content, tool_calls, tool_call_id, name, reasoning_content FROM messages "
            "WHERE session_id = ? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            {
                "role": row[0],
                "content": row[1],
                "tool_calls": json.loads(row[2]),
                "tool_call_id": row[3],
                "name": row[4],
                "reasoning_content": row[5] or "",
            }
            for row in rows
        ]

    def resync_messages(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """Wipe and rewrite all messages for a session (used by /fix to persist sanitized state)."""
        self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        now = time.time()
        for m in messages:
            self.conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, name, reasoning_content, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, m["role"], m["content"], json.dumps(m.get("tool_calls") or []),
                 m.get("tool_call_id"), m.get("name"), m.get("reasoning_content", ""), now),
            )
        self.conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
        self.conn.commit()

    def get_session_summary(self, session_id: str) -> str:
        """Get the session summary."""
        row = self.conn.execute(
            "SELECT summary FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else ""

    def save_session_summary(self, session_id: str, summary: str) -> None:
        """Save a session summary (medium-term memory)."""
        self.conn.execute(
            "UPDATE sessions SET summary = ?, updated_at = ? WHERE id = ?",
            (summary, time.time(), session_id),
        )
        self.conn.commit()

    def list_sessions(self, limit: int = 200) -> list[dict[str, Any]]:
        """List recent sessions."""
        rows = self.conn.execute(
            "SELECT id, title, summary, created_at, updated_at, source FROM sessions "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {"id": r[0], "title": r[1], "summary": r[2], "created_at": r[3], "updated_at": r[4], "source": r[5]}
            for r in rows
        ]

    # ——— Long-term memory (with embeddings) ———

    def remember(self, entry: MemoryEntry) -> None:
        """Store a long-term memory with embedding."""
        embedding_blob = None
        if entry.embedding:
            embedding_blob = b"".join(struct.pack("f", v) for v in entry.embedding)
        else:
            if emb := self.embed_text(entry.content):
                embedding_blob = b"".join(struct.pack("f", v) for v in emb)

        self.conn.execute(
            "INSERT OR REPLACE INTO memories (id, session_id, content, summary, tags, importance, timestamp, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.id, entry.session_id, entry.content, entry.summary,
             json.dumps(entry.tags), entry.importance, entry.timestamp, embedding_blob),
        )
        self.conn.commit()

    def recall(self, query: str, k: int = 5) -> list[MemoryEntry]:
        """Semantic search over long-term memories."""
        query_embedding = self.embed_text(query)
        if not query_embedding:
            return self._keyword_recall(query, k)

        rows = self.conn.execute(
            "SELECT id, session_id, content, summary, tags, importance, timestamp, embedding "
            "FROM memories WHERE embedding IS NOT NULL"
        ).fetchall()

        scored = []
        for row in rows:
            emb_blob = row[7]
            if emb_blob and len(emb_blob) % 4 == 0:
                n = len(emb_blob) // 4
                emb = list(struct.unpack(f"{n}f", emb_blob))
                similarity = self._cosine_sim(query_embedding, emb)
                scored.append((similarity, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._row_to_entry(row) for _, row in scored[:k]]

    def _keyword_recall(self, query: str, k: int) -> list[MemoryEntry]:
        """Fallback keyword-based recall."""
        words = query.lower().split()
        like_clauses = " OR ".join(["content LIKE ?" for _ in words])
        params = [f"%{w}%" for w in words]
        rows = self.conn.execute(
            f"SELECT id, session_id, content, summary, tags, importance, timestamp, embedding "
            f"FROM memories WHERE {like_clauses} ORDER BY importance DESC, timestamp DESC LIMIT ?",
            params + [k],
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def _row_to_entry(self, row) -> MemoryEntry:
        return MemoryEntry(
            id=row[0], session_id=row[1] or "", content=row[2], summary=row[3] or "",
            tags=json.loads(row[4]) if row[4] else [],
            importance=row[5] or 1.0, timestamp=row[6] or time.time(),
        )

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        return dot / (norm_a * norm_b + 1e-9)

    # ——— Facts (key-value persistent knowledge) ———

    def set_fact(self, key: str, value: str, source: str = "user") -> None:
        """Store a persistent fact."""
        self.conn.execute(
            "INSERT OR REPLACE INTO facts (key, value, source, created_at) VALUES (?, ?, ?, ?)",
            (key, value, source, time.time()),
        )
        self.conn.commit()

    def get_fact(self, key: str) -> str | None:
        """Retrieve a fact by key."""
        row = self.conn.execute("SELECT value FROM facts WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def get_all_facts(self) -> dict[str, str]:
        """Get all stored facts."""
        rows = self.conn.execute("SELECT key, value FROM facts").fetchall()
        return {r[0]: r[1] for r in rows}

    def delete_session(self, session_id: str) -> None:
        """Delete a session and its messages."""
        self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
