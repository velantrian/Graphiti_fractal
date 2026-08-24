"""Central application types."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict


class MemoryType(str, Enum):
    PERSONAL = "personal"
    PROJECT = "project"
    KNOWLEDGE = "knowledge"
    EXPERIENCE = "experience"


class EpisodeKind(str, Enum):
    CHAT_TURN = "chat_turn"
    CHAT_SUMMARY = "chat_summary"
    DOCUMENT = "document"
    EXPERIENCE = "experience"


class JobStage(str, Enum):
    PENDING = "pending"
    INGEST = "ingest"
    RATE_LIMITED = "rate_limited"
    DONE = "done"
    DONE_WITH_WARNINGS = "done_with_warnings"
    ERROR = "error"


class EpisodeDict(TypedDict, total=False):
    uuid: str
    content: str
    name: str
    score: float
    type: str
    group_id: str
    is_correction: bool
    episode_kind: str
    source_description: str
    created_at: Optional[str]


class EntityDict(TypedDict, total=False):
    uuid: str
    name: str
    summary: str
    score: float
    type: str
    group_id: str


class EdgeDict(TypedDict, total=False):
    uuid: str
    fact: str
    subject: Optional[str]
    object: Optional[str]
    relationship_type: Optional[str]
    name: Optional[str]
    score: float
    type: str
    group_id: str


class CommunityDict(TypedDict, total=False):
    uuid: str
    name: str
    summary: str
    score: float
    type: str
    group_id: str


class TimingProfile(TypedDict, total=False):
    chunking_time: float
    embedding_calls: int
    embedding_time: float
    graph_time: float
    total_time: float


class JobTiming(TypedDict, total=False):
    job_created_at: Optional[datetime]
    upload_request_started_at: Optional[datetime]
    ingest_started_at: Optional[datetime]
    ingest_finished_at: Optional[datetime]
    per_chunk: List[Dict[str, Any]]


class UploadJobStatus(TypedDict, total=False):
    stage: str
    total_chunks: Optional[int]
    processed_chunks: int
    started_at: str
    error: Optional[str]
    warnings: List[str]
    profile: TimingProfile
    timing: JobTiming
    message: str
    retry_in_seconds: float
    attempt: int


class IngestResult(TypedDict, total=False):
    status: str
    added: int
    skipped: int
    chunks: int
    elapsed: float
    warnings: List[str]


class RememberResult(TypedDict, total=False):
    status: str
    added: int
    skipped: int
    chunks: int
    elapsed: float
    warnings: List[str]
    reason: str


@dataclass
class SearchResult:
    episodes: List[EpisodeDict] = field(default_factory=list)
    entities: List[EntityDict] = field(default_factory=list)
    edges: List[EdgeDict] = field(default_factory=list)
    communities: List[CommunityDict] = field(default_factory=list)
    total_episodes: int = 0
    total_entities: int = 0
    total_edges: int = 0
    total_communities: int = 0


@dataclass
class ContextResult:
    text: str
    token_estimate: int
    sources: Dict[str, int] = field(default_factory=dict)


@dataclass
class ConversationMessage:
    role: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ConversationTurn:
    user: str
    assistant: str
    turn_index: int = 0


@dataclass
class CacheEntry:
    value: Any
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_expired(self, ttl_hours: int) -> bool:
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - created > timedelta(hours=ttl_hours)


@dataclass
class EmbeddingCacheEntry:
    value: List[float] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_expired(self, ttl_hours: int) -> bool:
        created = self.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - created > timedelta(hours=ttl_hours)


class ToolCallDict(TypedDict, total=False):
    tool: str
    command: Optional[str]
    args: Optional[str]
    exit_code: Optional[int]
    duration_ms: Optional[int]
    stdout: Optional[str]
    stderr: Optional[str]


class TestRunDict(TypedDict, total=False):
    framework: str
    command: Optional[str]
    passed: bool
    duration_ms: Optional[int]
    summary: Optional[str]


class ErrorEventDict(TypedDict, total=False):
    error_type: str
    message: Optional[str]
    stack: Optional[str]
    file: Optional[str]
    line: Optional[int]


class GraphNodeDict(TypedDict, total=False):
    uuid: str
    name: str
    summary: str
    labels: List[str]
    group_id: str
    created_at: str
    deleted: bool


class GraphEdgeDict(TypedDict, total=False):
    uuid: str
    source_node_uuid: str
    target_node_uuid: str
    relationship_type: str
    fact: str
    confidence: float
