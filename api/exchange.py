from typing import Dict, Optional
import logging
import pickle
from pathlib import Path
from requests import Session, RequestException
import atexit
from datetime import datetime, timedelta

from api.models.exchange import Listing, Listings

CACHE_FILENAME = "exchange.pkl"
CACHE_DIR = ".data"
UPDATE_RATE = timedelta(minutes=30)

_logger = logging.getLogger(__name__)


class Exchange:
    cache: Dict[int, Listing] = {}
    updated_time: Optional[datetime] = None
    session = Session()

    @staticmethod
    def load_cache() -> None:
        cache_path = Path(CACHE_DIR) / CACHE_FILENAME
        try:
            if cache_path.exists():
                with open(cache_path, "rb") as f:
                    Exchange.cache = pickle.load(f)
                    # for listing in Exchange.cache.values():
                    #     Listing.model_validate(listing)
                _logger.info("Loaded game data from cache file.")
            else:
                Exchange.cache = {}
                _logger.info("No cache file found, starting with empty cache.")
        except (pickle.UnpicklingError, IOError) as e:
            _logger.error(f"Error loading cache from {cache_path}: {e}")
            Exchange.cache = {}
        except Exception as e:
            _logger.error(f"Unexpected error loading cache: {e}")
            Exchange.cache = {}

    @staticmethod
    def _save_to_disk() -> None:
        cache_path = Path(CACHE_DIR) / CACHE_FILENAME
        Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
        try:
            with cache_path.open("wb") as f:
                pickle.dump(Exchange.cache, f)
            _logger.info(f"Cache saved to {cache_path}.")
        except IOError as e:
            _logger.error(f"Error saving cache to {cache_path}: {e}")
            raise
        except Exception as e:
            _logger.error(f"Unexpected error saving cache: {e}")
            raise

    @staticmethod
    def clear_cache() -> None:
        Exchange.cache = {}
        _logger.info("Exchange cache cleared.")

    @staticmethod
    def update_listings(force: bool = False):
        current_time = datetime.now()
        if force or (
            Exchange.updated_time and current_time - Exchange.updated_time < UPDATE_RATE
        ):
            return

        url = "https://api.g2.galactictycoons.com/public/exchange/mat-prices/"

        try:
            response = Exchange.session.get(url)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as e:
                raise ValueError(f"Failed to parse JSON response: {e}")
        except RequestException as e:
            raise RuntimeError(f"Failed to fetch listings from API: {e}")

        if "prices" not in data:
            raise ValueError("Unexpected response format: 'prices' key not found.")

        Exchange.updated_time = current_time

        try:
            listings = Listings.model_validate(data["prices"])
        except Exception as e:
            raise ValueError(f"Failed to parse listings data: {e}")
        for listing in listings:
            # Update price history if listing exists in cache
            if listing.id in Exchange.cache:
                listing.average_price_history = Exchange.cache[
                    listing.id
                ].average_price_history
            listing.average_price_history.loc[datetime.today().isoformat()] = (
                listing.average_price
            )
            Exchange.cache[listing.id] = listing
        _logger.info(
            f"Exchange listings updated. Total listings: {len(Exchange.cache)}"
        )

    @staticmethod
    def close() -> None:
        try:
            Exchange._save_to_disk()
        except Exception as e:
            _logger.error(f"Error during Exchange close: {e}")
        Exchange.session.close()

    @staticmethod
    def get_listing(id: int) -> Optional[Listing]:
        current_time = datetime.now()
        if id in Exchange.cache:
            if current_time - Exchange.updated_time < UPDATE_RATE:
                return Exchange.cache.get(id)
            else:
                _logger.info(f"Cache for listing {id} is stale, fetching new data.")
        else:
            _logger.info(f"Listing {id} not in cache, fetching from API.")

        # TODO: Could use update single listing from API
        Exchange.update_listings()
        return Exchange.cache.get(id)
