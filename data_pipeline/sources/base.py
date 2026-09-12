"""Abstract adapter every data source implements.

Keep adapters dumb: fetch a page/API and parse it into `ListingRecord`s,
nothing else. Excluded-listing filtering, validation, and dedup are shared
logic in `pipeline.py` so every source is held to the same rules regardless
of who writes the adapter.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..schema import ListingRecord


class SourceAdapter(ABC):
    name: str

    @abstractmethod
    def fetch(self, limit: int | None = None) -> Iterator[ListingRecord]:
        """Yield one ListingRecord per usable vehicle listing found."""
        raise NotImplementedError
