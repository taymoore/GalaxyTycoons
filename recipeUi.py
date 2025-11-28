from typing import List, Dict
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
    QHBoxLayout,
    QGroupBox,
    QWidget,
    QToolBox,
    QSlider,
    QSizePolicy,
    QLabel,
)
from PySide6.QtGui import QCloseEvent

from api.gameData import get_gamedata, get_item_name, get_building
from api.models.gameData import Recipe, BuildingSpecialization
from api.models.exchange import Listing
from recipeWorker import RecipeWorker

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.DEBUG)


class RecipeWindow(QWidget):
    class RecipeTableModel(QAbstractTableModel):
        def __init__(self, parent: QObject):
            super().__init__(parent)
            self.table_data: List[List[str]] = []
            self.recipes: List[Recipe] = []
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
            data = self.table_data[row][column]
            if role == Qt.ItemDataRole.DisplayRole:
                if column == 1:
                    data = "{:,.2f}".format(data / 100) if data != -1 else ""
                return data
            elif role == Qt.ItemDataRole.UserRole:
                return data

        @Slot(Recipe, Listing)
        def handle_recipe_table_update(
            self, recipe: Recipe, profit_per_hour: float
        ) -> None:
            row = []
            row.append(get_item_name(recipe.output.id))
            row.append(profit_per_hour)
            self.recipes.append(recipe)
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
            self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.setSortingEnabled(True)
            self.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    class RecipeTableProxyModel(QSortFilterProxyModel):
        def __init__(self, parent: QObject | None):
            super().__init__(parent)
            self.setDynamicSortFilter(True)
            self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.sort(1, Qt.SortOrder.DescendingOrder)
            self.tech_level_filters: Dict[BuildingSpecialization, int] = {}

        def lessThan(self, source_left: QModelIndex, source_right: QModelIndex) -> bool:
            if source_left.column() == 1:
                left = self.sourceModel().data(source_left, Qt.ItemDataRole.UserRole)
                right = self.sourceModel().data(source_right, Qt.ItemDataRole.UserRole)
                return left < right
            return super().lessThan(source_left, source_right)

        def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
            source_model = self.sourceModel()
            assert isinstance(source_model, RecipeWindow.RecipeTableModel)
            recipe = source_model.recipes[source_row]
            building_specialization = get_building(recipe.producedIn).specialization
            if recipe.reqTech <= self.tech_level_filters.get(building_specialization):
                return super().filterAcceptsRow(source_row, source_parent)
            return False

        @Slot()
        def set_tech_level_filter(
            self, specialization: BuildingSpecialization, max_tech_level: int
        ) -> None:
            self.tech_level_filters[specialization] = max_tech_level
            self.invalidateFilter()

    class FilterToolbox(QToolBox):
        class TechFilterWidget(QGroupBox):
            def __init__(self, title: str, parent: QObject) -> None:
                super().__init__(title, parent)

                self.layout = QHBoxLayout()

                self.slider = QSlider(Qt.Orientation.Horizontal, self)
                self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
                self.slider.setMinimum(1)
                self.slider.setMaximum(1)
                self.slider.setTickInterval(1)
                self.layout.addWidget(self.slider)

                self.label = QLabel("1", self)
                self.layout.addWidget(self.label)

                self.slider.valueChanged.connect(self.handle_slider_change)

                self.setLayout(self.layout)

            def set_maximum(self, max_value: int) -> None:
                self.slider.setMaximum(max_value)
                self.slider.setValue(max_value)

            def handle_slider_change(self, value: int) -> None:
                self.label.setText(str(value))

        def __init__(self, parent: QObject) -> None:
            super().__init__(parent)

            # Tech Filter
            tech_filter_widget = QWidget(self)
            tech_filter_layout = QVBoxLayout()
            tech_filter_widget.setLayout(tech_filter_layout)

            self.tech_widgets: Dict[
                BuildingSpecialization, RecipeWindow.FilterToolbox.TechFilterWidget
            ] = {}
            for specialization in BuildingSpecialization:
                if specialization == BuildingSpecialization.NONE:
                    continue
                tech_widget = RecipeWindow.FilterToolbox.TechFilterWidget(
                    BuildingSpecialization(specialization).name.title(), self
                )
                self.tech_widgets[specialization] = tech_widget
                tech_filter_layout.addWidget(tech_widget)
            self.addItem(tech_filter_widget, "Tech Level")
            self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            self.setMinimumWidth(130)

        @Slot()
        def handle_tech_level_change(
            self, specialization: BuildingSpecialization, max_tech_level: int
        ) -> None:
            self.tech_widgets[specialization].set_maximum(max_tech_level)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.main_layout = QHBoxLayout()
        self.setLayout(self.main_layout)

        self.toolbox = RecipeWindow.FilterToolbox(self)
        self.toolbox.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        self.main_layout.addWidget(self.toolbox)
        self.main_layout.setStretchFactor(self.toolbox, 0)

        self.recipe_table_model = RecipeWindow.RecipeTableModel(self)
        self.recipe_table_view = RecipeWindow.RecipeTableView(self)
        self.recipe_table_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.recipe_table_proxy_model = RecipeWindow.RecipeTableProxyModel(self)
        self.recipe_table_proxy_model.setSourceModel(self.recipe_table_model)
        for specialization, tech_widget in self.toolbox.tech_widgets.items():
            tech_widget.slider.valueChanged.connect(
                lambda value, spec=specialization: self.recipe_table_proxy_model.set_tech_level_filter(
                    spec, value
                )
            )
        self.recipe_table_view.setModel(self.recipe_table_proxy_model)
        self.main_layout.addWidget(self.recipe_table_view)
        self.main_layout.setStretchFactor(self.recipe_table_view, 1)

        self.recipe_worker = RecipeWorker(get_gamedata().recipes)
        self.recipe_worker.tech_level_change_signal.connect(
            self.toolbox.handle_tech_level_change
        )

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
