from functools import cache
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from PySide6.QtCore import Signal, Slot

from api.base_data_manager import BaseDataManager
from api.models.company import Company, Base, Warehouse

FETCH_COMPANY_TIMEOUT_SECONDS = 60 * 5  # 5 minutes

_logger = logging.getLogger(__name__)


class CompanyDataManager(BaseDataManager):
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
        super().__init__(retry_attempts, base_delay_seconds, parent)
        self.base_dict: Dict[int, Base] = {}
        self.fetch_company_timestamp: Optional[datetime] = None
        self.fetch_base_timestamp: Dict[int, datetime] = {}
        self.company = None

    @Slot()
    def fetch_company(self, force=False) -> Optional[Company]:
        """Fetch company data on demand."""
        if (
            force is False
            and self.company is not None
            and self.fetch_company_timestamp
            and datetime.now() - self.fetch_company_timestamp
            < timedelta(seconds=FETCH_COMPANY_TIMEOUT_SECONDS)
        ):
            return self.company
        _logger.debug("Starting company data fetch...")
        try:
            self.company = self._fetch_with_retry(
                "https://api.g2.galactictycoons.com/public/company", Company
            )
            if self.company:
                for ship in self.company.ships:
                    if ship.warehouse_id:
                        ship.warehouse = self._fetch_with_retry(
                            f"https://api.g2.galactictycoons.com/public/company/warehouse/{ship.warehouse_id}",
                            Warehouse,
                        )
                self.fetch_company_timestamp = datetime.now()
                _logger.info(f"Fetched company data at {self.fetch_company_timestamp}")
                self.company_loaded.emit(self.company)
                for base in self.company.bases:
                    base = self.fetch_base(base.id, force)
        except Exception as exc:  # noqa: BLE001
            _logger.error("Error fetching company data: %s", exc)
            self.error.emit(str(exc))
            self.company = None
        return self.company

    @Slot(int)
    def fetch_base(self, id: int, force=False) -> Optional[Base]:
        """Fetch base data on demand."""
        if (
            force is False
            and id in self.base_dict
            and self.fetch_base_timestamp.get(id)
            and datetime.now() - self.fetch_base_timestamp[id]
            < timedelta(seconds=FETCH_COMPANY_TIMEOUT_SECONDS)
        ):
            return self.base_dict[id]
        try:
            base = self._fetch_with_retry(
                f"https://api.g2.galactictycoons.com/public/company/base/{id}", Base
            )
            if base:
                self.base_dict[base.id] = base
                self.fetch_base_timestamp[base.id] = datetime.now()
                self.base_loaded.emit(base)
        except Exception as exc:  # noqa: BLE001
            _logger.error("Error fetching base data: %s", exc)
            self.error.emit(str(exc))
        return base
