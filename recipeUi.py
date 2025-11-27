from typing import List
import logging
from PySide6.QtCore import (
    Slot,
    Signal,
    QThread,
    Qt,
    QAbstractTableModel,
    QSortFilterProxyModel,
    QObject,
    QModelIndex,
    QPersistentModelIndex,
)

from PySide6.QtWidgets import (
    QHeaderView,
    QTableView,
    QAbstractItemView,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QCloseEvent

from api.gameData import get_gamedata, get_item_name
from api.models.gameData import Recipe
from api.models.exchange import Listing
from recipeWorker import RecipeWorker

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.DEBUG)


class RecipeWindow(QWidget):
    class RecipeTableModel(QAbstractTableModel):
        def __init__(self, parent: QObject):
            super().__init__(parent)
            self.table_data: List[List[str]] = []
            self.header_data: List[str] = ["Recipe Output", "Profit / hr"]

        def rowCount(self, /, parent: QModelIndex | QPersistentModelIndex = ...) -> int:
            return len(self.table_data)

        def columnCount(
            self, /, parent: QModelIndex | QPersistentModelIndex = ...
        ) -> int:
            return 2

        def headerData(
            self,
            section: int,
            orientation: Qt.Orientation,
            role: int = Qt.ItemDataRole.DisplayRole,
        ) -> object | None:
            if role == Qt.ItemDataRole.DisplayRole:
                if orientation == Qt.Orientation.Horizontal:
                    return self.header_data[section]

        def data(
            self,
            /,
            index: QModelIndex | QPersistentModelIndex,
            role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
        ) -> object | None:
            row = index.row()
            column = index.column()
            if role == Qt.ItemDataRole.DisplayRole:
                if column == 1:
                    data = "{:,.2f}".format(data / 100) if data != -1 else ""
                return data
            elif role == Qt.ItemDataRole.UserRole:
                return self.table_data[row][column]

        @Slot(Recipe, Listing)
        def handle_recipe_table_update(self, recipe: Recipe, listing: Listing) -> None:
            row = []
            row.append(get_item_name(recipe.output.id))
            row.append(listing.current_price)
            self.beginInsertRows(QModelIndex(), self.rowCount(), self.rowCount())
            self.table_data.append(row)
            self.endInsertRows()

    class RecipeTableView(QTableView):
        def __init__(self, parent):
            super().__init__(parent)
            self.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )
            self.verticalHeader().hide()
            self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
            self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.setSortingEnabled(True)
            self.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    class RecipeTableProxyModel(QSortFilterProxyModel):
        def __init__(self, parent: QObject | None):
            super().__init__(parent)
            self.setDynamicSortFilter(True)
            self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.sort(0, Qt.SortOrder.AscendingOrder)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.main_layout = QVBoxLayout()
        self.setLayout(self.main_layout)

        self.recipe_table_model = RecipeWindow.RecipeTableModel(self)
        self.recipe_table_view = RecipeWindow.RecipeTableView(self)
        # self.recipe_table_view.setModel(self.recipe_table_model)
        self.recipe_table_proxy_model = RecipeWindow.RecipeTableProxyModel(self)
        self.recipe_table_proxy_model.setSourceModel(self.recipe_table_model)
        self.recipe_table_view.setModel(self.recipe_table_proxy_model)
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

    def closeEvent(self, event: QCloseEvent) -> None:
        _logger.debug("Closing RecipeWindow, stopping worker thread.")
        self.recipe_worker_thread.quit()
        if self.recipe_worker_thread.wait():
            _logger.debug("Worker thread has stopped successfully.")
        else:
            _logger.debug("Worker thread did not stop in time.")
