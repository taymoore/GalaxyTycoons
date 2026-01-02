import logging
from pathlib import Path
import pickle
from typing import Dict, List, Any
from PySide6.QtCore import QObject, Slot
from pydantic import BaseModel, Field

from api.models.gameData import Specialization

CACHE_FILENAME = "settings.pkl"
CACHE_DIR = ".data"

_logger = logging.getLogger(__name__)

class Settings(QObject):
    class SettingsData(BaseModel):
        tech_level_filters: Dict[Specialization, int] = Field(default_factory=dict)
        tech_level_maximums: Dict[Specialization, int] = Field(default_factory=dict)
        configurations: List[Dict[str, Any]] = Field(default_factory=list)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = self._load_settings()
    
    @property
    def tech_level_filters(self) -> Dict[Specialization, int]:
        return self._data.tech_level_filters
    
    @tech_level_filters.setter
    def tech_level_filters(self, value: Dict[Specialization, int]):
        self._data.tech_level_filters = value
    
    @property
    def tech_level_maximums(self) -> Dict[Specialization, int]:
        return self._data.tech_level_maximums
    
    @tech_level_maximums.setter
    def tech_level_maximums(self, value: Dict[Specialization, int]):
        self._data.tech_level_maximums = value
    
    @property
    def configurations(self) -> List[Dict[str, Any]]:
        return self._data.configurations
    
    @configurations.setter
    def configurations(self, value: List[Dict[str, Any]]):
        self._data.configurations = value

    @Slot(Specialization, int)
    def set_tech_level_filter(
        self, specialization: Specialization, tech_level: int
    ) -> None:
        self._data.tech_level_filters[specialization] = tech_level
    
    @Slot(list, list)
    def set_tech_level_filters(
        self,
        specializations: List[Specialization],
        tech_levels: List[int]
    ) -> None:
        for specialization, tech_level in zip(specializations, tech_levels):
            self._data.tech_level_filters[specialization] = tech_level
        
    def _load_settings(self) -> SettingsData:
        """Load settings from cache file"""
        cache_path = Path(CACHE_DIR) / CACHE_FILENAME
        if cache_path.exists():
            try:
                with cache_path.open("rb") as f:
                    settings_data = pickle.load(f)
                _logger.debug(f"Settings loaded from {cache_path}.")
                return settings_data
            except (IOError, pickle.UnpicklingError) as e:
                _logger.error(f"Error loading settings from {cache_path}: {e}")
        return Settings.SettingsData()
    
    @Slot()
    def save_settings(self) -> None:
        """Save settings to cache file"""
        cache_path = Path(CACHE_DIR) / CACHE_FILENAME
        Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
        try:
            with cache_path.open("wb") as f:
                pickle.dump(self._data, f)
            _logger.debug(f"Settings saved to {cache_path}.")
        except IOError as e:
            _logger.error(f"Error saving settings to {cache_path}: {e}")
            raise
        except Exception as e:
            _logger.error(f"Unexpected error saving settings: {e}")
            raise
