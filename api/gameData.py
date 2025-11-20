import logging
from typing import Any, Dict
import pickle
from pathlib import Path
import requests

from api.models.gameData import GameData

CACHE_FILENAME = "game_data.pkl"

LOAD_CACHE = True

_logger = logging.getLogger(__name__)


def _get_gamedata() -> GameData:
    content_response = requests.get("https://api.g2.galactictycoons.com/gamedata.json")
    content_response.raise_for_status()
    if content_response is None:
        raise ValueError("Failed to fetch game data from API.")
    game_data = GameData.model_validate(content_response.json())
    game_data.materials_dict = {
        material.id: material for material in game_data.materials
    }
    return game_data


# Load cache from disk
cache: GameData
if LOAD_CACHE:
    try:
        if Path(f".data/{CACHE_FILENAME}").exists():
            cache = pickle.load(open(f".data/{CACHE_FILENAME}", "rb"))
            _logger.info("Loaded game data from cache file.")
        else:
            cache = _get_gamedata()
            _logger.info("No cache file found, fetched data from API.")
    except (IOError, ValueError) as e:
        _logger.error(f"Error loading {CACHE_FILENAME}: {e}")
        cache = _get_gamedata()
else:
    cache = _get_gamedata()


def get_gamedata() -> GameData:
    return cache


def save_gamedata() -> None:
    Path(".data").mkdir(parents=True, exist_ok=True)
    with open(f".data/{CACHE_FILENAME}", "wb") as f:
        pickle.dump(cache, f)


def get_item_name(item_id: int) -> str:
    material = get_gamedata().materials_dict.get(item_id)
    if material:
        return material.name
    else:
        return "Unknown Item"
