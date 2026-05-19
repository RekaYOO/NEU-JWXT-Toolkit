from typing import Optional
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False


class LoginResponse(BaseModel):
    success: bool
    message: str
    username: Optional[str] = None
