from typing import Dict, Optional
import logging
import pickle
from pathlib import Path
from requests import Session, RequestException
import atexit
from datetime import datetime, timedelta
from PySide6.QtCore import Signal
import pandas as pd

from api.models.exchange import Listing, Listings

CACHE_FILENAME = "exchange.pkl"
CACHE_DIR = ".data"
UPDATE_RATE = timedelta(minutes=30)

_logger = logging.getLogger(__name__)

listings_updated = Signal(Dict[int, Listing])

class Exchange:
    listings: Dict[int, Listing] = {}
    updated_time: Optional[datetime] = None
    session = Session()

    @staticmethod
    def load_cache() -> None:
        cache_path = Path(CACHE_DIR) / CACHE_FILENAME
        try:
            if cache_path.exists():
                with open(cache_path, "rb") as f:
                    loaded_listings = pickle.load(f)
                
                # Migrate old Listing format to new format
                Exchange.listings = {}
                for listing_id, listing in loaded_listings.items():
                    # Check if this is an old Listing (has average_price_history/current_price_history)
                    if hasattr(listing, 'average_price_history') and hasattr(listing, 'current_price_history'):
                        _logger.info(f"Migrating old Listing format for {listing.name} (ID: {listing.id})")
                        
                        # Extract price columns from old dataframes
                        avg_df = listing.average_price_history.copy()
                        curr_df = listing.current_price_history.copy()
                        
                        # Rename 'price' column to match new schema
                        if 'price' in avg_df.columns:
                            avg_df = avg_df.rename(columns={'price': 'average_price'})
                        if 'price' in curr_df.columns:
                            curr_df = curr_df.rename(columns={'price': 'current_price'})
                        
                        # Merge both dataframes on their index (timestamps), using outer join to keep all timestamps
                        df = avg_df[['average_price']].join(curr_df[['current_price']], how='outer')
                        
                        # Add total_quantity_available column (use current value for all rows since we don't have historical data)
                        df['total_quantity_available'] = listing.total_quantity_available if hasattr(listing, 'total_quantity_available') else pd.NA
                        
                        # Create new Listing object
                        new_listing = Listing(
                            matId=listing.id,
                            matName=listing.name,
                            currentPrice=listing.current_price,
                            avgPrice=listing.average_price,
                            totalQtyAvailable=listing.total_quantity_available if hasattr(listing, 'total_quantity_available') else 0,
                            orders=listing.orders if hasattr(listing, 'orders') else [],
                            avgQtySoldDaily=listing.average_quantity_sold_daily if hasattr(listing, 'average_quantity_sold_daily') else 0.0,
                            priceHistory=listing.price_history if hasattr(listing, 'price_history') else []
                        )
                        new_listing.dataframe = df
                        new_listing.updated_time = listing.updated_time if hasattr(listing, 'updated_time') else datetime.now()
                        Exchange.listings[listing_id] = new_listing
                    elif hasattr(listing, 'dataframe'):
                        # Already new format
                        Exchange.listings[listing_id] = listing
                    else:
                        _logger.warning(f"Unknown Listing format for {listing.name} (ID: {listing.id}), skipping")
                
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

    @staticmethod
    def _save_to_disk() -> None:
        cache_path = Path(CACHE_DIR) / CACHE_FILENAME
        Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
        try:
            with cache_path.open("wb") as f:
                pickle.dump(Exchange.listings, f)
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
            Exchange.updated_time and current_time - Exchange.updated_time < UPDATE_RATE
        ):
            return

        url = "https://api.g2.galactictycoons.com/public/exchange/mat-details/"

        try:
            response = Exchange.session.get(url)
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
            listing.dataframe.loc[datetime.today().isoformat()] = {
                'current_price': listing.current_price,
                'average_price': listing.average_price,
                'total_quantity_available': listing.total_quantity_available
            }
            listing.updated_time = current_time
            Exchange.listings[listing.id] = listing
        _logger.info(
            f"Exchange listings updated. Total listings: {len(Exchange.listings)}"
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
        if id in Exchange.listings:
            if current_time - Exchange.updated_time < UPDATE_RATE:
                return Exchange.listings.get(id)
            else:
                _logger.info(f"Cache for listing {id} is stale, fetching new data.")
        else:
            _logger.info(f"Listing {id} not in cache, fetching from API.")

        # TODO: Could use update single listing from API
        Exchange.update_listings()
        return Exchange.listings.get(id)
