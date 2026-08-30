"""
L0 Conversation Buffer - RAM storage for recent conversation messages.
Maintains conversation context without database queries.
"""

from collections import deque
from datetime import datetime, timezone
from typing import List, Dict, Any
import uuid

# Global storage: user_id -> ConversationBuffer
_conversation_buffers = {}


class ConversationBuffer:
    """RAM buffer for recent conversation messages per user."""

    def __init__(self, max_messages: int = 12):
        self.conversation_id = str(uuid.uuid4())[:8]
        self.buffer = deque(maxlen=max_messages)
        self.turn_index = 0
        self.last_activity = datetime.now(timezone.utc)

    def add_message(self, role: str, content: str) -> int:
        """Add message to buffer and return current turn index."""
        self.buffer.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc),
        })
        self.last_activity = datetime.now(timezone.utc)
        return self.turn_index

    def add_turn(self, user_message: str, assistant_response: str) -> int:
        """Add complete turn (user + assistant) and increment turn index."""
        self.add_message("user", user_message)
        self.add_message("assistant", assistant_response)
        self.turn_index += 1
        return self.turn_index

    def get_recent_messages(self, limit: int = 6) -> List[Dict[str, Any]]:
        """Get recent messages for context (without timestamps)."""
        recent = list(self.buffer)[-limit:]
        return [{"role": msg["role"], "content": msg["content"]} for msg in recent]

    def get_recent_turns(self, num_turns: int = 3) -> List[Dict[str, str]]:
        """Get the most recent complete user/assistant turns in chronological order."""
        if num_turns < 1:
            return []
        messages = list(self.buffer)
        turns: List[Dict[str, str]] = []
        i = 0
        while i < len(messages) - 1:
            if messages[i]["role"] == "user" and messages[i + 1]["role"] == "assistant":
                turns.append({
                    "user": messages[i]["content"],
                    "assistant": messages[i + 1]["content"],
                })
                i += 2
            else:
                i += 1
        return turns[-num_turns:]

    def should_create_summary(self) -> bool:
        """Check if we should create a summary (every 10 turns)."""
        return self.turn_index > 0 and self.turn_index % 10 == 0

    def get_last_n_turns(self, n: int) -> List[tuple[str, str]]:
        """Get last N complete turns as (uuid, content) for legacy/manual callers."""
        turns = self.get_recent_turns(n)
        return [
            (str(uuid.uuid4()), f"User: {turn['user']}\nAssistant: {turn['assistant']}")
            for turn in turns
        ]


def get_user_conversation_buffer(user_id: str) -> ConversationBuffer:
    """Get or create the configured conversation buffer for one user."""
    if user_id not in _conversation_buffers:
        from core.config import get_config

        _conversation_buffers[user_id] = ConversationBuffer(
            max_messages=get_config().app.conversation_buffer_max_messages
        )
    return _conversation_buffers[user_id]


def cleanup_inactive_buffers(max_age_hours: int = 24):
    """Remove conversation buffers older than max_age_hours."""
    now = datetime.now(timezone.utc)
    to_remove = []

    for user_id, buffer in _conversation_buffers.items():
        age_hours = (now - buffer.last_activity).total_seconds() / 3600
        if age_hours > max_age_hours:
            to_remove.append(user_id)

    for user_id in to_remove:
        del _conversation_buffers[user_id]

    return len(to_remove)


def clear_user_buffer(user_id: str) -> int:
    """Clear a user's process-local conversation buffer and return message count."""
    if user_id in _conversation_buffers:
        count = len(_conversation_buffers[user_id].buffer)
        del _conversation_buffers[user_id]
        return count
    return 0
