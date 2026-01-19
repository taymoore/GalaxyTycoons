import logging
from typing import List, Optional
import pickle
from pathlib import Path
import requests
from functools import lru_cache
import os
from dotenv import load_dotenv

from api.models.gameData import GameData, Building, Recipe, Worker, WorkerType

# Load environment variables from .env file
load_dotenv()

_logger = logging.getLogger(__name__)


class GameDataManager:
    """Manages game data caching, fetching, and lookup operations."""

    _CACHE_FILENAME = "game_data.pkl"
    _CACHE_DIR = ".data"
    _LOAD_CACHE = True

    data: Optional[GameData] = None

    @classmethod
    def _fetch_from_api(cls) -> GameData:
        """Fetch game data from the API."""
        api_key = os.getenv("GT_API_KEY")
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        response = requests.get(
            "https://api.g2.galactictycoons.com/gamedata.json", headers=headers
        )
        response.raise_for_status()

        game_data = GameData.model_validate(response.json())
        game_data.materials_dict = {
            material.id: material for material in game_data.materials
        }
        return game_data

    @classmethod
    def initialize(cls) -> None:
        """Load game data from cache or API. Called automatically on first import."""
        if cls.data is not None:
            return  # Already initialized

        cache_path = Path(cls._CACHE_DIR) / cls._CACHE_FILENAME

        if cls._LOAD_CACHE and cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    cls.data = pickle.load(f)
                _logger.info("Loaded game data from cache file.")
                return
            except (pickle.UnpicklingError, IOError) as e:
                _logger.error(f"Error loading cache from {cache_path}: {e}")

        # Fetch from API if cache doesn't exist or failed to load
        try:
            cls.data = cls._fetch_from_api()
            _logger.info("Fetched game data from API.")
        except Exception as e:
            _logger.error(f"Error fetching game data from API: {e}")
            raise

    @classmethod
    def get(cls) -> GameData:
        """Get the current game data instance."""
        if cls.data is None:
            cls.initialize()
        return cls.data

    @classmethod
    def save(cls) -> None:
        """Save game data to cache."""
        if cls.data is None:
            _logger.warning("No game data to save.")
            return

        cache_path = Path(cls._CACHE_DIR) / cls._CACHE_FILENAME
        Path(cls._CACHE_DIR).mkdir(parents=True, exist_ok=True)

        try:
            with open(cache_path, "wb") as f:
                pickle.dump(cls.data, f)
            _logger.info(f"Game data saved to {cache_path}.")
        except IOError as e:
            _logger.error(f"Error saving cache to {cache_path}: {e}")
            raise

    @staticmethod
    @lru_cache(maxsize=None)
    def get_item_name(item_id: int) -> str:
        """Get the name of an item by ID."""
        material = GameDataManager.get().materials_dict.get(item_id)
        return material.name if material else "Unknown Item"

    @staticmethod
    @lru_cache(maxsize=None)
    def get_building(building_id: int) -> Building:
        """Get a building by ID."""
        for building in GameDataManager.get().buildings:
            if building.id == building_id:
                return building
        raise ValueError(f"Building with ID {building_id} not found.")

    @staticmethod
    @lru_cache(maxsize=None)
    def get_worker(worker_type: WorkerType) -> Worker:
        """Get a worker by type."""
        for worker in GameDataManager.get().workers:
            if worker.type == worker_type:
                return worker
        raise ValueError(f"Worker with type {worker_type} not found.")

    @staticmethod
    @lru_cache(maxsize=None)
    def get_worker_housing(worker_type: WorkerType) -> Building:
        """Get the housing building for a worker type."""
        for building in GameDataManager.get().buildings:
            if (
                building.workersHousing
                and building.workersHousing[worker_type.value - 1] > 0
            ):
                return building
        raise ValueError(f"No housing found for worker type {worker_type}.")

    @staticmethod
    @lru_cache(maxsize=None)
    def get_recipe_by_output(item_id: int) -> List[Recipe]:
        """Get recipes that produces the specified item ID."""
        recipes = []
        for recipe in GameDataManager.get().recipes:
            if recipe.output.id == item_id:
                recipes.append(recipe)
        if not recipes:
            raise ValueError(f"No recipe found producing item ID {item_id}.")
        return recipes

    @staticmethod
    @lru_cache(maxsize=None)
    def get_planet(planet_id: int):
        """Get a planet by ID."""
        for system in GameDataManager.get().systems:
            if system.planets:
                for planet in system.planets:
                    if planet.id == planet_id:
                        return planet
        raise ValueError(f"Planet with ID {planet_id} not found.")


# Initialize on import
GameDataManager.initialize()
