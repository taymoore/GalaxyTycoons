import logging
from PySide6.QtCore import QSize, QEvent
from PySide6.QtWidgets import QMainWindow, QTabWidget

from api.gameData import get_gamedata, save_gamedata
from api.exchange import Exchange
from recipeUi import RecipeWindow
from planetsUi import PlanetsUi

_logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Data Tool")
        self.resize(QSize(1400, 800))

        # Create the Tab Widget
        self.tabs = QTabWidget()

        # Initialize the sub-windows
        self.recipe_tab = RecipeWindow(self)
        self.planets_tab = PlanetsUi(self)

        # Add tabs
        self.tabs.addTab(self.recipe_tab, "Recipes & Profits")
        self.tabs.addTab(self.planets_tab, "Planets")

        # Set the tabs as the central widget
        self.setCentralWidget(self.tabs)

    def closeEvent(self, event: QEvent) -> None:
        """
        Ensure that closing the main window triggers the cleanup
        logic (saving settings, stopping threads) in the sub-widgets.
        """
        _logger.debug("MainWindow closeEvent called.")

        # Manually call closeEvent on tabs to trigger their specific cleanup logic
        self.recipe_tab.close()
        self.planets_tab.close()

        save_gamedata()
        Exchange.close()

        super().closeEvent(event)


if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication

    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG
    )

    UPDATE_LISTINGS_ON_LOAD = False
    Exchange.load_cache()
    if not UPDATE_LISTINGS_ON_LOAD:
        from datetime import datetime

        Exchange.updated_time = datetime.now()
    Exchange.update_listings()

    app = QApplication(sys.argv)

    main_window = MainWindow()
    main_window.show()

    sys.exit(app.exec())
