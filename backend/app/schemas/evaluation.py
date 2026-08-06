"""HTTP request contracts for teaching evaluations."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Identifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$"),
]
Score = Annotated[int, Field(ge=1, le=6)]
ScoreSelection = Annotated[list[Score], Field(min_length=1, max_length=6)]
CustomScore = Score | ScoreSelection


class EvaluationRequest(BaseModel):
    """Shared strict boundary for evaluation write operations."""

    model_config = ConfigDict(extra="forbid")

    task_id: Identifier = Field(description="Evaluation task ID")
    dry_run: bool = Field(
        default=True,
        description="Validate and preview without writing to the remote system",
    )
    strategy: Literal["highest", "lowest", "custom"] = "highest"
    custom_scores: Annotated[
        dict[Identifier, CustomScore],
        Field(max_length=100),
    ] | None = None

class EvaluationSubmitRequest(EvaluationRequest):
    """Submit one teaching evaluation."""

    xspjid: Identifier = Field(description="Student evaluation record ID")
    text_results: Annotated[
        dict[Identifier, Annotated[str, Field(max_length=2_000)]],
        Field(max_length=50),
    ] | None = None


class EvaluationBatchRequest(EvaluationRequest):
    """Submit a bounded set of teaching evaluations sequentially."""

    delay: float = Field(default=2.0, ge=0.0, le=5.0, allow_inf_nan=False)
    xspjids: Annotated[list[Identifier], Field(min_length=1, max_length=50)] | None = None

    @model_validator(mode="after")
    def reject_duplicate_course_ids(self):
        if self.xspjids is not None and len(self.xspjids) != len(set(self.xspjids)):
            raise ValueError("xspjids must not contain duplicates")
        return self
