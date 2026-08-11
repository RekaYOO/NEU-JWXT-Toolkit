from pydantic import BaseModel, Field


class AcademicDocumentGenerateRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

