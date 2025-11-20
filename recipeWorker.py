from typing import List
import logging
from PySide6.QtCore import QObject, Slot, Signal, QThread, QRunnable

from api.models.gameData import Recipe
from api.exchange import Exchange
from api.models.exchange import Listing

_logger = logging.getLogger(__name__)


class RecipeWorker(QObject):
    recipe_table_update_signal = Signal(Recipe, Listing)

    def __init__(self, recipies=List[Recipe]):
        self.recipies = recipies
        self.abort = False
        super().__init__(objectName="RecipeWorker")

    @Slot()
    def run(self) -> None:
        _logger.debug("RecipeWorker run method called.")
        for recipe in self.recipies:
            if self.abort:
                _logger.debug("RecipeWorker run method aborted.")
                break
            assert isinstance(recipe, Recipe)
            listing = Exchange.get_listing(recipe.output.id)
            self.recipe_table_update_signal.emit(recipe, listing)

    def stop(self) -> None:
        _logger.debug("RecipeWorker stop method called.")
        self.abort = True
