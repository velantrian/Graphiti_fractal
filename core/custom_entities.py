from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


class ProjectEntity(BaseModel):
    """Project metadata extracted into the semantic graph."""

    name: str = Field(description="Название проекта")
    status: str = Field(
        default="Development",
        description="Статус: Concept, Development, Testing, Production, Archived",
    )
    components: List[str] = Field(default_factory=list, description="Список компонентов проекта")
    owner: str = Field(default="Unknown", description="Владелец/lead проекта")
    priority: int = Field(
        default=3,
        ge=1,
        le=4,
        description="Приоритет: 1-Critical, 2-High, 3-Medium, 4-Low",
    )


class TechnicalConceptEntity(BaseModel):
    """Technical concept or architecture pattern."""

    name: str = Field(description="Название концепции")
    description: str = Field(description="Краткое описание концепции")
    abstraction_level: int = Field(
        default=2,
        ge=1,
        le=4,
        description="Уровень: 1-Basic, 2-Intermediate, 3-Advanced, 4-Research",
    )
    related_concepts: List[str] = Field(default_factory=list)
    implementation_status: str = Field(default="Theoretical")


class DecisionEntity(BaseModel):
    """Decision that can later be superseded or reviewed."""

    decision_text: str = Field(description="Формулировка решения")
    decision_date: datetime = Field(description="Когда принято решение")
    decision_maker: str = Field(description="Кто принял решение")
    rationale: str = Field(description="Почему принято решение")
    status: str = Field(default="Active")
    dependencies: List[str] = Field(default_factory=list)


class TeamEntity(BaseModel):
    team_name: str
    members: List[str]
    focus: str
    communication_tool: Optional[str] = None


class L3Summary(BaseModel):
    """Legacy-compatible typed representation of an L3 synthesis artifact."""

    summary_text: str
    consolidated_from: List[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    group_id: str
