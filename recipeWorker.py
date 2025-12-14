from typing import List, Dict
import logging
from PySide6.QtCore import QObject, Slot, Signal, QThread, QRunnable

from api.models.gameData import Recipe, BuildingSpecialization, Worker, WorkerType
from api.gameData import get_item_name, get_building, get_worker
from api.exchange import Exchange
from api.models.exchange import Listing

_logger = logging.getLogger(__name__)


class RecipeWorker(QObject):
    recipe_added_signal = Signal(Recipe)
    tech_level_change_signal = Signal(BuildingSpecialization, int)

    def __init__(self, recipies=List[Recipe], tech_level_maximum=None) -> None:
        self.recipies = recipies
        self.abort = False
        self.tech_level_maximum: Dict[BuildingSpecialization, int] = (
            {} if tech_level_maximum is None else tech_level_maximum
        )
        super().__init__(objectName="RecipeWorker")

    @Slot()
    def run(self) -> None:
        _logger.debug("RecipeWorker run method called.")
        recipe: Recipe
        for recipe in self.recipies:

            # Check for abort signal
            if self.abort:
                _logger.debug("RecipeWorker run method aborted.")
                break

            # Skip recipes with no inputs (e.g. raw material extraction)
            if len(recipe.inputs) == 0:
                continue

            assert isinstance(recipe, Recipe)
            listing = Exchange.get_listing(recipe.output.id)
            if get_item_name(listing.id) == "TEMP":
                continue

            # Calculate some stats
            building = get_building(recipe.producedIn)

            # Update tech level slider to the maximum
            if recipe.reqTech > self.tech_level_maximum.get(building.specialization, 1):
                self.tech_level_change_signal.emit(
                    building.specialization, recipe.reqTech
                )
                self.tech_level_maximum[building.specialization] = recipe.reqTech

            # Skip if inputs are unavailable
            for material_amount in recipe.inputs:
                material_price = Exchange.get_listing(material_amount.id).current_price
                if material_price < 1:
                    break
            if material_price < 1:
                continue

            self.recipe_added_signal.emit(recipe)

    def stop(self) -> None:
        _logger.debug("RecipeWorker stop method called.")
        self.abort = True
