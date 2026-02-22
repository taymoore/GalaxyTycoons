from functools import cache
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Optional, Type, TypeVar

import requests
from dotenv import load_dotenv
from PySide6.QtCore import QObject, QSemaphore, Signal, Slot

from api.models.company import Company, Base

FETCH_COMPANY_TIMEOUT_SECONDS = 60 * 5  # 5 minutes

# Load environment variables from .env file
load_dotenv()

_logger = logging.getLogger(__name__)

T = TypeVar("T")


class CompanyDataManager(QObject):
    """Manager that fetches company data on demand via slot."""

    company_loaded = Signal(Company)
    base_loaded = Signal(Base)
    error = Signal(str)

    def __init__(
        self,
        retry_attempts: int = 4,
        base_delay_seconds: float = 2.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.retry_attempts = retry_attempts
        self.base_delay_seconds = base_delay_seconds
        self._stop_requested = False
        self._wake_semaphore = QSemaphore(0)
        self.base_dict: Dict[int, Base] = {}
        self.fetch_company_timestamp: Optional[datetime] = None
        self.company = None

    @Slot()
    def fetch_company(self) -> Optional[Company]:
        """Fetch company data on demand."""
        if (
            self.fetch_company_timestamp
            and datetime.now() - self.fetch_company_timestamp
            < timedelta(seconds=FETCH_COMPANY_TIMEOUT_SECONDS)
        ):
            _logger.debug(
                f"Not fetching company data; last fetch was at {self.fetch_company_timestamp}"
            )
            return self.company
        _logger.info("Starting company data fetch...")
        try:
            self.company = self._fetch_with_retry(
                "https://api.g2.galactictycoons.com/public/company", Company
            )
            if self.company:
                self.fetch_company_timestamp = datetime.now()
                _logger.info(f"Fetched company data at {self.fetch_company_timestamp}")
                self.company_loaded.emit(self.company)
                for base in self.company.bases:
                    base = self.fetch_base(base.id)
        except Exception as exc:  # noqa: BLE001
            _logger.error("Error fetching company data: %s", exc)
            self.error.emit(str(exc))
            self.company = None
        return self.company

    @Slot(int)
    def fetch_base(self, id: int) -> Optional[Base]:
        """Fetch base data on demand."""
        try:
            base = self._fetch_with_retry(
                f"https://api.g2.galactictycoons.com/public/company/base/{id}", Base
            )
            if base:
                self.base_dict[base.id] = base
                self.base_loaded.emit(base)
        except Exception as exc:  # noqa: BLE001
            _logger.error("Error fetching base data: %s", exc)
            self.error.emit(str(exc))
        return base

    def request_stop(self) -> None:
        """Request a stop; will interrupt any ongoing waits in fetch operations."""
        self._wake_semaphore.release(1)

    def _fetch_with_retry(self, url: str, model_class: Type[T]) -> Optional[T]:
        api_key = os.getenv("GT_API_KEY")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        delay = 0.0
        for attempt in range(self.retry_attempts):
            if self._wake_semaphore.tryAcquire(1, int(delay * 1000)):
                return None

            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after
                    else self.base_delay_seconds * (2**attempt)
                )
                _logger.warning(
                    "Rate limited on company fetch; delaying for %.2fs", delay
                )
                continue
            elif response.status_code == 403:
                raise PermissionError("Access forbidden: check your API key.")

            try:
                response.raise_for_status()
                print(response.text)
                data = model_class.model_validate(response.json())
                return data
            except Exception as exc:  # noqa: BLE001
                if attempt == self.retry_attempts - 1:
                    raise
                _logger.warning(
                    "Fetch failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.retry_attempts,
                    exc,
                )
                delay = self.base_delay_seconds * (2**attempt)
        return None
