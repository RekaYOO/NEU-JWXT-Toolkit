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


class SystemMailConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: Literal["ssl", "starttls", "none"] = "ssl"
    smtp_username: str = Field(default="", max_length=255)
    smtp_password: str | None = Field(default=None, max_length=500)
    clear_smtp_password: bool = False
    from_email: str = Field(default="", max_length=255)
    to_email: str = Field(default="", max_length=255)


class AuthRecoveryConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_base_url: str = Field(default="", max_length=500)
