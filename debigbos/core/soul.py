"""Soul/Personality system — gives de BigBos its character.

The soul defines:
  - Persona: The character/personality
  - Tone: Communication style
  - Constraints: Behavioral rules
  - Greeting: First impression
  - Emotional range: (future) mood/stress simulation
"""

from dataclasses import dataclass, field
from typing import Optional

from ..config.manager import SoulConfig


@dataclass
class Soul:
    """The personality engine of de BigBos."""

    config: SoulConfig

    # Runtime state
    mood: float = 0.5  # 0.0 = negative, 1.0 = positive
    energy: float = 1.0  # 0.0 = tired, 1.0 = fresh
    session_count: int = 0
    user_name: Optional[str] = None
    learned_preferences: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def persona(self) -> str:
        return self.config.persona

    def build_system_prompt(self, extra_context: str = "", facts: str = "") -> str:
        """Build the full system prompt including soul, facts, and context."""
        parts = [f"You are {self.config.name}. {self.config.persona}"]

        if self.config.tone:
            parts.append(f"Tone: {self.config.tone}")

        if self.config.constraints:
            parts.append("Rules:")
            for c in self.config.constraints:
                parts.append(f"  - {c}")

        if self.user_name:
            parts.append(f"You are talking to {self.user_name}.")

        if self.learned_preferences:
            prefs = "\n".join(f"  - {k}: {v}" for k, v in self.learned_preferences.items())
            parts.append(f"User preferences:\n{prefs}")

        if facts:
            parts.append(f"Relevant facts:\n{facts}")

        if self.config.custom_prompt:
            parts.append(self.config.custom_prompt)

        if extra_context:
            parts.append(extra_context)

        parts.append("""
Be direct and concise. Use GitHub-flavored markdown for formatting.
When helping with code, prioritize correctness and security.
Only ask clarifying questions when genuinely uncertain — otherwise act.
""".strip())

        return "\n\n".join(parts)

    def learn_preference(self, key: str, value: str) -> None:
        """Learn a user preference."""
        self.learned_preferences[key] = value

    def adjust_mood(self, delta: float) -> None:
        """Adjust mood based on interaction quality."""
        self.mood = max(0.0, min(1.0, self.mood + delta))

    def get_greeting(self) -> str:
        """Get the configured greeting."""
        return self.config.greeting

    def personalize_greeting(self) -> str:
        """Return a personalized greeting based on state."""
        if self.user_name:
            if self.session_count == 0:
                return f"Yo {self.user_name}! {self.config.name} here. First time? Let's build something awesome."
            else:
                return f"Welcome back, {self.user_name}! What are we working on today?"
        return self.config.greeting

    def welcome_banner(self, model: str = "", provider: str = "", workspace: str = "",
                       skills: int = 0, tools: int = 0, sessions: int = 0) -> str:
        """Return a rich welcome banner for new sessions / startup — OpenCode-style."""
        lines = [
            f"",
            f"[bold #fab283]  de BigBos[/bold #fab283]  [dim]AI Assistant with Soul[/dim]",
            f"",
        ]

        items = []
        if provider and model:
            items.append(f"[#fab283]{provider}[/#fab283]/[#5c9cf5]{model}[/#5c9cf5]")
        if workspace:
            items.append(f"[dim]{workspace}[/dim]")
        items.append(f"[yellow]{skills} skills[/yellow]")
        items.append(f"[magenta]{tools} tools[/magenta]")
        if sessions:
            items.append(f"[blue]{sessions} sessions[/blue]")

        lines.append(f"  {'  │  '.join(items)}")
        lines.append(f"")
        lines.append(f"  [dim]Just start typing  •  /help for commands  •  Ctrl+Q to quit[/dim]")
        lines.append(f"")

        return "\n".join(lines)


