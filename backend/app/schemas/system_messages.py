from typing import List, Literal

from pydantic import BaseModel, Field


class SystemMessageReadItem(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)
    kind: Literal["reminder", "audit"] = "reminder"


class SystemMessageReadRequest(BaseModel):
    messages: List[SystemMessageReadItem] = Field(min_length=1, max_length=20)
