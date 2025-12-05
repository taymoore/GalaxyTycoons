from typing import List, Dict
import logging
from PySide6.QtCore import QObject, Slot, Signal, QThread, QRunnable

from api.models.gameData import Recipe, BuildingSpecialization, Worker, WorkerType
from api.gameData import get_item_name, get_building, get_worker
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

            # Calculate some stats
            building = get_building(recipe.producedIn)

            # Update tech level slider to the maximum
            if recipe.reqTech > self.tech_level_maximum.get(building.specialization, 1):
                self.tech_level_change_signal.emit(
                    building.specialization, recipe.reqTech
                )
                self.tech_level_maximum[building.specialization] = recipe.reqTech

            # Calculate worker cost
            worker_type: WorkerType
            worker_cost_per_hour = 0.0
            for worker_type, worker_count in enumerate(
                building.workersNeeded or [], start=1
            ):
                if worker_count == 0:
                    continue
                worker = get_worker(worker_type)
                for consumable in worker.consumables:
                    if consumable.essential is False:
                        continue
                    consumable_listing = Exchange.get_listing(consumable.matId)
                    if consumable_listing.current_price < 1:
                        _logger.warning(
                            f"No price data for consumable {consumable_listing.name} ({consumable.matId}), skipping worker cost calculation for recipe {get_item_name(recipe.output.id)} ({recipe.id})."
                        )
                        continue
                    worker_cost_per_hour += (
                        consumable_listing.current_price
                        * consumable.amount
                        * worker_count
                        / 24000
                    )

            # Calculate profit per hour
            profit_per_hour = listing.current_price * recipe.output.am
            for material_amount in recipe.inputs:
                material_price = Exchange.get_listing(material_amount.id).current_price
                if material_price < 1:
                    profit_per_hour = 0
                    break
                profit_per_hour -= material_price * material_amount.am
            profit_per_hour = profit_per_hour / (recipe.timeMinutes / 60)

            profit_per_hour -= worker_cost_per_hour

            self.recipe_table_update_signal.emit(recipe, profit_per_hour)

    def stop(self) -> None:
        _logger.debug("RecipeWorker stop method called.")
        self.abort = True
