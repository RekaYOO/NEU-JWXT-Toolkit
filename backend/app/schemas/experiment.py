"""HTTP request contracts for experiment-course mutations."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


ExperimentIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$"),
]


class ExperimentCourseMutationRequest(BaseModel):
    """Required identifiers for selecting or deselecting an experiment round."""

    model_config = ConfigDict(extra="forbid")

    term: ExperimentIdentifier
    task_id: ExperimentIdentifier
    project_code: ExperimentIdentifier
    round_id: ExperimentIdentifier
