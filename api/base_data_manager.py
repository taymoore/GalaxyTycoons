import logging
import os
from typing import Optional, Type, TypeVar, get_args, get_origin

import requests
from dotenv import load_dotenv
from PySide6.QtCore import QObject, QSemaphore

# Load environment variables from .env file
load_dotenv()

_logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseDataManager(QObject):
    """Base class for data managers with common fetch retry logic."""

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

    def request_stop(self) -> None:
        """Request a stop; will interrupt any ongoing waits in fetch operations."""
        self._wake_semaphore.release(1)

    def _fetch_with_retry(self, url: str, model_class: Type[T]) -> Optional[T]:
        """Fetch data from API with retry logic and rate limit handling.
        
        Args:
            url: The API endpoint URL
            model_class: The Pydantic model class or List[ModelClass] type
            
        Returns:
            The validated model instance, list of model instances, or None if failed
        """
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
                    "Rate limited on fetch; delaying for %.2fs", delay
                )
                continue
            elif response.status_code == 403:
                raise PermissionError("Access forbidden: check your API key.")

            try:
                response.raise_for_status()
                json_data = response.json()
                
                # Handle List types
                origin = get_origin(model_class)
                if origin is list:
                    # Extract the item type from List[ItemType]
                    args = get_args(model_class)
                    if args:
                        item_type = args[0]
                        data = [item_type.model_validate(item) for item in json_data]  # type: ignore
                    else:
                        data = json_data  # type: ignore
                else:
                    data = model_class.model_validate(json_data)  # type: ignore
                
                return data  # type: ignore
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
