from tkinter import SE
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
    Qt,
    QAbstractTableModel,
    QSortFilterProxyModel,
    QObject,
    QModelIndex,
    QPersistentModelIndex,
    QPointF,
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
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyle,
)
from PySide6.QtGui import QCloseEvent, QWheelEvent, QColor, QBrush, QPalette, QTextLayout, QTextCharFormat, QPainter, QTextOption
import pyqtgraph as pg
import matplotlib.colors as mcolors
import matplotlib.cm as cm

from settings import Settings
from utils import align_add, calculate_profit_and_consumables, ConsumablesDelegate, format_consumables
from api.gameData import get_gamedata, get_item_name, get_building, get_worker
from api.models.gameData import Recipe, BuildingSpecialization, Building, WorkerType
from api.exchange import Exchange
from api.models.exchange import Listing
from recipeWorker import RecipeWorker

_logger = logging.getLogger(__name__)


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
            assert isinstance(self.p1, pg.PlotItem)
            self.p1.getAxis("left").setLabel("Profit/hr", color="#00ff00")

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
            output_average_price_index = (
                pd.to_datetime(listing.average_price_history.index).astype("int64")
                // 10**9
            )

            # Ensure y_data is numeric
            output_average_price = (
                pd.to_numeric(listing.average_price_history["price"], errors="coerce")
                * recipe.output.am
                / (100 * recipe.timeMinutes / 60)
            )
            output_current_price_index = (
                pd.to_datetime(listing.current_price_history.index).astype("int64")
                // 10**9
            )
            output_current_price = (
                pd.to_numeric(listing.current_price_history["price"], errors="coerce")
                * recipe.output.am
                / (100 * recipe.timeMinutes / 60)
            )

            # Store data points for nearest-point calculation
            self.data_points = list(zip(output_average_price_index, output_average_price))
            self.listing_price = listing.average_price_history["price"].to_numpy() / 100

            # Plot the data
            self.p1.plot(
                x=np.asarray(output_average_price_index),
                y=np.asarray(output_average_price),
                pen=pg.mkPen(color="#00ff00", width=2),
                name="Average Profit",
            )
            self.p1.plot(
                x=np.asarray(output_current_price_index),
                y=np.asarray(output_current_price),
                pen=pg.mkPen(color="#00ff00", width=2, style=Qt.PenStyle.DashLine),
                name="Current Profit",
            )
            # self.label.setText(
            #     f"Market Price: {self.listing_price[-1]:,.2f}<br>Profit/hr: {y_data.iloc[-1]:,.2f}<br>Time: {pd.to_datetime(x_data[-1], unit='s')}"
            # )
            # self.label.setPos(
            #     pg.Point(x_data[-1], y_data.iloc[-1])
            #     - self.label.boundingRect().topRight()
            # )

            # Plot each ingredient price
            ingredient_average_price_subtotal = None
            ingredient_current_price_subtotal = None
            colormap = cm.get_cmap("tab10")
            num_ingredients = len(recipe.inputs)
            ingredient_colors = [
                mcolors.to_hex(colormap(i / num_ingredients))
                for i in range(num_ingredients)
            ]
            for material_idx, material_amount in enumerate(recipe.inputs):
                material_listing = Exchange.get_listing(material_amount.id)
                material_listing.average_price_history.sort_index(inplace=True)
                input_average_price = material_listing.average_price_history.copy()
                input_current_price = material_listing.current_price_history.copy()
                input_average_price.index = (
                    pd.to_datetime(input_average_price.index).astype("int64") // 10**9
                )
                input_average_price["price"] = (
                    input_average_price["price"]
                    * material_amount.am
                    / (100 * recipe.timeMinutes / 60)
                )
                input_current_price.index = (
                    pd.to_datetime(input_current_price.index).astype("int64") // 10**9
                )
                input_current_price["price"] = (
                    input_current_price["price"]
                    * material_amount.am
                    / (100 * recipe.timeMinutes / 60)
                )
                # _logger.debug(f"Plotting ingredient {material_listing.name} ({material_amount.id}). Latest average price history: {material_listing.average_price_history['price'].iloc[-1]/100:.2f}.")

                # Assign a unique color to each ingredient
                ingredient_color = ingredient_colors[material_idx]

                self.p1.plot(
                    x=input_average_price.index.to_numpy(),
                    y=input_average_price["price"].to_numpy(),
                    pen=pg.mkPen(color=ingredient_color, width=1),
                    name=f"Average Ingredient: {material_listing.name}",
                )
                self.p1.plot(
                    x=input_current_price.index.to_numpy(),
                    y=input_current_price["price"].to_numpy(),
                    pen=pg.mkPen(color=ingredient_color, width=1, style=Qt.PenStyle.DashLine),
                    name=f"Current Ingredient: {material_listing.name}",
                )

                label = pg.TextItem(
                    text=f"{material_listing.name}\nAv Cost/hr: {input_average_price['price'].iloc[-1]:,.2f}\nCurr Cost/hr: {input_current_price['price'].iloc[-1]:,.2f}",
                    color=ingredient_color,
                    anchor=(1, 1),
                )
                label.setPos(input_current_price.index[-1], input_current_price["price"].iloc[-1])
                self.p1.addItem(label)

                ingredient_average_price_subtotal = (
                    align_add(ingredient_average_price_subtotal, input_average_price["price"])
                    if ingredient_average_price_subtotal is not None
                    else input_average_price["price"]
                )
                ingredient_current_price_subtotal = (
                    align_add(ingredient_current_price_subtotal, input_current_price["price"])
                    if ingredient_current_price_subtotal is not None
                    else input_current_price["price"]
                )

            ingredient_average_price_subtotal.dropna(inplace=True)
            self.p1.plot(
                x=ingredient_average_price_subtotal.index.to_numpy(),
                y=ingredient_average_price_subtotal.to_numpy(),
                pen=pg.mkPen(color="#ff0000", width=2),
                name="Average Ingredient Total",
            )
            ingredient_current_price_subtotal.dropna(inplace=True)
            self.p1.plot(
                x=ingredient_current_price_subtotal.index.to_numpy(),
                y=ingredient_current_price_subtotal.to_numpy(),
                pen=pg.mkPen(color="#ff0000", width=2, style=Qt.PenStyle.DashLine),
                name="Current Ingredient Total",
            )
            label = pg.TextItem(
                text=f"Average Cost/hr: {ingredient_average_price_subtotal.iloc[-1]:,.2f}\nCurrent Cost/hr: {ingredient_current_price_subtotal.iloc[-1]:,.2f}",
                color="#ff0000",
                anchor=(1, 1),
            )
            label.setPos(ingredient_average_price_subtotal.index[-1], ingredient_average_price_subtotal.iloc[-1])
            self.p1.addItem(label)

            try:
                self.p1.vb.autoRange()
            except Exception as e:
                _logger.error(f"Error during autoRange: {e}")

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
            self.consumables_data: List[tuple[tuple[int, ...], tuple[int, ...]]] = []  # Store (preferred, rejected) tuples
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
            return len(self.header_data)

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
                    data = "{:,.2f}".format(data) if data != -1 else ""
                return data
            elif role == Qt.ItemDataRole.UserRole:
                return data
            return None

        def add_row(
            self,
            recipe: Recipe,
            profit_per_hour: float,
            consumable_preferred: tuple[int, ...],
            consumable_rejected: tuple[int, ...],
        ) -> None:
            row = []
            row.append(get_item_name(recipe.output.id))
            row.append(profit_per_hour)
            row.append(
                f"{get_building(recipe.producedIn).specialization.name} {recipe.reqTech}"
            )
            row.append(format_consumables(consumable_preferred, consumable_rejected))

            self.recipes.append(recipe)
            self.consumables_data.append((consumable_preferred, consumable_rejected))
            self.beginInsertRows(QModelIndex(), self.rowCount(), self.rowCount())
            self.table_data.append(row)
            self.endInsertRows()

        def setData(
            self,
            /,
            index: QModelIndex | QPersistentModelIndex,
            value: object,
            role: Qt.ItemDataRole = Qt.ItemDataRole.EditRole,
        ) -> bool:
            row = index.row()
            column = index.column()
            if role == Qt.ItemDataRole.EditRole:
                self.table_data[row][column] = value
                self.dataChanged.emit(index, index)
                return True
            return False
        
        def update_consumables(self, row: int, consumable_preferred: tuple[int, ...], consumable_rejected: tuple[int, ...]) -> None:
            """Update consumables data and text for a specific row."""
            self.table_data[row][3] = format_consumables(consumable_preferred, consumable_rejected)
            self.consumables_data[row] = (consumable_preferred, consumable_rejected)
            
            # Emit dataChanged for the consumables column
            consumables_index = self.index(row, 3)
            self.dataChanged.emit(consumables_index, consumables_index)


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
            self.sortByColumn(1, Qt.SortOrder.DescendingOrder)

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
                # model.modelReset.connect(self.adjust_table_width)
                model.dataChanged.connect(self.adjust_table_width)
                model.rowsInserted.connect(self.adjust_table_width)
                # model.rowsRemoved.connect(self.adjust_table_width)

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

            # Notify the parent splitter to adjust sizes
            if self.parent() and isinstance(self.parent(), QSplitter):
                self.parent().setSizes(
                    [total_table_width, self.parent().width() - total_table_width]
                )

    class RecipeTableProxyModel(QSortFilterProxyModel):
        def __init__(self, parent: QObject | None, settings: Settings = None) -> None:
            super().__init__(parent)
            self.setDynamicSortFilter(True)
            self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.sort(1, Qt.SortOrder.DescendingOrder)
            self.tech_level_filters: Dict[BuildingSpecialization, int] = settings.tech_level_filters if settings else {}
            self.building_filters: Dict[int, bool] = {}
            self.tech_level_modifier: int = 0

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
            max_tech_level = self.tech_level_filters.get(building_specialization, float('inf'))
            if recipe.reqTech > max_tech_level + self.tech_level_modifier: 
                return False
            
            # Filter based on building
            if not self.building_filters.get(recipe.producedIn, True):
                return False
            
            return super().filterAcceptsRow(source_row, source_parent)

        @Slot(int, bool)
        def set_building_filter(self, building: Building, enabled: bool) -> None:
            self.beginFilterChange()
            self.building_filters[building.id] = enabled
            self.endFilterChange()

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
        
        techSliderChanged = Signal(BuildingSpecialization, int)

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
            self.tech_filter_all_widget.slider.setMaximum(19)
            tech_filter_layout.addWidget(self.tech_filter_all_widget)
            self.tech_widgets: Dict[
                BuildingSpecialization, RecipeWindow.FilterToolbox.TechFilterWidget
            ] = {}
            for specialization in BuildingSpecialization:
                if specialization == BuildingSpecialization.NONE or specialization == BuildingSpecialization.RESOURCE_EXTRACTION:
                    continue
                tech_widget = RecipeWindow.FilterToolbox.TechFilterWidget(
                    BuildingSpecialization(specialization).name.title(), self
                )
                self.tech_widgets[specialization] = tech_widget
                tech_filter_layout.addWidget(tech_widget)
            # Initialize slider values from settings
            for specialization, max_level in settings.tech_level_maximums.items():
                self.tech_widgets[specialization].set_maximum(max_level)
            for specialization, filter_level in settings.tech_level_filters.items():
                self.tech_widgets[specialization].slider.setValue(filter_level)
            for specialization, tech_widget in self.tech_widgets.items():
                tech_widget.slider.valueChanged.connect(
                    lambda value, spec=specialization: self.techSliderChanged.emit(
                        spec, value
                    )
                )
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


    def __init__(self, parent: QObject, recipe_worker: RecipeWorker, settings: Settings) -> None:
        super().__init__(parent)
        
        # TODO: Remove this reference cycle
        self.settings = settings

        # Store reference to recipe worker
        self.recipe_worker = recipe_worker
        self.main_layout = QHBoxLayout()
        self.setLayout(self.main_layout)

        # Filter Toolbox
        self.toolbox = RecipeWindow.FilterToolbox(self, self.settings)
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
        self.recipe_table_proxy_model = RecipeWindow.RecipeTableProxyModel(self, self.settings)
        self.recipe_table_proxy_model.setSourceModel(self.recipe_table_model)
        
        # Apply custom delegate to consumables column
        consumables_delegate = ConsumablesDelegate(self)
        self.recipe_table_view.setItemDelegateForColumn(3, consumables_delegate)
        
        self.toolbox.techSliderChanged.connect(self.handle_tech_slider_change)
        self.toolbox.tech_filter_all_widget.slider.valueChanged.connect(
            self.handle_all_tech_slider_change
        )
        self.recipe_table_view.setModel(self.recipe_table_proxy_model)

        splitter.addWidget(self.recipe_table_view)

        self.plot = RecipeWindow.PriceGraph(self)
        splitter.addWidget(self.plot)

        # Adjust stretch factors in splitter
        splitter.setStretchFactor(0, 0)  # Table view gets no extra space
        splitter.setStretchFactor(1, 1)  # Plot gets all extra space

        self.recipe_table_view.recipe_clicked.connect(self.plot.plot_recipe)

        # Connect signals from recipe worker
        self.recipe_worker.tech_level_change_signal.connect(
            self.toolbox.handle_tech_level_change
        )
        self.recipe_worker.recipe_added_signal.connect(self.handle_recipe_added)
        self.recipe_worker.exchange_updated_signal.connect(
            self.handle_exchange_updated
        )

    # Called from toolbox when a tech slider is changed
    @Slot(BuildingSpecialization, int)
    def handle_tech_slider_change(
        self, specialization: BuildingSpecialization, max_tech_level: int
    ) -> None:
        self.recipe_table_proxy_model.beginFilterChange()
        self.settings.set_tech_level_filter(specialization, max_tech_level)
        self.recipe_table_proxy_model.endFilterChange()

    @Slot(int)
    def handle_all_tech_slider_change(self, value: int) -> None:
        self.recipe_table_proxy_model.beginFilterChange()
        self.recipe_table_proxy_model.tech_level_modifier = value
        self.recipe_table_proxy_model.endFilterChange()

    # Called from recipe worker when a new recipe is added
    @Slot(Recipe)
    def handle_recipe_added(self, recipe: Recipe) -> None:
        building = get_building(recipe.producedIn)

        # Calculate profit and consumables
        result = calculate_profit_and_consumables(recipe)
        if result is None:
            profit_per_hour = float("-inf")
            consumable_preferred_combination = ()
            consumable_rejected_combination = ()
        else:
            profit_per_hour, consumable_preferred_combination, consumable_rejected_combination = result

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
        self.recipe_table_model.add_row(
            recipe, profit_per_hour, consumable_preferred_combination, consumable_rejected_combination
        )

    # Called from recipe worker when exchange listings are updated
    @Slot(dict)
    def handle_exchange_updated(self) -> None:
        """
        Update the profit per hour for all recipes in the table when listings are updated.

        Args:
            listings (Dict[int, Listing]): Updated listings data.
        """
        for row, recipe in enumerate(self.recipe_table_model.recipes):
            result = calculate_profit_and_consumables(recipe)
            if result is None:
                profit_per_hour = float("-inf")
                consumable_preferred_combination = ()
                consumable_rejected_combination = ()
            else:
                profit_per_hour, consumable_preferred_combination, consumable_rejected_combination = result

            if profit_per_hour < 0:
                _logger.debug(f"Recipe '{get_item_name(recipe.output.id)}' now has negative profit per hour: {profit_per_hour:.2f}.")
                _logger.debug(f"Preferred consumables: {consumable_preferred_combination}, Rejected consumables: {consumable_rejected_combination}.")
            self.recipe_table_model.setData(self.recipe_table_model.index(row, 1), profit_per_hour, Qt.ItemDataRole.EditRole)
            self.recipe_table_model.update_consumables(row, consumable_preferred_combination, consumable_rejected_combination)

    def closeEvent(self, event: QCloseEvent) -> None:
        _logger.debug("Saving settings.")
        # TODO: Put this in galaxyTycoonUi.py
        self.settings.tech_level_filters = self.recipe_table_proxy_model.tech_level_filters
        self.settings.tech_level_maximums = self.toolbox.get_tech_level_maximums()



