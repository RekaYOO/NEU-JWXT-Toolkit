from datetime import date, datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, model_validator


class FestivalSettingsUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    network_mode: Literal["follow", "direct", "webvpn"]


class FestivalServiceStatus(BaseModel):
    network_mode: Literal["follow", "direct", "webvpn"]
    effective_network_mode: Literal["direct", "webvpn"]
    primary_authenticated: bool = False
    current_user: str = ""
    service_authenticated: bool = False
    service_auth_state: Literal[
        "authenticated", "checking", "login_required", "network_unreachable",
        "campus_network_blocked", "service_unavailable",
    ]
    message: str
    error_code: Optional[str] = None
    auth_scope: str = "cxcy"


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
