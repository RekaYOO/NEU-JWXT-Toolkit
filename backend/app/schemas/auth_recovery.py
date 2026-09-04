"""Schemas for shared token-scoped authentication recovery."""

from pydantic import BaseModel, ConfigDict, Field


class AuthRecoveryCaptchaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    captcha_code: str = Field(min_length=1, max_length=16)


class AuthRecoverySMSRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=8)
    trust_device: bool = False
