"""Background refresh boundary. A worker can be supplied without coupling API to scraping."""
from typing import Protocol


class RefreshJobQueue(Protocol):
    def enqueue(self, portal_id: int, run_id: int) -> None:
        """Schedule processing for a persisted run and return promptly."""


class DeferredRefreshQueue:
    """Stage 1 queue placeholder; refreshes remain persisted as running until a worker is attached."""

    def enqueue(self, portal_id: int, run_id: int) -> None:
        return None


refresh_jobs: RefreshJobQueue = DeferredRefreshQueue()
