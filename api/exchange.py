import time
from typing import Dict, Optional
import logging
import pickle
from pathlib import Path
from requests import Session, RequestException
from datetime import datetime, timedelta
import pandas as pd
import os
from dotenv import load_dotenv
from PySide6.QtCore import Signal, QObject

from api.models.exchange import Listing, Listings

# Load environment variables from .env file
load_dotenv()

_logger = logging.getLogger(__name__)


class Exchange(QObject):
    """Manages exchange listings caching, fetching, and retrieval."""

    # Signal emitted when exchange data is updated
    exchange_updated_signal = Signal()

    _instance = None
    _CACHE_FILENAME = "exchange.pkl"
    _CACHE2_FILENAME = "exchange2.pkl"
    _CACHE_DIR = ".data"
    _UPDATE_RATE = timedelta(minutes=5)

    listings: Dict[int, Listing] = {}
    updated_time: Optional[datetime] = None
    session = Session()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Exchange, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            super().__init__()
            self._initialized = True

    @staticmethod
    def load_cache() -> None:
        cache_path = Path(Exchange._CACHE_DIR) / Exchange._CACHE_FILENAME
        try:
            if cache_path.exists():
                with open(cache_path, "rb") as f:
                    Exchange.listings, Exchange.updated_time = pickle.load(f)
                _logger.info("Loaded game data from cache file.")
            else:
                Exchange.listings = {}
                _logger.info("No cache file found, starting with empty cache.")
        except (pickle.UnpicklingError, IOError) as e:
            _logger.error(f"Error loading cache from {cache_path}: {e}")
            Exchange.listings = {}
        except Exception as e:
            _logger.error(f"Unexpected error loading cache: {e}")
            Exchange.listings = {}

        # Merge with cache2 if exists
        cache2_path = Path(Exchange._CACHE_DIR) / Exchange._CACHE2_FILENAME
        try:
            if cache2_path.exists():
                with open(cache2_path, "rb") as f:
                    listings2, _ = pickle.load(f)
                for listing1, listing2 in zip(
                    Exchange.listings.values(), listings2.values()
                ):
                    listing1.dataframe = listing1.dataframe.combine_first(
                        listing2.dataframe
                    )
                # delete cache2 after merging
                cache2_path.unlink()
                _logger.info("Merged additional game data from second cache file.")
        except (pickle.UnpicklingError, IOError) as e:
            _logger.error(f"Error loading second cache from {cache2_path}: {e}")

    @staticmethod
    def _save_to_disk() -> None:
        cache_path = Path(Exchange._CACHE_DIR) / Exchange._CACHE_FILENAME
        Path(Exchange._CACHE_DIR).mkdir(parents=True, exist_ok=True)
        try:
            with cache_path.open("wb") as f:
                pickle.dump((Exchange.listings, Exchange.updated_time), f)
            _logger.info(f"Cache saved to {cache_path}.")
        except IOError as e:
            _logger.error(f"Error saving cache to {cache_path}: {e}")
            raise
        except Exception as e:
            _logger.error(f"Unexpected error saving cache: {e}")
            raise

    @staticmethod
    def clear_cache() -> None:
        Exchange.listings = {}
        _logger.info("Exchange cache cleared.")

    @staticmethod
    def update_listings(force: bool = False):
        current_time = datetime.now()
        if not force and (
            Exchange.updated_time
            and current_time - Exchange.updated_time < Exchange._UPDATE_RATE
        ):
            return

        url = "https://api.g2.galactictycoons.com/public/exchange/mat-details/"

        api_key = os.getenv("GT_API_KEY")
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = Exchange.session.get(url, headers=headers)
            if response.status_code == 429:
                _logger.warning("Rate limited by exchange API.")
                sleep_time = int(response.headers.get("Retry-After", 60))
                _logger.info(f"Sleep for {sleep_time} seconds before retrying.")
                time.sleep(sleep_time)
                return
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as e:
                raise ValueError(f"Failed to parse JSON response: {e}")
        except RequestException as e:
            raise RuntimeError(f"Failed to fetch listings from API: {e}")

        if "materials" not in data:
            raise ValueError("Unexpected response format: 'materials' key not found.")

        Exchange.updated_time = current_time

        try:
            listings = Listings.model_validate(data["materials"])
        except Exception as e:
            raise ValueError(f"Failed to parse listings data: {e}")

        for listing in listings:
            # Update price history if listing exists in cache
            if listing.id in Exchange.listings:
                listing.dataframe = Exchange.listings[listing.id].dataframe
            # Add current data point to dataframe
            listing.dataframe.loc[datetime.today().isoformat(), "current_price"] = (
                listing.current_price
            )
            listing.dataframe.loc[datetime.today().isoformat(), "average_price"] = (
                listing.average_price
            )
            listing.dataframe.loc[
                datetime.today().isoformat(), "total_quantity_available"
            ] = listing.total_quantity_available
            for price_history_entry in listing.price_history:
                listing.dataframe.loc[
                    price_history_entry.date + "T00:00:00", "quantity_sold"
                ] = price_history_entry.quantity_sold
            listing.updated_time = current_time
            Exchange.listings[listing.id] = listing
        _logger.info(
            f"Exchange listings updated. Total listings: {len(Exchange.listings)}"
        )

        # Emit signal that exchange data has been updated
        Exchange._instance.exchange_updated_signal.emit()

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
        if id in Exchange.listings:
            if current_time - Exchange.updated_time < Exchange._UPDATE_RATE:
                return Exchange.listings.get(id)
            else:
                _logger.info(f"Cache for listing {id} is stale, fetching new data.")
        else:
            _logger.info(f"Listing {id} not in cache, fetching from API.")

        # TODO: Could use update single listing from API
        Exchange.update_listings()
        return Exchange.listings.get(id)
