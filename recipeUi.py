from typing import List, Dict
import logging
import itertools
from pathlib import Path
import pickle
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from PySide6.QtCore import (
    Slot,
    Signal,
    QThread,
    QSize,
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
    QCheckBox,
    QSplitter,
)
from PySide6.QtGui import QCloseEvent, QWheelEvent
import pyqtgraph as pg
import matplotlib.colors as mcolors
import matplotlib.cm as cm

from utils import align_add
from api.gameData import get_gamedata, get_item_name, get_building, get_worker
from api.models.gameData import Recipe, BuildingSpecialization, Building, WorkerType
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
    class PriceGraph(pg.PlotWidget):
        class FmtAxesItem(pg.AxisItem):
            def tickStrings(self, values, scale, spacing):
                return [f"{v:,.0f}" for v in values]

        def __init__(self, parent=None, background="default", plotItem=None, **kargs):
            kargs["axisItems"] = {
                "bottom": pg.DateAxisItem(),
                "left": RecipeWindow.PriceGraph.FmtAxesItem(orientation="left"),
                "right": RecipeWindow.PriceGraph.FmtAxesItem(orientation="right"),
            }
            super().__init__(parent, background, plotItem, **kargs)

            self.p1 = self.plotItem
            self.p1.getAxis("left").setLabel("Profit/hr", color="#00ff00")
            self.p1_pen = pg.mkPen(color="#00ff00", width=2)

            # Label to display the nearest point's value
            self.label = pg.LabelItem(justify="right", color="#00ff00")
            scene = self.scene()
            assert isinstance(scene, pg.GraphicsScene)
            scene.addItem(self.label)

            # Enable mouse tracking
            scene.sigMouseMoved.connect(self.on_mouse_moved)

            # Store plotted data points for nearest-point calculation
            self.data_points = []
            self.listing_price = []

        def auto_range(self):
            self.p1.vb.updateAutoRange()

            bounds = [np.inf, -np.inf]
            for item in self.p1.vb.addedItems:
                if not isinstance(item, pg.PlotDataItem):
                    continue
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
            center = pg.Point(
                pg.functions.invertQTransform(vb.childGroup.transform()).map(
                    ev.position()
                )
            )

            vb._resetTarget()
            vb.scaleBy(s, center)
            ev.accept()
            vb.sigRangeChangedManually.emit(mask)

        @Slot(Recipe)
        def plot_recipe(self, recipe: Recipe) -> None:
            self.p1.clear()
            self.p1.vb.enableAutoRange()

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
                / (100 * recipe.timeMinutes / 60)
            )

            # Store data points for nearest-point calculation
            self.data_points = list(zip(x_data, y_data))
            self.listing_price = listing.average_price_history["price"].to_numpy() / 100

            # Plot the data
            self.p1.plot(
                x=np.asarray(x_data),
                y=np.asarray(y_data),
                pen=self.p1_pen,
                name="Profit",
            )
            # self.label.setText(
            #     f"Market Price: {self.listing_price[-1]:,.2f}<br>Profit/hr: {y_data.iloc[-1]:,.2f}<br>Time: {pd.to_datetime(x_data[-1], unit='s')}"
            # )
            # self.label.setPos(
            #     pg.Point(x_data[-1], y_data.iloc[-1])
            #     - self.label.boundingRect().topRight()
            # )

            # Plot each ingredient price
            ingredient_subtotal = None
            colormap = cm.get_cmap("tab10")
            num_ingredients = len(recipe.inputs)
            ingredient_colors = [
                mcolors.to_hex(colormap(i / num_ingredients))
                for i in range(num_ingredients)
            ]
            for material_idx, material_amount in enumerate(recipe.inputs):
                material_listing = Exchange.get_listing(material_amount.id)
                material_listing.average_price_history.sort_index(inplace=True)
                price_history = material_listing.average_price_history.copy()
                price_history.index = (
                    pd.to_datetime(price_history.index).astype("int64") // 10**9
                )
                price_history["price"] = (
                    price_history["price"]
                    * material_amount.am
                    / (100 * recipe.timeMinutes / 60)
                )

                # Assign a unique color to each ingredient
                ingredient_color = ingredient_colors[material_idx]
                pen = pg.mkPen(color=ingredient_color, width=1)

                x = price_history.index.to_numpy()
                y = price_history["price"].to_numpy()
                self.p1.plot(
                    x=x,
                    y=y,
                    pen=pen,
                    name=f"Ingredient: {material_listing.name}",
                )

                label = pg.TextItem(
                    text=f"{material_listing.name}\nCost/hr: {y[-1]:,.2f}",
                    color=ingredient_color,
                    anchor=(1, 1),
                )
                label.setPos(x[-1], y[-1])
                self.p1.addItem(label)

                ingredient_subtotal = (
                    align_add(ingredient_subtotal, price_history["price"])
                    if ingredient_subtotal is not None
                    else price_history["price"]
                )

            p1_pen_dashed = pg.mkPen(
                color="#00ff00", width=1, style=Qt.PenStyle.DashLine
            )
            x = ingredient_subtotal.index.to_numpy()
            y = ingredient_subtotal.to_numpy()
            self.p1.plot(
                x=x,
                y=y,
                pen=p1_pen_dashed,
                name="Ingredient Total",
            )
            label = pg.TextItem(
                text=f"Total Cost/hr: {y[-1]:,.2f}",
                color="#00ff00",
                anchor=(1, 1),
            )
            label.setPos(x[-1], ingredient_subtotal[x[-1]])
            self.p1.addItem(label)

            self.auto_range()

        def on_mouse_moved(self, pos):
            # Convert mouse position to plot coordinates
            mouse_point = self.p1.vb.mapSceneToView(pos)
            mouse_x, mouse_y = mouse_point.x(), mouse_point.y()

            # Find the nearest data point
            if not self.data_points:
                return

            distances = [
                (mouse_x - x) ** 2 + (mouse_y - y) ** 2 for x, y in self.data_points
            ]
            nearest_index = np.argmin(distances)
            nearest_x, nearest_y = self.data_points[nearest_index]

            # Update the label with the nearest point's value
            self.label.setText(
                f"Market Price: {self.listing_price[nearest_index]:,.2f}<br>Profit/hr: {nearest_y:,.2f}<br>Time: {pd.to_datetime(nearest_x, unit='s')}"
            )
            view_point = self.p1.vb.mapViewToScene(pg.Point(nearest_x, nearest_y))
            self.label.setPos(view_point - self.label.boundingRect().topRight())

    class RecipeTableModel(QAbstractTableModel):

        def __init__(self, parent: QObject):
            super().__init__(parent)
            self.table_data: List[List[str]] = []
            self.recipes: List[Recipe] = []
            self.header_data: List[str] = [
                "Recipe Output",
                "Profit / hr",
                "Tech Req.",
                "Consumables",
            ]

        def rowCount(self, /, parent: QModelIndex | QPersistentModelIndex = ...) -> int:
            return len(self.table_data)

        def columnCount(
            self, /, parent: QModelIndex | QPersistentModelIndex = ...
        ) -> int:
            return 4

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

        def recipe_table_update(
            self,
            recipe: Recipe,
            profit_per_hour: float,
            consumable_list: tuple[int, ...],
        ) -> None:
            row = []
            row.append(get_item_name(recipe.output.id))
            row.append(profit_per_hour)
            row.append(
                f"{get_building(recipe.producedIn).specialization.name} {recipe.reqTech}"
            )
            row.append(
                ", ".join(sorted(get_item_name(mat_id) for mat_id in consumable_list))
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
            self.recipe_clicked.emit(recipe)

        def setModel(self, model: QAbstractTableModel) -> None:
            """Override setModel to connect signals for dynamic resizing."""
            super().setModel(model)
            if model:
                model.modelReset.connect(self.adjust_table_width)
                model.dataChanged.connect(self.adjust_table_width)
                model.rowsInserted.connect(self.adjust_table_width)
                model.rowsRemoved.connect(self.adjust_table_width)

        def adjust_table_width(self):
            """Adjust the table width to fit the contents."""
            self.resizeColumnsToContents()

            # Calculate the total width of all columns
            total_table_width = sum(
                self.horizontalHeader().sectionSize(i)
                for i in range(self.horizontalHeader().count())
            )

            # Add width for vertical scrollbar (if present)
            if self.verticalScrollBar().isVisible():
                total_table_width += self.verticalScrollBar().width()

            # Update the table's minimum and maximum width
            self.setMinimumWidth(total_table_width)
            self.setMaximumWidth(total_table_width)

            # Notify the parent splitter to adjust sizes
            if self.parent() and isinstance(self.parent(), QSplitter):
                self.parent().setSizes(
                    [total_table_width, self.parent().width() - total_table_width]
                )

    class RecipeTableProxyModel(QSortFilterProxyModel):
        def __init__(self, parent: QObject | None):
            super().__init__(parent)
            self.setDynamicSortFilter(True)
            self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.sort(1, Qt.SortOrder.DescendingOrder)
            self.tech_level_filters: Dict[BuildingSpecialization, int] = {}
            self.building_filters: Dict[int, bool] = {}

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

            # Filter based on tech
            building_specialization = get_building(recipe.producedIn).specialization
            try:
                if recipe.reqTech > self.tech_level_filters[building_specialization]:
                    return False
            except KeyError:
                _logger.error(
                    f"No tech level filter set for {building_specialization}."
                )
            try:
                if not self.building_filters[recipe.producedIn]:
                    return False
            except KeyError:
                _logger.error(f"No building filter set for {recipe.producedIn}.")
            return super().filterAcceptsRow(source_row, source_parent)

        @Slot(BuildingSpecialization, int)
        def set_tech_level_filter(
            self, specialization: BuildingSpecialization, max_tech_level: int
        ) -> None:
            self.tech_level_filters[specialization] = max_tech_level
            self.invalidateFilter()

        @Slot(list, list)
        def set_tech_level_filters(
            self,
            specializations: List[BuildingSpecialization],
            max_tech_levels: List[int],
        ) -> None:
            for specialization, max_tech_level in zip(specializations, max_tech_levels):
                self.tech_level_filters[specialization] = max_tech_level
            self.invalidateFilter()

        @Slot(int, bool)
        def set_building_filter(self, building: Building, enabled: bool) -> None:
            self.building_filters[building.id] = enabled
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

            def value(self) -> int:
                return self.slider.value()

        class BuildingCheckbox(QCheckBox):
            checkbox_toggled = Signal(Building, bool)

            def __init__(self, building: Building, parent: QObject) -> None:
                super().__init__(building.name, parent)
                self.building = building
                self.toggled.connect(self.handle_toggle)
                self.setChecked(True)

            @Slot(bool)
            def handle_toggle(self, checked: bool) -> None:
                self.checkbox_toggled.emit(self.building, checked)

        def __init__(self, parent: QObject, settings: Settings) -> None:
            super().__init__(parent)

            # Tech Filter
            tech_filter_widget = QWidget(self)
            tech_filter_layout = QVBoxLayout()
            tech_filter_widget.setLayout(tech_filter_layout)
            self.tech_filter_all_widget = RecipeWindow.FilterToolbox.TechFilterWidget(
                "All", self
            )
            self.tech_filter_all_widget.slider.setValue(0)
            self.tech_filter_all_widget.label.setText("0")
            self.tech_filter_all_widget.slider.setMaximum(10)
            tech_filter_layout.addWidget(self.tech_filter_all_widget)
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

            # Building Filter
            building_filter_widget = QWidget(self)
            self.building_filter_layout = QVBoxLayout()
            building_filter_widget.setLayout(self.building_filter_layout)
            self.building_widgets: Dict[int, QCheckBox] = {}
            self.building_filter_all_checkbox = QCheckBox("Select All", self)
            self.building_filter_all_checkbox.setChecked(True)
            self.building_filter_layout.addWidget(self.building_filter_all_checkbox)
            self.building_filter_all_checkbox.toggled.connect(
                self.handle_building_filter_all_toggle
            )
            self.addItem(building_filter_widget, "Buildings")

            self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            self.setMinimumWidth(145)

            self.set_tech_level_sliders(settings)

        @Slot(bool)
        def handle_building_filter_all_toggle(self, checked: bool) -> None:
            for checkbox in self.building_widgets.values():
                checkbox.setChecked(checked)

        # Creates and adds a building filter checkbox
        # Returns None if building filter already exists
        def add_building_filter(self, building: Building) -> BuildingCheckbox:
            if building.id in self.building_widgets:
                return
            checkbox = RecipeWindow.FilterToolbox.BuildingCheckbox(building, self)
            self.building_widgets[building.id] = checkbox

            # Sort widgets alphabetically before adding new one
            widgets = [checkbox]
            for i in reversed(range(1, self.building_filter_layout.count())):
                item = self.building_filter_layout.itemAt(i)
                widget = item.widget()
                if widget:
                    widgets.append(widget)
                    self.building_filter_layout.removeWidget(widget)
            widgets.sort(key=lambda w: w.text() if hasattr(w, "text") else "")
            for widget in widgets:
                self.building_filter_layout.addWidget(widget)

            return checkbox

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
        self.toolbox.setMaximumWidth(self.toolbox.sizeHint().width())
        self.main_layout.addWidget(self.toolbox)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_layout.addWidget(splitter)
        self.main_layout.setStretchFactor(splitter, 1)

        self.recipe_table_model = RecipeWindow.RecipeTableModel(self)
        self.recipe_table_view = RecipeWindow.RecipeTableView(self)
        self.recipe_table_view.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
        )
        self.recipe_table_proxy_model = RecipeWindow.RecipeTableProxyModel(self)
        self.recipe_table_proxy_model.setSourceModel(self.recipe_table_model)
        # Connect each tech slider to the proxy model filter
        for specialization, tech_widget in self.toolbox.tech_widgets.items():
            tech_widget.slider.valueChanged.connect(
                lambda value, spec=specialization, value_modifier=self.toolbox.tech_filter_all_widget.value(): self.recipe_table_proxy_model.set_tech_level_filter(
                    spec, value + value_modifier
                )
            )
        # Connect "All" tech slider to all specialization sliders
        self.toolbox.tech_filter_all_widget.slider.valueChanged.connect(
            lambda value, widgets=self.toolbox.tech_widgets: self.recipe_table_proxy_model.set_tech_level_filters(
                list(widgets.keys()),
                [
                    value + tech_widget.slider.value()
                    for tech_widget in widgets.values()
                ],
            )
        )
        # Set initial tech level filters from settings
        for specialization, max_level in settings.tech_level_filters.items():
            self.recipe_table_proxy_model.set_tech_level_filter(
                specialization, max_level
            )
        self.recipe_table_view.setModel(self.recipe_table_proxy_model)

        splitter.addWidget(self.recipe_table_view)

        self.plot = RecipeWindow.PriceGraph(self)
        splitter.addWidget(self.plot)

        # Adjust stretch factors in splitter
        splitter.setStretchFactor(0, 0)  # Table view gets no extra space
        splitter.setStretchFactor(1, 1)  # Plot gets all extra space

        self.recipe_table_view.recipe_clicked.connect(self.plot.plot_recipe)

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

        self.recipe_worker.recipe_added_signal.connect(self.handle_recipe_added)

        self.recipe_worker_thread.start()

    @Slot(Recipe)
    def handle_recipe_added(self, recipe: Recipe) -> None:
        building = get_building(recipe.producedIn)

        # Calculate profit per hour
        try:
            if (r := calculate_profit_per_hour(recipe)) is None:
                return
            profit_per_hour, consumable_preferred_combination = r
        except ValueError as e:
            _logger.error(
                f"Error calculating profit per hour for recipe {get_item_name(recipe.output.id)} ({recipe.id}): {e}"
            )
            return

        # Add building filter
        checkbox = self.toolbox.add_building_filter(building)
        if checkbox is not None:
            self.recipe_table_proxy_model.set_building_filter(
                building, True
            )  # need to set initial state before connect since emit is in add_building_filter()
            checkbox.checkbox_toggled.connect(
                self.recipe_table_proxy_model.set_building_filter
            )

        # Update recipe table
        self.recipe_table_model.recipe_table_update(
            recipe, profit_per_hour, consumable_preferred_combination
        )

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


def calculate_profit_per_hour(recipe: Recipe) -> None | tuple[float, tuple[int, ...]]:
    """
    Calculate the profit per hour for a given recipe.

    This function computes the profit generated by a recipe on an hourly basis,
    taking into account the current market prices of the output and input materials,
    as well as the costs associated with the workers needed to produce the output.

    Args:
        recipe (Recipe): The recipe for which to calculate the profit per hour.

    Returns:
        None | tuple[float, tuple[int, ...]]:
            Returns a tuple containing the profit per hour and a tuple of worker counts
            if the calculation is successful. Returns None if the calculation cannot be performed.

    Raises:
        ValueError: If there is no price data available for any of the materials.
    """
    building = get_building(recipe.producedIn)

    # Calculate profit per hour
    listing = Exchange.get_listing(recipe.output.id)
    base_profit_per_hour = listing.current_price * recipe.output.am
    for material_amount in recipe.inputs:
        material_price = Exchange.get_listing(material_amount.id).current_price
        if material_price < 1:
            # This should've been caught by recipeWorker.run()
            raise ValueError(f"No price data for material {material_amount.id}.")
        base_profit_per_hour -= material_price * material_amount.am
    base_profit_per_hour = base_profit_per_hour / (recipe.timeMinutes / 60)

    # Calculate worker cost
    worker_type: WorkerType
    optimal_profit_per_hour = 0.0
    # Get list of consumables
    consumable_id_set: set[int] = set()
    for worker_type, worker_count in enumerate(building.workersNeeded or [], start=1):
        if worker_count == 0:
            continue
        worker = get_worker(worker_type)
        consumable_id_set.update(
            [consumable.matId for consumable in worker.consumables]
        )
    if len(consumable_id_set) == 0:
        _logger.debug(
            f"No workers needed for building {building.name} ({building.id})."
        )
    # Try all combinations of consumables to find lowest cost
    consumable_preferred_combination = None
    for combination_size in range(len(consumable_id_set or []) + 1):
        for consumable_list in itertools.combinations(
            consumable_id_set or [], combination_size
        ):
            worker_cost_per_hour = 0.0
            production_modifier = 1.0
            worker_count_satisfaction_list: List[tuple[float, float]] = (
                []
            )  # worker_count, worker_satisfaction
            for worker_type, worker_count in enumerate(
                building.workersNeeded or [], start=1
            ):
                if worker_count == 0:
                    continue
                consumable_optional_missed_count = 0
                consumable_essential_missed_count = 0
                combination_valid = True
                worker = get_worker(worker_type)
                for consumable in worker.consumables:
                    # If consumable is in this combination, calculate its cost
                    if consumable.matId in consumable_list:
                        consumable_listing = Exchange.get_listing(consumable.matId)
                        if consumable_listing.current_price < 1:
                            # _logger.warning(
                            #     f"No price data for consumable {consumable_listing.name} ({consumable.matId}), skipping worker cost calculation for recipe {get_item_name(recipe.output.id)} ({recipe.id}). Consumable combination tried: {consumable_list}."
                            # )
                            combination_valid = False
                            break
                        worker_cost_per_hour += (
                            consumable_listing.current_price  # in cents
                            * consumable.amount  # daily consumption per 1000 workers
                            * worker_count  # number of workers
                            / 24  # hours per day
                            / 1000  # per 1000 workers
                            / 100  # convert cents to dollars
                        )
                        # _logger.debug(
                        #     f"Consumable Price: {consumable_listing.current_price}, Amount: {consumable.amount}, Worker Count: {worker_count}, Cost/hr: {worker_cost_per_hour}"
                        # )
                    # If consumable is not in this combination, apply satisfaction penalty
                    else:
                        if consumable.essential:
                            consumable_essential_missed_count += 1
                        else:
                            consumable_optional_missed_count += 1
                if not combination_valid:
                    break
                worker_type_satisfaction = 1.0
                worker_type_satisfaction -= 0.1 * consumable_optional_missed_count
                worker_type_satisfaction *= 0.6**consumable_essential_missed_count
                worker_type_satisfaction = max(worker_type_satisfaction, 0.1)
                worker_count_satisfaction_list.append(
                    (worker_count, worker_type_satisfaction)
                )
            total_worker_count = sum(
                worker_count for worker_count, _ in worker_count_satisfaction_list
            )
            total_worker_satisfaction = (
                sum(
                    worker_count * worker_satisfaction / total_worker_count
                    for worker_count, worker_satisfaction in worker_count_satisfaction_list
                )
                if total_worker_count > 0
                else 0.1
            )
            configuration_profit_per_hour = (
                base_profit_per_hour * total_worker_satisfaction - worker_cost_per_hour
            )
            if configuration_profit_per_hour > optimal_profit_per_hour:
                optimal_profit_per_hour = configuration_profit_per_hour
                consumable_preferred_combination = consumable_list
    if optimal_profit_per_hour == 0.0:
        # _logger.debug(
        #     f"Could not calculate profit for recipe {get_item_name(recipe.output.id)} ({recipe.id}). Base profit/hr: {base_profit_per_hour:,.2f}."
        # )
        return

    if consumable_preferred_combination is None:
        _logger.error(
            f"No valid consumable combination found for recipe {get_item_name(recipe.output.id)} ({recipe.id}). This should not happen."
        )
        return

    return optimal_profit_per_hour, consumable_preferred_combination
