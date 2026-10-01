"""Bounded local agenda documents; no official course mutations."""

from datetime import date as Date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgendaEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    # Legacy single-date records keep using ``date``. New records can select
    # several concrete dates or a weekday across several teaching weeks.
    selection_mode: Literal["date", "week"] = "date"
    date: Date | None = None
    dates: list[Date] = Field(default_factory=list, max_length=100)
    weekday: int | None = Field(default=None, ge=1, le=7)
    weeks: list[int] = Field(default_factory=list, max_length=30)
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
        if self.selection_mode == "date":
            values = list(dict.fromkeys(self.dates or ([self.date] if self.date else [])))
            if not values:
                raise ValueError("至少选择一个日期")
            self.dates = sorted(values)
            self.date = self.dates[0]
            self.weekday = None
            self.weeks = []
        else:
            values = sorted(set(self.weeks))
            if self.weekday is None or not values or any(value < 1 for value in values):
                raise ValueError("按周次日程必须选择星期和教学周")
            self.date = None
            self.dates = []
            self.weeks = values
        return self


class AgendaMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Date
    target: Date


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
    semester_end: Date | None = None
