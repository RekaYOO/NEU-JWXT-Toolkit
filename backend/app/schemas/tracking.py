"""Schemas for grade tracking configuration and actions."""

from typing import Literal

from pydantic import BaseModel, Field


class GradeTrackingConfigUpdate(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=15, ge=5, le=1440)
    start_hour: int = Field(default=9, ge=0, le=23)
    end_hour: int = Field(default=21, ge=1, le=24)
    notify_initial: bool = True
    site_url: str = Field(default="", max_length=500)
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Literal["ssl", "starttls", "none"] = "ssl"
    smtp_username: str = Field(default="", max_length=255)
    smtp_password: str | None = Field(default=None, max_length=500)
    clear_smtp_password: bool = False
    from_email: str = Field(default="", max_length=255)
    to_email: str = Field(default="", max_length=255)
