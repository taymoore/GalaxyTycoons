import logging
import os
from datetime import datetime
from typing import Optional, Type, TypeVar

import requests
from dotenv import load_dotenv
from PySide6.QtCore import QObject, QSemaphore, Signal, Slot

from api.models.company import Company

# Load environment variables from .env file
load_dotenv()

_logger = logging.getLogger(__name__)

T = TypeVar("T")

class CompanyDataManager(QObject):
    """Manager that fetches company data on demand via slot."""

    company_loaded = Signal(Company)
    error = Signal(str)

    def __init__(
        self,
        *,
        retry_attempts: int = 4,
        base_delay_seconds: float = 2.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.retry_attempts = retry_attempts
        self.base_delay_seconds = base_delay_seconds
        self.company: Optional[Company] = None
        self._stop_requested = False
        self._wake_semaphore = QSemaphore(0)

    @Slot()
    def fetch_company(self) -> None:
        """Fetch company data on demand."""
        try:
            company = self._fetch_with_retry(
                "https://api.g2.galactictycoons.com/public/company",
                Company
            )
            if company:
                self.company = company
                self.company_loaded.emit(company)
        except Exception as exc:  # noqa: BLE001
            _logger.error("Error fetching company data: %s", exc)
            self.error.emit(str(exc))

    def request_stop(self) -> None:
        """Request a stop; will interrupt any ongoing waits in fetch operations."""
        self._stop_requested = True
        self._wake_semaphore.release(1)

    def _fetch_with_retry(self, url: str, model_class: Type[T]) -> Optional[T]:
        api_key = os.getenv("GT_API_KEY")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        delay = 0.0
        for attempt in range(self.retry_attempts):
            if self._stop_requested:
                return None
            if delay:
                if self._wait_or_stop(delay):
                    return None

            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else self.base_delay_seconds * (2**attempt)
                _logger.warning("Rate limited on company fetch; delaying for %.2fs", delay)
                continue

            try:
                response.raise_for_status()
                data = model_class.model_validate(response.json())
                return data
            except Exception as exc:  # noqa: BLE001
                if attempt == self.retry_attempts - 1:
                    raise
                _logger.warning("Fetch failed (attempt %s/%s): %s", attempt + 1, self.retry_attempts, exc)
                delay = self.base_delay_seconds * (2**attempt)
        return None

    def _wait_or_stop(self, seconds: float) -> bool:
        """Wait up to seconds; return True if stop was requested during wait."""
        # QSemaphore.tryAcquire blocks this thread only; releasing it ends wait early
        woke = self._wake_semaphore.tryAcquire(1, int(seconds * 1000))
        return woke or self._stop_requested


def create_company_manager(*, retry_attempts: int = 4, base_delay_seconds: float = 2.0) -> CompanyDataManager:
    """Convenience helper to create a company data manager."""

    return CompanyDataManager(
        retry_attempts=retry_attempts,
        base_delay_seconds=base_delay_seconds,
    )