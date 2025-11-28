from typing import List, Dict
import logging
from PySide6.QtCore import QObject, Slot, Signal, QThread, QRunnable

from api.models.gameData import Recipe, BuildingSpecialization
from api.gameData import get_item_name, get_building
from api.exchange import Exchange
from api.models.exchange import Listing

_logger = logging.getLogger(__name__)


class RecipeWorker(QObject):
    recipe_table_update_signal = Signal(Recipe, float)
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
            if self.abort:
                _logger.debug("RecipeWorker run method aborted.")
                break
            assert isinstance(recipe, Recipe)
            listing = Exchange.get_listing(recipe.output.id)
            if get_item_name(listing.id) == "TEMP":
                continue

            # Update tech level slider to the maximum
            building_specialization = get_building(recipe.producedIn).specialization
            if recipe.reqTech > self.tech_level_maximum.get(building_specialization, 1):
                self.tech_level_change_signal.emit(
                    building_specialization, recipe.reqTech
                )
                self.tech_level_maximum[building_specialization] = recipe.reqTech

            # Calculate profit per hour
            profit_per_hour = listing.current_price * recipe.output.am
            for material_amount in recipe.inputs:
                profit_per_hour -= (
                    Exchange.get_listing(material_amount.id).current_price
                    * material_amount.am
                )
            profit_per_hour = profit_per_hour / (recipe.timeMinutes / 60)

            self.recipe_table_update_signal.emit(recipe, profit_per_hour)

    def stop(self) -> None:
        _logger.debug("RecipeWorker stop method called.")
        self.abort = True
