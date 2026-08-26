"""
Shared low-level utilities for the event-intelligence application.

Only domain-neutral helpers belong in this package.

Domain decisions belong in their owning services, for example:

    source independence
        -> SourceProvenanceService

    entity resolution
        -> EntityResolutionService

    relationship canonicalization
        -> RelationshipService

    evidence admission / materiality
        -> evidence pipeline

    insight equivalence / redundancy
        -> insight quality / ranking components
"""

from utils.deduplicate import (
    DeduplicationError,
    deduplicate_by_key,
    stable_unique,
)
from utils.logger import (
    LoggingConfigurationError,
    configure_logging,
    get_logger,
)


__all__ = [
    "DeduplicationError",
    "LoggingConfigurationError",
    "configure_logging",
    "get_logger",
    "stable_unique",
    "deduplicate_by_key",
]