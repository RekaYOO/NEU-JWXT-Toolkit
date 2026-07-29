from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ResearchEnrollmentRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=128)
    batch_id: str = Field(min_length=1, max_length=128)
    phone: str = Field(min_length=1, max_length=16)
    email: str = Field(min_length=3, max_length=20)
    reason: str = Field(default="", max_length=300)


class ResearchCancellationRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=128)


class ResearchFavoriteRequest(BaseModel):
    batch_id: str = Field(min_length=1, max_length=128)
    topic_id: str = Field(min_length=1, max_length=128)
    favorite: bool


class ResearchChangeSummary(BaseModel):
    added: int = 0
    updated: int = 0
    removed: int = 0
    new_batch: bool = False
    confirmed_changed: bool = False


class ResearchCacheResponse(BaseModel):
    available: bool
    version: Optional[int] = None
    username: str = ""
    saved_at: str = ""
    revision: str = ""
    batch: Dict[str, Any] = Field(default_factory=dict)
    eligibility: Dict[str, Any] = Field(default_factory=dict)
    topics: List[Dict[str, Any]] = Field(default_factory=list)
    confirmed_topics: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    favorite_topic_ids: List[str] = Field(default_factory=list)
    favorite_topics: List[Dict[str, Any]] = Field(default_factory=list)
    update_available: bool = False
    changes: ResearchChangeSummary = Field(default_factory=ResearchChangeSummary)
    cache: Optional[Dict[str, Any]] = None


class ResearchFavoriteResponse(BaseModel):
    success: bool
    favorite: bool
    favorite_topic_ids: List[str] = Field(default_factory=list)
    favorite_topics: List[Dict[str, Any]] = Field(default_factory=list)
