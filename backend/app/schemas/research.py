from pydantic import BaseModel, Field


class ResearchEnrollmentRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=128)
    batch_id: str = Field(min_length=1, max_length=128)
    phone: str = Field(min_length=1, max_length=16)
    email: str = Field(min_length=3, max_length=20)
    reason: str = Field(default="", max_length=300)


class ResearchCancellationRequest(BaseModel):
    topic_id: str = Field(min_length=1, max_length=128)
