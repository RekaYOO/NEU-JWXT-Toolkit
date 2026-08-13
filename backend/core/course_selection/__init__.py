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
from .jwxk import (
    JWXK_CAS_SERVICE,
    JwxkBatch,
    JwxkError,
    JwxkPublicClient,
    parse_public_batches,
    resolve_network_mode,
)

__all__ = [
    "MODEL_VERSION",
    "CourseMarket",
    "CourseSelectionError",
    "CourseSelectionProvider",
    "MarketSnapshot",
    "SelectionPolicy",
    "optimize_course_weights",
    "JWXK_CAS_SERVICE",
    "JwxkBatch",
    "JwxkError",
    "JwxkPublicClient",
    "parse_public_batches",
    "resolve_network_mode",
]
