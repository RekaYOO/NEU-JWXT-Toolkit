from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class FestivalActivityModel(BaseModel):
    id: str
    section: str
    name: str
    team_name: str = ""
    status: str = ""
    category: str = ""
    type: str = ""
    award: str = ""
    sign_in: str = ""
    sign_out: str = ""
    certificate_available: bool = False
    registration_time: str = ""
    activity_time: str = ""
    start_time: Optional[datetime] = None
    duration: str = ""
    department: str = ""
    location: str = ""
    notes: str = ""
    description: str = ""


class FestivalActivitiesResponse(BaseModel):
    available: bool
    username: str = ""
    source: str
    activities: List[FestivalActivityModel] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    total: int = 0
    cache: Optional[Dict[str, Any]] = None


class CertificateArchiveRequest(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot precede start_date")
        if (self.end_date - self.start_date).days > 369:
            raise ValueError("date range cannot exceed 370 days")
        return self
