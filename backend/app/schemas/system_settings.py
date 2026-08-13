from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class CacheResourceSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    interval_minutes: int = Field(default=5, ge=1, le=52560000)


class CacheSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resources: dict[str, CacheResourceSetting] = Field(default_factory=dict)


class SystemSettingsResponse(BaseModel):
    cache: dict[str, CacheResourceSetting]
