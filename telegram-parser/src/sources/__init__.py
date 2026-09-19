"""Job-board source adapters.

Each source owns its request and parsing details. The scheduler and Telegram
bot consume the common :class:`~src.models.JobPosting` model instead.
"""

from src.sources.base import JobSource, SearchDefinition

__all__ = ["JobSource", "SearchDefinition"]
