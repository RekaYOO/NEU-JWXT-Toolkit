from typing import Optional
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False
    network_mode: str = "direct"


class LoginResponse(BaseModel):
    success: bool
    message: str
    username: Optional[str] = None
    requires_webvpn: bool = False
    network_mode: str = "direct"
    error_code: Optional[str] = None
    suggestion: Optional[str] = None


class WebVPNQRStartRequest(BaseModel):
    username: Optional[str] = None


class WebVPNQRStatusRequest(BaseModel):
    flow_id: str


class WebVPNPasswordStartRequest(BaseModel):
    username: str
    password: str
    remember: bool = False


class WebVPNSMSCodeRequest(BaseModel):
    flow_id: str


class WebVPNSMSVerifyRequest(WebVPNSMSCodeRequest):
    code: str
    trust_device: bool = False
