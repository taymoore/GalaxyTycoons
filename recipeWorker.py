from typing import List, Dict
import logging
from PySide6.QtCore import QObject, Slot, Signal, QThread, QRunnable, QSemaphore

from api.models.gameData import Recipe, BuildingSpecialization, RecipeType, Worker, WorkerType
from api.gameData import get_item_name, get_building, get_worker
from api.exchange import Exchange
from api.models.exchange import Listing

FETCH_LISTING_INTERVAL_MS = 1000 * 60 * 15  # 15 minutes

_logger = logging.getLogger(__name__)


class RecipeWorker(QObject):
    recipe_added_signal = Signal(Recipe)
    exchange_updated_signal = Signal(dict)
    tech_level_change_signal = Signal(BuildingSpecialization, int)
    finished = Signal()

    def __init__(self, recipies=List[Recipe], tech_level_maximum=None) -> None:
        self.recipies = recipies
        self.wake_semaphore = QSemaphore(0)
        self.tech_level_maximum: Dict[BuildingSpecialization, int] = (
            {} if tech_level_maximum is None else tech_level_maximum
        )
        super().__init__(objectName="RecipeWorker")

    @Slot()
    def run(self) -> None:
        _logger.debug("RecipeWorker run method called.")
        recipe: Recipe
        current_thread = QThread.currentThread()
        for recipe in self.recipies:

            # Check for abort signal
            if current_thread.isInterruptionRequested():
                _logger.debug(
                    "RecipeWorker run method aborted during recipe processing."
                )
                break

            # Skip recipes with no inputs (e.g. raw material extraction)
            if len(recipe.inputs) == 0 or recipe.type == RecipeType.EXTRACTION:
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

            self.recipe_added_signal.emit(recipe)

        # Fetch worker listings on timer
        while not current_thread.isInterruptionRequested():
            _logger.debug(
                "RecipeWorker updating exchange listings in background thread."
            )
            # Check for abort signal
            if self.wake_semaphore.tryAcquire(1, FETCH_LISTING_INTERVAL_MS):
                _logger.debug("RecipeWorker run method aborted during listing update.")
                break
            Exchange.update_listings()
            self.exchange_updated_signal.emit(Exchange.listings)
        _logger.debug("RecipeWorker run method finished.")
        self.finished.emit()

    def wake_up(self) -> None:
        self.wake_semaphore.release(1)
