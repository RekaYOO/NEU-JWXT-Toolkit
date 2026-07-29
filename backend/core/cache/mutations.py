"""Declarative safety policies for remote write operations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MutationPolicy:
    operation: str
    automatic_retry: bool
    invalidations: tuple[str, ...]

    def validate(self) -> None:
        if not self.operation.strip():
            raise ValueError("mutation operation is required")
        if not self.invalidations:
            raise ValueError(
                f"mutation {self.operation!r} must declare invalidations"
            )


def _policy(operation: str, *invalidations: str) -> MutationPolicy:
    policy = MutationPolicy(
        operation=operation,
        automatic_retry=False,
        invalidations=tuple(invalidations),
    )
    policy.validate()
    return policy


MUTATION_POLICIES = {
    "research.enroll": _policy(
        "research.enroll", "research-training"
    ),
    "research.cancel": _policy(
        "research.cancel", "research-training"
    ),
    "experiment.select": _policy(
        "experiment.select", "academic-report", "experiment-courses"
    ),
    "experiment.deselect": _policy(
        "experiment.deselect", "academic-report", "experiment-courses"
    ),
    "evaluation.submit": _policy(
        "evaluation.submit",
        "evaluation-tasks",
        "evaluation-courses",
        "evaluation-indicators",
    ),
    "evaluation.batch": _policy(
        "evaluation.batch",
        "evaluation-tasks",
        "evaluation-courses",
        "evaluation-indicators",
    ),
}


def mutation_policy(operation: str) -> MutationPolicy:
    try:
        return MUTATION_POLICIES[operation]
    except KeyError as error:
        raise KeyError(f"undeclared mutation operation: {operation}") from error

