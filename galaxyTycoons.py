import logging
from pathlib import Path
import pickle
from tkinter import SE
from PySide6.QtCore import QSize, QThread
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QTabWidget

from api.gameData import save_gamedata, get_gamedata
from api.exchange import Exchange
from configurationUi import ConfigurationWindow
from recipeUi import RecipeWindow
from planetsUi import PlanetsWindow
from investmentsUi import InvestmentsWindow
from recipeWorker import RecipeWorker
from settings import Settings

_logger = logging.getLogger(__name__)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Data Tool")
        self.resize(QSize(1400, 800))

        # Load settings
        self.settings = Settings()

        # Create the Tab Widget
        self.tabs = QTabWidget()

        # Create and start RecipeWorker thread
        self.recipe_worker = RecipeWorker(get_gamedata().recipes, self.settings.tech_level_maximums)
        self.recipe_worker_thread = QThread(self)
        self.recipe_worker_thread.setObjectName("RecipeWorkerThread")
        self.recipe_worker.moveToThread(self.recipe_worker_thread)
        self.recipe_worker_thread.started.connect(self.recipe_worker.run)
        self.recipe_worker.finished.connect(self.recipe_worker.deleteLater)
        self.recipe_worker_thread.finished.connect(
            self.recipe_worker_thread.deleteLater
        )

        # Initialize the sub-windows
        self.recipe_tab = RecipeWindow(self, self.recipe_worker, self.settings)
        self.planets_tab = PlanetsWindow(self)
        self.investments_tab = InvestmentsWindow(self)
        self.configuration_tab = ConfigurationWindow(self, self.settings)

        # Start the recipe worker thread
        self.recipe_worker_thread.start()

        # Add tabs
        self.tabs.addTab(self.recipe_tab, "Recipes & Profits")
        self.tabs.addTab(self.planets_tab, "Planets")
        self.tabs.addTab(self.investments_tab, "Investments")
        self.tabs.addTab(self.configuration_tab, "Configuration")

        # Set the tabs as the central widget
        self.setCentralWidget(self.tabs)

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        Ensure that closing the main window triggers the cleanup
        logic (saving settings, stopping threads) in the sub-widgets.
        """
        _logger.debug("MainWindow closeEvent called.")

        # Stop RecipeWorker thread
        _logger.debug("Stopping RecipeWorker thread.")
        self.recipe_worker_thread.requestInterruption()
        self.recipe_worker.wake_up()
        self.recipe_worker_thread.quit()
        if self.recipe_worker_thread.wait(5000):
            _logger.debug("RecipeWorker thread has stopped successfully.")
        else:
            _logger.debug("RecipeWorker thread did not stop in time.")

        # Manually call closeEvent on tabs to trigger their specific cleanup logic
        self.recipe_tab.close()
        self.planets_tab.close()
        self.investments_tab.close()
        self.configuration_tab.close()

        save_gamedata()
        Exchange.close()

        self.settings.save_settings()

        super().closeEvent(event)


if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication

    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG
    )

    UPDATE_LISTINGS_ON_LOAD = True
    Exchange.load_cache()
    if not UPDATE_LISTINGS_ON_LOAD:
        from datetime import datetime

        Exchange.updated_time = datetime.now()
    Exchange.update_listings()

    app = QApplication(sys.argv)

    main_window = MainWindow()
    main_window.show()

    sys.exit(app.exec())
