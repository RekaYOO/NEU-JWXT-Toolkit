from typing import Optional
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False
    network_mode: str = "auto"


class LoginResponse(BaseModel):
    success: bool
    message: str
    username: Optional[str] = None
    requires_webvpn: bool = False
    network_mode: str = "direct"


class WebVPNQRStartRequest(BaseModel):
    username: Optional[str] = None


class WebVPNQRStatusRequest(BaseModel):
    flow_id: str
