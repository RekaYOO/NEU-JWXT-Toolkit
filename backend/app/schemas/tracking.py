"""Schemas for grade tracking configuration and actions."""

from pydantic import BaseModel, ConfigDict, Field


class GradeTrackingConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_minutes: int = Field(default=30, ge=5, le=1440)
    start_hour: int = Field(default=9, ge=0, le=23)
    end_hour: int = Field(default=21, ge=1, le=24)


class GradeTrackingEnabledUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
