"""Pure, stateless course-weight game model."""

from .model import (
    MODEL_VERSION,
    CourseMarket,
    CourseSelectionError,
    MarketSnapshot,
    SelectionPolicy,
    optimize_course_weights,
)
from .provider import CourseSelectionProvider

__all__ = [
    "MODEL_VERSION",
    "CourseMarket",
    "CourseSelectionError",
    "CourseSelectionProvider",
    "MarketSnapshot",
    "SelectionPolicy",
    "optimize_course_weights",
]
