"""Declarative safety policies for remote write operations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MutationPolicy:
    operation: str
    automatic_retry: bool
    invalidations: tuple[str, ...]
    refetches: tuple[str, ...]

    def validate(self) -> None:
        if not self.operation.strip():
            raise ValueError("mutation operation is required")
        if not self.invalidations and not self.refetches:
            raise ValueError(
                f"mutation {self.operation!r} must declare a consistency action"
            )


def _policy(
    operation: str,
    *invalidations: str,
    refetches: tuple[str, ...] = (),
) -> MutationPolicy:
    policy = MutationPolicy(
        operation=operation,
        automatic_retry=False,
        invalidations=tuple(invalidations),
        refetches=refetches,
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
        refetches=("evaluation-tasks", "evaluation-courses"),
    ),
    "evaluation.batch": _policy(
        "evaluation.batch",
        refetches=("evaluation-tasks", "evaluation-courses"),
    ),
}


def mutation_policy(operation: str) -> MutationPolicy:
    try:
        return MUTATION_POLICIES[operation]
    except KeyError as error:
        raise KeyError(f"undeclared mutation operation: {operation}") from error
