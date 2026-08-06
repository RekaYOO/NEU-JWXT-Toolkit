"""Schemas for grade tracking configuration and actions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GradeTrackingConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Accepted but never applied so one release of cached/older frontend code
    # cannot overwrite the dedicated switch endpoint.
    enabled: bool | None = Field(default=None, exclude=True)
    notify_initial: bool | None = Field(default=None, exclude=True)
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    start_hour: int = Field(default=9, ge=0, le=23)
    end_hour: int = Field(default=21, ge=1, le=24)
    site_url: str = Field(default="", max_length=500)
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Literal["ssl", "starttls", "none"] = "ssl"
    smtp_username: str = Field(default="", max_length=255)
    smtp_password: str | None = Field(default=None, max_length=500)
    clear_smtp_password: bool = False
    from_email: str = Field(default="", max_length=255)
    to_email: str = Field(default="", max_length=255)


class GradeTrackingEnabledUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
