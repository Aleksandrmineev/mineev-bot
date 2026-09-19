"""AMS source adapter namespace.

The concrete client/parser will be added here after the public AMS search
request format is pinned down with fixtures. Keeping this namespace separate
prevents AMS-specific assumptions leaking into the karriere.at code.
"""

from src.sources.ams.client import AmsBrowserClient

__all__ = ["AmsBrowserClient"]
