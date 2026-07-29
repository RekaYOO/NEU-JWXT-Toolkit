"""Declarative registry for rebuildable cache resources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from threading import RLock
from typing import Any, Callable, Iterable, Mapping

from .models import AccountScope, FetchContext, PayloadType


RESOURCE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
VARIANT_NAME = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

Canonicalizer = Callable[[Any], Any]
Fetcher = Callable[[FetchContext], Any]
Differ = Callable[[Any | None, Any], Mapping[str, Any]]


def identity(value: Any) -> Any:
    return value


def default_diff(previous: Any | None, current: Any) -> Mapping[str, Any]:
    return {} if previous == current else {"content_changed": True}


@dataclass(frozen=True)
class CacheResourceSpec:
    resource: str
    schema_version: int
    revision_algorithm_version: int
    account_scope: AccountScope
    payload_type: PayloadType
    max_age: timedelta
    offline_readable: bool
    sensitivity: str
    fetch: Fetcher
    canonicalize: Canonicalizer = identity
    diff: Differ = default_diff
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    mutation_invalidations: tuple[str, ...] = field(default_factory=tuple)

    def validate_shape(self) -> None:
        if not RESOURCE_NAME.fullmatch(self.resource):
            raise ValueError(f"Invalid cache resource name: {self.resource!r}")
        if self.schema_version < 1 or self.revision_algorithm_version < 1:
            raise ValueError("Cache schema and revision versions must be positive")
        if self.max_age < timedelta(0):
            raise ValueError("Cache max_age cannot be negative")
        if not self.sensitivity.strip():
            raise ValueError("Cache sensitivity must be declared")
        if not callable(self.fetch) or not callable(self.canonicalize) or not callable(self.diff):
            raise TypeError("fetch, canonicalize and diff must be callable")
        if self.resource in self.dependencies:
            raise ValueError(f"Resource {self.resource!r} cannot depend on itself")


class CacheRegistry:
    def __init__(self, specs: Iterable[CacheResourceSpec] = ()) -> None:
        self._specs: dict[str, CacheResourceSpec] = {}
        self._lock = RLock()
        for spec in specs:
            self.register(spec)

    def register(self, spec: CacheResourceSpec) -> None:
        spec.validate_shape()
        with self._lock:
            if spec.resource in self._specs:
                raise ValueError(f"Cache resource already registered: {spec.resource}")
            self._specs[spec.resource] = spec

    def get(self, resource: str) -> CacheResourceSpec:
        with self._lock:
            try:
                return self._specs[resource]
            except KeyError as exc:
                raise KeyError(f"Unknown cache resource: {resource}") from exc

    def resources(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._specs))

    def validate(self) -> None:
        with self._lock:
            specs = dict(self._specs)
        for spec in specs.values():
            referenced = (*spec.dependencies, *spec.mutation_invalidations)
            missing = sorted(set(referenced) - specs.keys())
            if missing:
                raise ValueError(
                    f"Resource {spec.resource!r} references unknown resources: "
                    f"{', '.join(missing)}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(resource: str) -> None:
            if resource in visiting:
                raise ValueError(f"Cache dependency cycle includes {resource!r}")
            if resource in visited:
                return
            visiting.add(resource)
            for dependency in specs[resource].dependencies:
                visit(dependency)
            visiting.remove(resource)
            visited.add(resource)

        for resource in specs:
            visit(resource)

    def dependents_of(self, resource: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                sorted(
                    spec.resource
                    for spec in self._specs.values()
                    if resource in spec.dependencies
                )
            )

