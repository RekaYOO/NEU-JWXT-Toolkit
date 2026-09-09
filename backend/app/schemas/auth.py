import unicodedata
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _normalize_username(value: str) -> str:
    """Normalize browser/mobile input without ever modifying the password."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not normalized:
        raise ValueError("请输入学号")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise ValueError("学号不能包含空白或控制字符")
    return normalized


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    remember: bool = False
    network_mode: str = "direct"

    _validate_username = field_validator("username")(_normalize_username)


class LoginResponse(BaseModel):
    success: bool
    message: str
    username: Optional[str] = None
    requires_webvpn: bool = False
    network_mode: str = "direct"
    error_code: Optional[str] = None
    suggestion: Optional[str] = None


class WebVPNQRStartRequest(BaseModel):
    username: Optional[str] = Field(default=None, max_length=64)
    target_service: Literal["primary", "jwxk", "cxcy"] = "primary"

    @field_validator("username")
    @classmethod
    def normalize_optional_username(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not str(value).strip():
            return None
        return _normalize_username(value)


class WebVPNQRStatusRequest(BaseModel):
    flow_id: str


class WebVPNPasswordStartRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    remember: bool = False
    target_service: Literal["primary", "jwxk", "cxcy"] = "primary"

    _validate_username = field_validator("username")(_normalize_username)


class WebVPNSMSCodeRequest(BaseModel):
    flow_id: str


class WebVPNSMSSendRequest(WebVPNSMSCodeRequest):
    captcha_code: str


class WebVPNSMSVerifyRequest(WebVPNSMSCodeRequest):
    code: str
    trust_device: bool = False
