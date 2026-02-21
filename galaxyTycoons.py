import logging
from typing import Dict
from PySide6.QtCore import QSize, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QTabWidget
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication

from api.models.company import Base
from baseUi import BaseWindow
import utils
from api.gameData import GameDataManager
from api.exchange import Exchange
from api.company import CompanyDataManager
from configurationUi import ConfigurationWindow
from recipeUi import RecipeWindow
from planetsUi import PlanetsWindow
from investmentsUi import InvestmentsWindow
from recipeWorker import RecipeWorker
from settings import Settings

_logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    fetch_company_signal = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Data Tool")
        self.resize(QSize(1400, 800))

        self.base_uis: Dict[int, BaseWindow] = {}

        # Create status bar
        self.statusBar().showMessage("Ready")

        # Load settings
        self.settings = Settings()

        # Create the Tab Widget
        self.tabs = QTabWidget()

        # Create and start RecipeWorker thread
        self.recipe_worker = RecipeWorker(
            GameDataManager.get().recipes, self.settings.tech_level_maximums
        )
        self.recipe_worker_thread = QThread(self)
        self.recipe_worker_thread.setObjectName("APIThread")
        self.recipe_worker.moveToThread(self.recipe_worker_thread)
        self.recipe_worker_thread.started.connect(self.recipe_worker.run)
        self.recipe_worker.finished.connect(self.recipe_worker.deleteLater)
        self.recipe_worker_thread.finished.connect(
            self.recipe_worker_thread.deleteLater
        )

        # Create company data manager on its own thread to avoid blocking
        self.company_data_manager = CompanyDataManager()
        self.company_thread = QThread(self)
        self.company_thread.setObjectName("CompanyThread")
        self.company_data_manager.moveToThread(self.company_thread)
        self.company_thread.finished.connect(self.company_data_manager.deleteLater)
        self.fetch_company_signal.connect(self.company_data_manager.fetch_company)

        # Initialize the sub-windows
        self.recipe_tab = RecipeWindow(self, self.recipe_worker, self.settings)
        # self.planets_tab = PlanetsWindow(self)
        self.investments_tab = InvestmentsWindow(self, self.settings)
        self.company_data_manager.base_loaded.connect(
            self.investments_tab.handle_base_loaded
        )
        self.company_data_manager.base_loaded.connect(self.handle_base_loaded)
        self.configuration_tab = ConfigurationWindow(
            self, self.settings, self.recipe_worker, self.company_data_manager
        )

        # Start background threads
        self.recipe_worker_thread.start()
        self.company_thread.start()

        # Add tabs
        self.tabs.addTab(self.recipe_tab, "Recipes & Profits")
        # self.tabs.addTab(self.planets_tab, "Planets")
        self.tabs.addTab(self.investments_tab, "Investments")
        self.tabs.addTab(self.configuration_tab, "Configuration")

        # Connect signals
        self.tabs.currentChanged.connect(self.handle_tab_change)
        # Get the Exchange instance and connect its signal
        exchange_instance = Exchange()
        exchange_instance.exchange_updated_signal.connect(
            self.investments_tab.handle_exchange_updated
        )
        # Tech Slider change should refresh investments
        self.recipe_tab.toolbox.techSliderChanged.connect(
            self.investments_tab.handle_tech_slider_changed
        )

        # Set the tabs as the central widget
        self.setCentralWidget(self.tabs)

        # Create menubar
        self._create_menubar()

        # Start loading company data
        self.fetch_company_signal.emit()

    @Slot(int)
    def handle_tab_change(self, index: int) -> None:
        # If index is Configuration or Investments tab
        if index == self.tabs.indexOf(
            self.configuration_tab
        ) or index == self.tabs.indexOf(self.investments_tab):
            _logger.debug("Emitting fetch_company_signal due to tab change.")
            self.fetch_company_signal.emit()

    @Slot(Base)
    def handle_base_loaded(self, base: Base) -> None:
        if base.id not in self.base_uis.keys():
            self.base_uis[base.id] = BaseWindow(
                self, self.company_data_manager.fetch_company()
            )
            self.tabs.addTab(self.base_uis[base.id], base.name)
            self.company_data_manager.base_loaded.connect(
                self.base_uis[base.id].handle_base_loaded
            )
            self.company_data_manager.company_loaded.connect(
                self.base_uis[base.id].handle_company_loaded
            )
            self.base_uis[base.id].update_tab_name_signal.connect(self.update_tab_name)
            self.base_uis[base.id].handle_base_loaded(base)

    @Slot(int, str)
    def update_tab_name(self, base_id: int, new_name: str) -> None:
        if base_id not in self.base_uis:
            _logger.warning(
                f"Received request to update tab name for unknown base_id {base_id}"
            )
            return
        index = self.tabs.indexOf(self.base_uis[base_id])
        if index == -1:
            _logger.warning(
                f"Received request to update tab name for base_id {base_id} but tab not found"
            )
            return
        self.tabs.setTabText(index, new_name)

    def _create_menubar(self) -> None:
        """Create the menubar with export options."""
        menubar = self.menuBar()

        tools_menu = menubar.addMenu("Tools")

        # Copy Listings
        copy_listings_action = QAction("Copy Listings to Clipboard", self)
        copy_listings_action.triggered.connect(self._copy_listings_to_clipboard)
        tools_menu.addAction(copy_listings_action)

        # Add separator
        tools_menu.addSeparator()

        # Add "All Consumables" checkbox
        self.all_consumables_action = QAction("All Consumables", self)
        self.all_consumables_action.setCheckable(True)
        self.all_consumables_action.setChecked(False)
        self.all_consumables_action.triggered.connect(self._toggle_all_consumables)
        tools_menu.addAction(self.all_consumables_action)

        # Add "Average price" checkbox
        self.average_price_action = QAction("Use Average Price", self)
        self.average_price_action.setCheckable(True)
        self.average_price_action.setChecked(False)
        self.average_price_action.triggered.connect(self._toggle_average_price)
        tools_menu.addAction(self.average_price_action)

    def _copy_listings_to_clipboard(self) -> None:
        """Copy all listings to clipboard in tab-separated format for Excel."""
        if not Exchange.listings:
            _logger.warning("No listings available to copy.")
            return

        lines = []

        # Sort listings by name for consistent output
        sorted_listings = sorted(
            Exchange.listings.values(), key=lambda listing: listing.name
        )

        # Add each listing
        for listing in sorted_listings:
            listing_price = (
                listing.average_price
                if utils.use_average_price
                else listing.current_price
            )
            lines.append(
                f"{listing.name}\t{listing_price / 100 if listing_price > 0 else 'N/A'}"
            )
            # lines.append(
            #     f"{listing.name}\t{GameDataManager.get().materials_dict[listing.id].weight}"
            # )

        # Join with newlines
        table_text = "\n".join(lines)

        # Copy to clipboard
        clipboard = QApplication.clipboard()
        clipboard.setText(table_text)

        _logger.info(f"Copied {len(sorted_listings)} listings to clipboard.")

    def _toggle_all_consumables(self, checked: bool) -> None:
        """Toggle the use of all consumables in profit calculations."""
        _logger.info(f"All consumables mode {'enabled' if checked else 'disabled'}")

        # Update the global flag in utils.py
        utils.use_all_consumables = checked

        self.statusBar().showMessage(
            f"Recalculating with {'all' if checked else 'optimal'} consumables..."
        )

        # Use a timer to allow the UI to update before starting the heavy calculation
        from PySide6.QtCore import QTimer

        QTimer.singleShot(100, self._perform_consumables_recalculation)

    def _toggle_average_price(self, checked: bool) -> None:
        """Toggle the use of average price in profit calculations."""
        _logger.info(f"Average price mode {'enabled' if checked else 'disabled'}")

        # Update the global flag in utils.py
        utils.use_average_price = checked

        self.statusBar().showMessage(
            f"Recalculating with {'average' if checked else 'current'} prices..."
        )

        # Use a timer to allow the UI to update before starting the heavy calculation
        from PySide6.QtCore import QTimer

        QTimer.singleShot(100, self._perform_consumables_recalculation)

    def _perform_consumables_recalculation(self) -> None:
        """Perform the actual recalculation after toggling consumables mode."""
        try:
            # Trigger recalculation of recipes
            self.recipe_tab.handle_exchange_updated()
            self.configuration_tab.handle_exchange_updated()
            self.investments_tab.handle_exchange_updated()
        finally:
            # Re-enable the action and clear status message
            self.statusBar().showMessage("Recalculation complete", 3000)

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        Ensure that closing the main window triggers the cleanup
        logic (saving settings, stopping threads) in the sub-widgets.
        """
        _logger.debug("MainWindow closeEvent called.")

        # Stop RecipeWorker thread
        _logger.debug("Stopping background threads.")
        self.recipe_worker_thread.requestInterruption()
        self.recipe_worker.wake_up()
        self.company_data_manager.request_stop()
        self.recipe_worker_thread.quit()
        self.company_thread.quit()
        if self.recipe_worker_thread.wait(5000):
            _logger.debug("RecipeWorker thread has stopped successfully.")
        else:
            _logger.debug("RecipeWorker thread did not stop in time.")
        if self.company_thread.wait(5000):
            _logger.debug("Company thread has stopped successfully.")
        else:
            _logger.debug("Company thread did not stop in time.")

        # Manually call closeEvent on tabs to trigger their specific cleanup logic
        self.recipe_tab.close()
        # self.planets_tab.close()
        self.investments_tab.close()
        self.configuration_tab.close()

        GameDataManager.save()
        Exchange.close()

        self.settings.save_settings()

        super().closeEvent(event)


if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication

    logging.basicConfig(
        format="%(asctime)s - %(levelname)s - %(message)s", level=logging.DEBUG
    )

    # Initialize Exchange as a QObject
    exchange_instance = Exchange()
    Exchange.load_cache()
    Exchange.update_listings()

    app = QApplication(sys.argv)

    main_window = MainWindow()
    main_window.show()

    sys.exit(app.exec())
