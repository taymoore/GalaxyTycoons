import logging
from datetime import datetime, timedelta
import os
from typing import List, Optional

from PySide6.QtCore import Slot
import requests

from api.base_data_manager import BaseDataManager
from api.models.gameData import MaterialAmount
from api.models.wishlist import WishlistModel

FETCH_WISHLIST_TIMEOUT_SECONDS = 60 * 5  # 5 minutes

_logger = logging.getLogger(__name__)


class WishlistDataManager(BaseDataManager):
    """Manager that fetches company data on demand via slot."""

    def __init__(
        self,
        retry_attempts: int = 4,
        base_delay_seconds: float = 2.0,
        parent=None,
    ) -> None:
        super().__init__(retry_attempts, base_delay_seconds, parent)
        self.wishlists: List[WishlistModel] = []
        self.fetch_wishlist_timestamp: Optional[datetime] = None

    @Slot()
    def fetch_wishlists(self, force=False) -> List[WishlistModel]:
        """Fetch wishlist data on demand."""
        if (
            force is False
            and self.wishlists
            and self.fetch_wishlist_timestamp
            and datetime.now() - self.fetch_wishlist_timestamp
            < timedelta(seconds=FETCH_WISHLIST_TIMEOUT_SECONDS)
        ):
            return self.wishlists
        _logger.debug("Starting wishlist data fetch...")
        try:
            wishlists_result = self._fetch_with_retry(
                "https://api.g2.galactictycoons.com/public/wishlists",
                List[WishlistModel],
            )
            if wishlists_result:
                self.wishlists = wishlists_result
                self.fetch_wishlist_timestamp = datetime.now()
                _logger.info(
                    f"Fetched wishlist data at {self.fetch_wishlist_timestamp}"
                )
        except Exception as exc:  # noqa: BLE001
            _logger.error("Error fetching company data: %s", exc)
        return self.wishlists

    @Slot(int, list)
    def add_items_to_wishlist(
        self, wishlist_id: int, mats: List[MaterialAmount]
    ) -> bool:
        """Add materials to a wishlist.

        Returns:
            True if successful, False otherwise
        """
        api_key = os.getenv("GT_API_KEY")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        delay = 0.0
        for attempt in range(self.retry_attempts):
            if self._wake_semaphore.tryAcquire(1, int(delay * 1000)):
                return False

            try:
                response = requests.post(
                    f"https://api.g2.galactictycoons.com/public/wishlist/{wishlist_id}/additems",
                    json=[mat.model_dump() for mat in mats],
                    headers=headers,
                    timeout=15,
                )

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = (
                        float(retry_after)
                        if retry_after
                        else self.base_delay_seconds * (2**attempt)
                    )
                    _logger.warning(
                        "Rate limited when adding items to wishlist %s. Retrying in %.2fs...",
                        wishlist_id,
                        delay,
                    )
                    continue
                elif response.status_code == 403:
                    raise PermissionError("Access forbidden: check your API key.")

                response.raise_for_status()
                _logger.info("Successfully added items to wishlist %s", wishlist_id)
                return True

            except Exception as exc:  # noqa: BLE001
                if attempt == self.retry_attempts - 1:
                    _logger.error(
                        "Failed to add items to wishlist %s after %s attempts: %s",
                        wishlist_id,
                        self.retry_attempts,
                        exc,
                    )
                    return False
                _logger.warning(
                    "Failed to add items to wishlist (attempt %s/%s): %s",
                    attempt + 1,
                    self.retry_attempts,
                    exc,
                )
                delay = self.base_delay_seconds * (2**attempt)

        return False
