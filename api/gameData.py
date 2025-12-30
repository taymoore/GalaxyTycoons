import logging
from typing import Any, Dict
import pickle
from pathlib import Path
import requests
from functools import cache
import os
from dotenv import load_dotenv

from api.models.gameData import GameData, Building, Worker, WorkerType

# Load environment variables from .env file
load_dotenv()

CACHE_FILENAME = "game_data.pkl"

LOAD_CACHE = True

_logger = logging.getLogger(__name__)


def _get_gamedata() -> GameData:
    api_key = os.getenv("GT_API_KEY")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    content_response = requests.get("https://api.g2.galactictycoons.com/gamedata.json", headers=headers)
    content_response.raise_for_status()
    if content_response is None:
        raise ValueError("Failed to fetch game data from API.")
    game_data = GameData.model_validate(content_response.json())
    game_data.materials_dict = {
        material.id: material for material in game_data.materials
    }
    return game_data


# Load cache from disk
game_data_cache: GameData
if LOAD_CACHE:
    try:
        if Path(f".data/{CACHE_FILENAME}").exists():
            game_data_cache = pickle.load(open(f".data/{CACHE_FILENAME}", "rb"))
            _logger.info("Loaded game data from cache file.")
        else:
            game_data_cache = _get_gamedata()
            _logger.info("No cache file found, fetched data from API.")
    except (IOError, ValueError) as e:
        _logger.error(f"Error loading {CACHE_FILENAME}: {e}")
        game_data_cache = _get_gamedata()
else:
    game_data_cache = _get_gamedata()


def get_gamedata() -> GameData:
    return game_data_cache


def save_gamedata() -> None:
    Path(".data").mkdir(parents=True, exist_ok=True)
    with open(f".data/{CACHE_FILENAME}", "wb") as f:
        pickle.dump(game_data_cache, f)


def get_item_name(item_id: int) -> str:
    material = get_gamedata().materials_dict.get(item_id)
    if material:
        return material.name
    else:
        return "Unknown Item"


@cache
def get_building(building_id: int) -> Building:
    for building in get_gamedata().buildings:
        if building.id == building_id:
            return building
    raise ValueError(f"Building with ID {building_id} not found.")


@cache
def get_worker(worker_type: WorkerType) -> Worker:
    for worker in get_gamedata().workers:
        if worker.type == worker_type:
            return worker
    raise ValueError(f"Worker with type {worker_type} not found.")

@cache
def get_worker_housing(worker_type: WorkerType) -> Building:
    for building in get_gamedata().buildings:
        if building.workersHousing and building.workersHousing[worker_type.value - 1] > 0:
            return building