import logging
from PySide6.QtCore import (
    Slot,
    Signal,
    QThread,
    Qt,
    QCoreApplication,
    QAbstractTableModel,
    QSortFilterProxyModel,
    QObject,
    QModelIndex,
    QPersistentModelIndex,
    QSize,
)

from PySide6.QtWidgets import (
    QHeaderView,
    QTableView,
    QAbstractItemView,
    QApplication,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QTextEdit,
    QTableWidget,
    QTabWidget,
)

from api.gameData import get_gamedata
from api.models.gameData import Recipe
from api.models.exchange import Listing
from recipeWorker import RecipeWorker

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.DEBUG)


class RecipeWindow(QWidget):
    class RecipeTableModel(QAbstractTableModel):
        def __init__(self, parent: QObject):
            super().__init__(parent)

        def rowCount(self, /, parent: QModelIndex | QPersistentModelIndex = ...) -> int:
            return 0

        def columnCount(
            self, /, parent: QModelIndex | QPersistentModelIndex = ...
        ) -> int:
            return 0

        def data(
            self,
            /,
            index: QModelIndex | QPersistentModelIndex,
            role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
        ) -> object | None:
            return None

        @Slot(Recipe, Listing)
        def handle_recipe_table_update(self, recipe: Recipe, listing: Listing) -> None:
            _logger.debug(
                f"Received recipe update for recipe id {recipe.id} with listing {listing}."
            )
            # Here you would update the model's internal data structure and emit the necessary signals
            # to notify the view of the changes.

    class RecipeTableView(QTableView):
        def __init__(self, parent):
            super().__init__(parent)
            self.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.verticalHeader().hide()
            self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    class RecipeTableProxyModel(QSortFilterProxyModel):
        def __init__(self, parent: QObject | None):
            super().__init__(parent)
            self.setDynamicSortFilter(True)
            self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        self.recipe_table_model = RecipeWindow.RecipeTableModel(self)
        self.recipe_table_view = RecipeWindow.RecipeTableView(self)
        self.recipe_table_proxy_model = RecipeWindow.RecipeTableProxyModel(self)
        self.main_layout.addWidget(self.recipe_table_view)

        self.recipe_worker = RecipeWorker(get_gamedata().recipes)
        self.recipe_worker_thread = QThread(self)
        self.recipe_worker_thread.setObjectName("RecipeWorkerThread")
        self.recipe_worker.moveToThread(self.recipe_worker_thread)
        self.recipe_worker_thread.started.connect(self.recipe_worker.run)
        self.recipe_worker_thread.finished.connect(self.recipe_worker.deleteLater)

        self.recipe_worker.recipe_table_update_signal.connect(
            self.recipe_table_model.handle_recipe_table_update
        )

        self.recipe_worker_thread.start()

    # @Slot()
    # def stop_worker_thread(self) -> None:
    #     self.recipe_worker_thread.quit()
    #     self.recipe_worker_thread.wait()

    # @Slot()
    # def cleanup_worker_thread(self) -> None:
    #     if self.recipe_worker_thread.isRunning():
    #         self.stop_worker_thread()
    #     self.recipe_worker_thread.deleteLater()
    #     self.recipe_worker.deleteLater()

    def closeEvent(self, event):
        # self.cleanup_worker_thread()
        _logger.debug("Closing RecipeWindow, stopping worker thread.")
        self.recipe_worker_thread.quit()
        if self.recipe_worker_thread.wait():
            _logger.debug("Worker thread has stopped successfully.")
        else:
            _logger.debug("Worker thread did not stop in time.")
