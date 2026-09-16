"""Bounded local agenda documents; no official course mutations."""

from datetime import date
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgendaEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    date: date
    title: str = Field(min_length=1, max_length=120)
    start_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    location: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)
    important: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def valid_time(self):
        if not self.title.strip() or self.start_time >= self.end_time:
            raise ValueError("标题不能为空，结束时间必须晚于开始时间")
        return self


class AgendaMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: date
    target: date


class AgendaDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0)
    events: list[AgendaEvent] = Field(default_factory=list, max_length=1000)
    moves: list[AgendaMove] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_items(self):
        if len({event.id for event in self.events}) != len(self.events):
            raise ValueError("日程标识重复")
        targets = [move.target for move in self.moves]
        if len(set(targets)) != len(targets) or any(move.source == move.target for move in self.moves):
            raise ValueError("目标日期已有调休或与原日期相同，请先撤销旧调休")
        return self


class AgendaResponse(AgendaDocument):
    semester_ended: bool = False
    semester_end: date | None = None
