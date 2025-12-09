from typing import List, Dict
import logging
from pathlib import Path
import pickle
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
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
from PySide6.QtGui import QCloseEvent, QWheelEvent
from pyqtgraph import (
    PlotWidget,
    AxisItem,
    DateAxisItem,
    ViewBox,
    mkPen,
    functions,
    Point,
)

from utils import align_add
from api.gameData import get_gamedata, get_item_name, get_building
from api.models.gameData import Recipe, BuildingSpecialization
from api.exchange import Exchange
from api.models.exchange import Listing
from recipeWorker import RecipeWorker

CACHE_FILENAME = "settings.pkl"
CACHE_DIR = ".data"

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.DEBUG)


class Settings(BaseModel):
    tech_level_filters: Dict[BuildingSpecialization, int] = Field(default_factory=dict)
    tech_level_maximums: Dict[BuildingSpecialization, int] = Field(default_factory=dict)


class RecipeWindow(QWidget):
    class PriceGraph(PlotWidget):
        class FmtAxesItem(AxisItem):
            def tickStrings(self, values, scale, spacing):
                return [f"{v:,.0f}" for v in values]

        def __init__(self, parent=None, background="default", plotItem=None, **kargs):
            kargs["axisItems"] = {
                "bottom": DateAxisItem(),
                "left": RecipeWindow.PriceGraph.FmtAxesItem(orientation="left"),
                "right": RecipeWindow.PriceGraph.FmtAxesItem(orientation="right"),
            }
            super().__init__(parent, background, plotItem, **kargs)

            self.p1 = self.plotItem
            self.p1.getAxis("left").setLabel("Price", color="#00ff00")
            self.p1_pen = mkPen(color="#00ff00", width=2)

        def auto_range(self):
            # self.p2.enableAutoRange(axis="y")
            self.p1.vb.updateAutoRange()
            # self.p2.updateAutoRange()

            bounds = [np.inf, -np.inf]
            for item in self.p1.vb.addedItems:
                _bounds = item.dataBounds(0)
                if _bounds[0] is None or _bounds[1] is None:
                    continue
                bounds[0] = min(_bounds[0], bounds[0])
                bounds[1] = max(_bounds[1], bounds[1])
            if bounds[0] != np.inf and bounds[1] != -np.inf:
                self.p1.vb.setRange(xRange=bounds)

        def wheelEvent(self, ev, axis=None):
            super().wheelEvent(ev)
            vb = self.p1.vb
            if axis in (0, 1):
                mask = [False, False]
                mask[axis] = vb.state["mouseEnabled"][axis]
            else:
                mask = vb.state["mouseEnabled"][:]
            s = 1.02 ** (
                (ev.angleDelta().y() - ev.angleDelta().x())
                * vb.state["wheelScaleFactor"]
            )  # actual scaling factor
            s = [(None if m is False else s) for m in mask]
            center = Point(
                functions.invertQTransform(vb.childGroup.transform()).map(ev.position())
            )

            vb._resetTarget()
            vb.scaleBy(s, center)
            ev.accept()
            vb.sigRangeChangedManually.emit(mask)

        @Slot(Recipe)
        def plot_recipe(self, recipe: Recipe) -> None:
            self.p1.clear()
            listing = Exchange.get_listing(recipe.output.id)

            listing.average_price_history.sort_index(inplace=True)

            # Convert the index to Unix timestamps (numerical format)
            x_data = (
                pd.to_datetime(listing.average_price_history.index).astype("int64")
                // 10**9
            )

            # Ensure y_data is numeric
            y_data = (
                pd.to_numeric(listing.average_price_history["price"], errors="coerce")
                * recipe.output.am
                / (recipe.timeMinutes / 60)
            )

            # Plot the data
            self.p1.plot(
                x=np.asarray(x_data),
                y=np.asarray(y_data),
                pen=self.p1_pen,
                name="Purchases",
            )

            # Plot each ingredient price
            ingredient_subtotal = None
            for material_amount in recipe.inputs:
                material_listing = Exchange.get_listing(material_amount.id)
                material_listing.average_price_history.sort_index(inplace=True)
                price_history = material_listing.average_price_history.copy()
                price_history.index = (
                    pd.to_datetime(price_history.index).astype("int64") // 10**9
                )
                price_history["price"] = (
                    price_history["price"]
                    * material_amount.am
                    / (recipe.timeMinutes / 60)
                )

                self.p1.plot(
                    x=price_history.index.to_numpy(),
                    y=price_history["price"].to_numpy(),
                    pen=mkPen(width=1),
                    name=f"Ingredient: {material_listing.name}",
                )

                ingredient_subtotal = (
                    align_add(ingredient_subtotal, price_history["price"])
                    if ingredient_subtotal is not None
                    else price_history["price"]
                )

            p1_pen_dashed = mkPen(color="#00ff00", width=1, style=Qt.PenStyle.DashLine)
            self.p1.plot(
                x=ingredient_subtotal.index.to_numpy(),
                y=ingredient_subtotal.to_numpy(),
                pen=p1_pen_dashed,
                name="Ingredient Total",
            )

            self.auto_range()

    class RecipeTableModel(QAbstractTableModel):

        def __init__(self, parent: QObject):
            super().__init__(parent)
            self.table_data: List[List[str]] = []
            self.recipes: List[Recipe] = []
            self.header_data: List[str] = ["Recipe Output", "Profit / hr", "Tech Req."]

        def rowCount(self, /, parent: QModelIndex | QPersistentModelIndex = ...) -> int:
            return len(self.table_data)

        def columnCount(
            self, /, parent: QModelIndex | QPersistentModelIndex = ...
        ) -> int:
            return 3

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
            row.append(
                f"{get_building(recipe.producedIn).specialization.name} {recipe.reqTech}"
            )
            self.recipes.append(recipe)
            self.beginInsertRows(QModelIndex(), self.rowCount(), self.rowCount())
            self.table_data.append(row)
            self.endInsertRows()

    class RecipeTableView(QTableView):
        recipe_clicked = Signal(Recipe)

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

            self.clicked.connect(self.handle_table_clicked)

        @Slot(QModelIndex)
        def handle_table_clicked(self, index: QModelIndex) -> None:
            if not index.isValid():
                return
            proxy_model = self.model()
            source_index = proxy_model.mapToSource(index)
            source_model = proxy_model.sourceModel()
            assert isinstance(source_model, RecipeWindow.RecipeTableModel)
            recipe = source_model.recipes[source_index.row()]
            # _logger.debug(
            #     f"RecipeTableView clicked at row {index.row()}, column {index.column()}, recipe {get_item_name(recipe.output.id)}."
            # )
            self.recipe_clicked.emit(recipe)

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
            if recipe.reqTech <= self.tech_level_filters.get(
                building_specialization, 0
            ):
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
            class TechSlider(QSlider):
                def __init__(
                    self, orientation: Qt.Orientation, parent: QObject
                ) -> None:
                    super().__init__(orientation, parent)
                    self.setTickPosition(QSlider.TickPosition.TicksBelow)
                    self.setMinimum(0)
                    self.setMaximum(1)
                    self.setTickInterval(1)

                def wheelEvent(self, event: QWheelEvent) -> None:
                    event.ignore()

            def __init__(self, title: str, parent: QObject) -> None:
                super().__init__(title, parent)

                self.layout = QHBoxLayout()

                self.slider = self.TechSlider(Qt.Orientation.Horizontal, self)
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

        def __init__(self, parent: QObject, settings: Settings) -> None:
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

            self.set_tech_level_sliders(settings)

        @Slot()
        def handle_tech_level_change(
            self, specialization: BuildingSpecialization, max_tech_level: int
        ) -> None:
            self.tech_widgets[specialization].set_maximum(max_tech_level)

        def get_tech_level_maximums(self) -> Dict[BuildingSpecialization, int]:
            tech_level_maximums: Dict[BuildingSpecialization, int] = {}
            for specialization, tech_widget in self.tech_widgets.items():
                tech_level_maximums[specialization] = tech_widget.slider.maximum()
            return tech_level_maximums

        def set_tech_level_sliders(self, settings: Settings) -> None:
            for specialization, max_level in settings.tech_level_maximums.items():
                self.tech_widgets[specialization].set_maximum(max_level)
            for specialization, filter_level in settings.tech_level_filters.items():
                self.tech_widgets[specialization].slider.setValue(filter_level)

    def __init__(self, parent: QObject) -> None:
        cache_path = Path(CACHE_DIR) / CACHE_FILENAME
        if cache_path.exists():
            try:
                with cache_path.open("rb") as f:
                    settings = pickle.load(f)
                _logger.debug(f"Loaded settings from {cache_path}.")
            except (IOError, pickle.UnpicklingError) as e:
                _logger.error(f"Error loading settings from {cache_path}: {e}")
                settings = Settings()
        else:
            settings = Settings()

        super().__init__(parent)
        self.main_layout = QHBoxLayout()
        self.setLayout(self.main_layout)

        self.toolbox = RecipeWindow.FilterToolbox(self, settings)
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
        for specialization, max_level in settings.tech_level_filters.items():
            self.recipe_table_proxy_model.set_tech_level_filter(
                specialization, max_level
            )
        self.recipe_table_view.setModel(self.recipe_table_proxy_model)
        self.main_layout.addWidget(self.recipe_table_view)

        self.plot = RecipeWindow.PriceGraph(self)
        self.main_layout.addWidget(self.plot)

        self.recipe_table_view.recipe_clicked.connect(self.plot.plot_recipe)

        self.main_layout.setStretchFactor(self.recipe_table_view, 1)
        self.main_layout.setStretchFactor(self.plot, 1)

        self.recipe_worker = RecipeWorker(
            get_gamedata().recipes, settings.tech_level_maximums
        )
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

        _logger.debug("Saving settings.")
        settings = Settings()
        settings.tech_level_filters = self.recipe_table_proxy_model.tech_level_filters
        settings.tech_level_maximums = self.toolbox.get_tech_level_maximums()
        cache_path = Path(CACHE_DIR) / CACHE_FILENAME
        Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
        try:
            with cache_path.open("wb") as f:
                pickle.dump(settings, f)
            _logger.debug(f"Settings saved to {cache_path}.")
        except IOError as e:
            _logger.error(f"Error saving settings to {cache_path}: {e}")
            raise
        except Exception as e:
            _logger.error(f"Unexpected error saving settings: {e}")
            raise
