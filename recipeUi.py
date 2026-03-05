import logging
import math
from typing import Dict, List

import matplotlib.cm as cm
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd
import pyqtgraph as pg
from pydantic import BaseModel, Field
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QPainter,
    QPalette,
    QTextCharFormat,
    QTextLayout,
    QTextOption,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolBox,
    QVBoxLayout,
    QWidget,
)

from api.exchange import Exchange
from api.gameData import GameDataManager
from api.models.exchange import Listing
from api.models.gameData import Building, Recipe, Specialization, WorkerType
from recipeWorker import RecipeWorker
from settings import Settings
from utils import (
    BuildingColorDelegate,
    ConsumablesDelegate,
    GradientColorDelegate,
    PriceGraph,
    SpecializationColorDelegate,
    align_add,
    calculate_profit_and_consumables,
    format_consumables,
)

_logger = logging.getLogger(__name__)

QUANTITY_SOLD_SCALING_FACTOR = (
    10.0  # Scaling factor for quantity sold logarithmic transformation
)


class ValueRecalculationWorker(QObject):
    """Worker that recalculates values in background thread when weight factor changes."""

    values_updated = Signal(object)  # dict of {row: value}
    finished = Signal()

    def __init__(self, table_data: List[List[float]], weight: float):
        super().__init__(objectName="ValueRecalculationWorker")
        self.table_data = table_data
        self.weight = weight

    def run(self) -> None:
        """Recalculate values for all rows with current weight."""
        try:
            _logger.debug("ValueRecalculationWorker started")
            batch_updates = {}
            batch_size = 100  # Emit updates in batches of 100 rows

            for row in range(len(self.table_data)):
                # Check if thread should be interrupted
                current_thread = QThread.currentThread()
                if current_thread.isInterruptionRequested():
                    _logger.debug("ValueRecalculationWorker interrupted")
                    break

                profit_per_hour = self.table_data[row][1]
                quantity_sold_per_hour = self.table_data[row][2]

                # Apply logarithmic transformation to quantity to reduce impact of very large values
                # log1p(x) = log(1+x), handles 0 and negative values safely
                # Scale by QUANTITY_SOLD_SCALING_FACTOR to bring it into comparable range with profit/hr
                quantity_sold_log = (
                    math.log1p(quantity_sold_per_hour) * QUANTITY_SOLD_SCALING_FACTOR
                )

                # Calculate value: weighted average of profit and log(quantity)
                # weight is for profit/hr, (1-weight) is for log(quantity)
                value = (profit_per_hour * (1 - self.weight)) + (
                    quantity_sold_log * self.weight
                )
                batch_updates[row] = value

                # Emit batch when we reach batch_size or at the end
                if len(batch_updates) >= batch_size or row == len(self.table_data) - 1:
                    self.values_updated.emit(batch_updates)
                    batch_updates = {}
        except Exception as e:
            _logger.error(f"Error in ValueRecalculationWorker: {e}")
        finally:
            self.finished.emit()



class RecipeWindow(QWidget):
    class RecipeTableModel(QAbstractTableModel):

        def __init__(self, parent: QObject):
            super().__init__(parent)
            self.table_data: List[List[str]] = []
            self.consumables_data: List[tuple[tuple[int, ...], tuple[int, ...]]] = (
                []
            )  # Store (preferred, rejected) tuples
            self.recipes: List[Recipe] = []
            self.header_data: List[str] = [
                "Recipe Output",
                "$ / hr",
                "Vel.",
                "Price Δ",
                "Value",
                "Tech Req.",
                "Building",
                "Consumables",
            ]
            # Track min/max values for gradient coloring
            self.profit_min = 0.0
            self.profit_max = 1.0
            self.quantity_min = 0.0
            self.quantity_max = 1.0

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
                if column == 1 or column == 2 or column == 4:  # $ / hr, Vel., Value
                    data = "{:,.2f}".format(data) if data != -1 else ""
                elif column == 3:  # Price Δ - format with + sign for positive values
                    data = "{:+,.2f}".format(data) if data != 0 else "0.00"
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
            quantity_sold_daily: float = 0.0,
        ) -> None:
            row = []
            row.append(GameDataManager.get_item_name(recipe.output.id))
            row.append(profit_per_hour)
            quantity_sold_per_hour = (
                quantity_sold_daily * (recipe.timeMinutes / 60)
                if recipe.timeMinutes > 0
                else 0.0
            )
            row.append(quantity_sold_per_hour)
            
            # Calculate price delta (current_price - average_price)
            listing = Exchange.get_listing(recipe.output.id)
            price_delta = (
                (listing.current_price - listing.average_price) / 100 if listing else 0.0
            )
            row.append(price_delta)
            
            row.append(0.0)  # Value column, will be updated later
            row.append(
                f"{GameDataManager.get_building(recipe.producedIn).specialization.name} {recipe.reqTech}"
            )
            row.append(f"{GameDataManager.get_building(recipe.producedIn).name}")
            row.append(format_consumables(consumable_preferred, consumable_rejected))

            self.recipes.append(recipe)
            self.consumables_data.append((consumable_preferred, consumable_rejected))
            self.beginInsertRows(QModelIndex(), self.rowCount(), self.rowCount())
            self.table_data.append(row)
            self.endInsertRows()

            # Update min/max values for gradient coloring
            self.update_min_max_values()

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

        def update_consumables(
            self,
            row: int,
            consumable_preferred: tuple[int, ...],
            consumable_rejected: tuple[int, ...],
        ) -> None:
            """Update consumables data and text for a specific row."""
            self.table_data[row][7] = format_consumables(
                consumable_preferred, consumable_rejected
            )
            self.consumables_data[row] = (consumable_preferred, consumable_rejected)

            # Emit dataChanged for the consumables column
            consumables_index = self.index(row, 7)
            self.dataChanged.emit(consumables_index, consumables_index)

        def update_value(self, row: int, value: float) -> None:
            """Update the value column for a specific row."""
            self.table_data[row][4] = value
            value_index = self.index(row, 4)
            self.dataChanged.emit(value_index, value_index)

        def update_values_batch(self, values_dict: dict) -> None:
            """Update multiple value rows at once and emit a single dataChanged signal.

            Args:
                values_dict: Dictionary of {row: value} pairs
            """
            if not values_dict:
                return

            # Update all values in the dictionary
            for row, value in values_dict.items():
                self.table_data[row][4] = value

            # Emit a single dataChanged signal for the entire range
            if values_dict:
                first_row = min(values_dict.keys())
                last_row = max(values_dict.keys())
                self.dataChanged.emit(self.index(first_row, 4), self.index(last_row, 4))

        def update_min_max_values(self) -> None:
            """Update min/max values for profit and quantity columns for gradient coloring."""
            if not self.table_data:
                self.profit_min = self.profit_max = 0.0
                self.quantity_min = self.quantity_max = 0.0
                return

            # Extract profit values (column 1), filtering out inf and -inf
            profit_values = [
                row[1]
                for row in self.table_data
                if isinstance(row[1], (int, float)) and math.isfinite(row[1])
            ]
            if profit_values:
                self.profit_min = min(profit_values)
                self.profit_max = max(profit_values)
                if self.profit_max == self.profit_min:
                    self.profit_max = self.profit_min + 1.0
            else:
                self.profit_min = self.profit_max = 0.0

            # Extract quantity values (column 2), apply log transform, filter out inf and -inf
            quantity_values = [
                math.log1p(row[2]) * QUANTITY_SOLD_SCALING_FACTOR
                for row in self.table_data
                if isinstance(row[2], (int, float)) and math.isfinite(row[2])
            ]
            if quantity_values:
                self.quantity_min = min(quantity_values)
                self.quantity_max = max(quantity_values)
                if self.quantity_max == self.quantity_min:
                    self.quantity_max = self.quantity_min + 1.0
            else:
                self.quantity_min = self.quantity_max = 0.0

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
        filter_changed = Signal()  # Emitted when any filter changes

        def __init__(self, parent: QObject | None, settings: Settings = None) -> None:
            super().__init__(parent)
            self.setDynamicSortFilter(True)
            self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.tech_level_filters: Dict[Specialization, int] = (
                settings.tech_level_filters if settings else {}
            )
            self.building_filters: Dict[int, bool] = {}
            self.tech_level_modifier: int = 0

        def lessThan(self, source_left: QModelIndex, source_right: QModelIndex) -> bool:
            # Numeric sorting for columns 1 (Profit/hr), 2 (Quantity Sold), 3 (Price Δ), and 4 (Value)
            if source_left.column() in (1, 2, 3, 4):
                left = self.sourceModel().data(source_left, Qt.ItemDataRole.UserRole)
                right = self.sourceModel().data(source_right, Qt.ItemDataRole.UserRole)
                # Handle -1 values (invalid/missing data)
                if left == -1 or right == -1:
                    return left < right
                try:
                    return float(left) < float(right)
                except (TypeError, ValueError):
                    return left < right
            return super().lessThan(source_left, source_right)

        def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
            source_model = self.sourceModel()
            assert isinstance(source_model, RecipeWindow.RecipeTableModel)
            recipe = source_model.recipes[source_row]

            # Filter based on tech
            building_specialization = GameDataManager.get_building(
                recipe.producedIn
            ).specialization
            max_tech_level = self.tech_level_filters.get(
                building_specialization, float("inf")
            )
            if recipe.reqTech > max_tech_level + self.tech_level_modifier:
                return False

            # Filter based on building
            if not self.building_filters.get(recipe.producedIn, True):
                return False

            return super().filterAcceptsRow(source_row, source_parent)

        @Slot(int, bool)
        def set_building_filter(self, building: Building, enabled: bool) -> None:
            self.building_filters[building.id] = enabled
            self.invalidateFilter()
            self.filter_changed.emit()

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

        techSliderChanged = Signal(Specialization, int)
        valueWeightChanged = Signal(float)

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
                Specialization, RecipeWindow.FilterToolbox.TechFilterWidget
            ] = {}
            for specialization in Specialization:
                if (
                    specialization == Specialization.NONE
                    or specialization == Specialization.RESOURCE_EXTRACTION
                ):
                    continue
                tech_widget = RecipeWindow.FilterToolbox.TechFilterWidget(
                    Specialization(specialization).name.title(), self
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

            # Value Weight Filter
            value_weight_widget = QWidget(self)
            value_weight_layout = QVBoxLayout()
            value_weight_widget.setLayout(value_weight_layout)

            value_weight_label = QLabel("Value Weight Factor", self)
            value_weight_layout.addWidget(value_weight_label)

            self.value_weight_slider = QSlider(Qt.Orientation.Horizontal, self)
            self.value_weight_slider.setMinimum(0)
            self.value_weight_slider.setMaximum(100)
            self.value_weight_slider.setValue(50)  # Default to 50/50 split
            self.value_weight_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            self.value_weight_slider.setTickInterval(10)
            value_weight_layout.addWidget(self.value_weight_slider)

            self.value_weight_value_label = QLabel("0.5", self)
            value_weight_layout.addWidget(self.value_weight_value_label)

            self.value_weight_slider.valueChanged.connect(
                self.handle_value_weight_change
            )

            self.addItem(value_weight_widget, "Value Weight")

            self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            self.setMinimumWidth(145)

        @Slot(int)
        def handle_value_weight_change(self, value: int) -> None:
            """Handle value weight slider changes. Value ranges from 0-100, converted to 0.0-1.0."""
            weight = value / 100.0
            self.value_weight_value_label.setText(f"{weight:.2f}")
            self.valueWeightChanged.emit(weight)

        def get_value_weight(self) -> float:
            """Get the current value weight as a float between 0.0 and 1.0."""
            return self.value_weight_slider.value() / 100.0

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
            self, specialization: Specialization, max_tech_level: int
        ) -> None:
            self.tech_widgets[specialization].set_maximum(max_tech_level)

        def get_tech_level_maximums(self) -> Dict[Specialization, int]:
            tech_level_maximums: Dict[Specialization, int] = {}
            for specialization, tech_widget in self.tech_widgets.items():
                tech_level_maximums[specialization] = tech_widget.slider.maximum()
            return tech_level_maximums

    def __init__(
        self, parent: QObject, recipe_worker: RecipeWorker, settings: Settings
    ) -> None:
        super().__init__(parent)

        # TODO: Remove this reference cycle
        self.settings = settings

        # Store reference to recipe worker
        self.recipe_worker = recipe_worker
        self.main_layout = QHBoxLayout()
        self.setLayout(self.main_layout)

        # Initialize value recalculation worker thread and debounce timer
        self.value_recalc_worker = None
        self.value_recalc_thread = None
        self.pending_weight = None

        # Debounce timer: waits 300ms after slider stops moving before recalculating
        self.value_weight_debounce_timer = QTimer(self)
        self.value_weight_debounce_timer.setSingleShot(True)
        self.value_weight_debounce_timer.timeout.connect(
            self.on_value_weight_debounce_timeout
        )

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
        self.recipe_table_proxy_model = RecipeWindow.RecipeTableProxyModel(
            self, self.settings
        )
        self.recipe_table_proxy_model.setSourceModel(self.recipe_table_model)

        # Apply custom delegate to consumables column
        consumables_delegate = ConsumablesDelegate(self)
        self.recipe_table_view.setItemDelegateForColumn(7, consumables_delegate)

        # Apply gradient color delegates to profit and quantity columns
        self.profit_delegate = GradientColorDelegate(self)
        # Quantity delegate uses log transformation to match the range calculation
        self.quantity_delegate = GradientColorDelegate(
            self, value_transform=lambda x: math.log1p(x) * QUANTITY_SOLD_SCALING_FACTOR
        )
        # Price delta delegate (no transformation needed)
        self.price_delta_delegate = GradientColorDelegate(self)
        
        self.recipe_table_view.setItemDelegateForColumn(1, self.profit_delegate)
        self.recipe_table_view.setItemDelegateForColumn(2, self.quantity_delegate)
        self.recipe_table_view.setItemDelegateForColumn(3, self.price_delta_delegate)

        # Apply specialization color delegate to tech requirement column
        self.specialization_delegate = SpecializationColorDelegate(self)
        self.recipe_table_view.setItemDelegateForColumn(5, self.specialization_delegate)

        # Apply building color delegate to building column
        self.building_delegate = BuildingColorDelegate(self)
        self.recipe_table_view.setItemDelegateForColumn(6, self.building_delegate)

        # Connect to model data changes to update delegate ranges
        self.recipe_table_model.dataChanged.connect(self.update_delegate_ranges)
        self.recipe_table_model.rowsInserted.connect(self.update_delegate_ranges)

        # Connect to proxy model filter changes to update delegate ranges based on visible rows
        self.recipe_table_proxy_model.layoutChanged.connect(self.update_delegate_ranges)
        self.recipe_table_proxy_model.filter_changed.connect(
            self.update_delegate_ranges
        )

        self.toolbox.techSliderChanged.connect(self.handle_tech_slider_change)
        self.toolbox.tech_filter_all_widget.slider.valueChanged.connect(
            self.handle_all_tech_slider_change
        )
        self.toolbox.valueWeightChanged.connect(self.handle_value_weight_change)
        self.recipe_table_view.setModel(self.recipe_table_proxy_model)
        self.recipe_table_proxy_model.sort(4, Qt.SortOrder.DescendingOrder)  # Sort by Value column
        
        # Hide the Value column (column 4) - keep for sorting but don't display
        self.recipe_table_view.setColumnHidden(4, True)
        HIDE_CONSUMABLES = True
        if HIDE_CONSUMABLES:
            self.recipe_table_view.setColumnHidden(7, True)

        splitter.addWidget(self.recipe_table_view)

        self.plot = PriceGraph(self)
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
        self.recipe_worker.exchange_updated_signal.connect(self.handle_exchange_updated)

    # Called from toolbox when a tech slider is changed
    @Slot(Specialization, int)
    def handle_tech_slider_change(
        self, specialization: Specialization, max_tech_level: int
    ) -> None:
        self.settings.set_tech_level_filter(specialization, max_tech_level)
        self.recipe_table_proxy_model.invalidateFilter()
        self.update_delegate_ranges()

    @Slot(int)
    def handle_all_tech_slider_change(self, value: int) -> None:
        self.recipe_table_proxy_model.tech_level_modifier = value
        self.recipe_table_proxy_model.invalidateFilter()
        self.update_delegate_ranges()

    @Slot(float)
    def handle_value_weight_change(self, weight: float) -> None:
        """Debounce value weight changes - wait 300ms after slider stops before recalculating."""
        self.pending_weight = weight
        # Restart the timer each time slider moves
        self.value_weight_debounce_timer.start(300)  # 300ms debounce

    @Slot()
    def update_delegate_ranges(self) -> None:
        """Update the min/max ranges for gradient delegates based on visible (filtered) rows only."""
        # Calculate ranges from visible rows in the proxy model
        profit_values = []
        quantity_values = []
        price_delta_values = []

        # Iterate through all rows in the proxy model (only visible rows)
        for proxy_row in range(self.recipe_table_proxy_model.rowCount()):
            source_index = self.recipe_table_proxy_model.mapToSource(
                self.recipe_table_proxy_model.index(proxy_row, 0)
            )
            source_row = source_index.row()

            # Get profit value (column 1)
            profit_val = self.recipe_table_model.table_data[source_row][1]
            if isinstance(profit_val, (int, float)) and math.isfinite(profit_val):
                profit_values.append(profit_val)

            # Get quantity value (column 2) with log transformation
            quantity_val = self.recipe_table_model.table_data[source_row][2]
            if isinstance(quantity_val, (int, float)) and math.isfinite(quantity_val):
                quantity_values.append(
                    math.log1p(quantity_val) * QUANTITY_SOLD_SCALING_FACTOR
                )

            # Get price delta value (column 3)
            price_delta_val = self.recipe_table_model.table_data[source_row][3]
            if isinstance(price_delta_val, (int, float)) and math.isfinite(price_delta_val):
                price_delta_values.append(price_delta_val)

        # Update profit delegate range
        if profit_values:
            profit_min = min(profit_values)
            profit_max = max(profit_values)
            if profit_max == profit_min:
                profit_max = profit_min + 1.0
        else:
            profit_min = profit_max = 0.0

        self.profit_delegate.set_value_range(profit_min, profit_max)

        # Update quantity delegate range
        if quantity_values:
            quantity_min = min(quantity_values)
            quantity_max = max(quantity_values)
            if quantity_max == quantity_min:
                quantity_max = quantity_min + 1.0
        else:
            quantity_min = quantity_max = 0.0

        self.quantity_delegate.set_value_range(quantity_min, quantity_max)

        # Update price delta delegate range - centered around 0 like in baseUi.py
        if price_delta_values:
            price_delta_min = min(price_delta_values)
            price_delta_max = max(price_delta_values)
            # Ensure range is centered around 0 for proper red/green gradient
            abs_max = max(abs(price_delta_min), abs(price_delta_max))
            self.price_delta_delegate.set_value_range(-abs_max, abs_max)
        else:
            self.price_delta_delegate.set_value_range(0.0, 0.0)

        # Trigger a repaint of the affected columns
        if self.recipe_table_model.rowCount() > 0:
            self.recipe_table_view.viewport().update()

    @Slot()
    def on_value_weight_debounce_timeout(self) -> None:
        """Called after slider stops moving - now actually recalculate values."""
        if self.pending_weight is None:
            return

        weight = self.pending_weight
        self.pending_weight = None

        # Stop any existing recalculation worker
        if self.value_recalc_thread is not None:
            try:
                if self.value_recalc_thread.isRunning():
                    _logger.debug(
                        "Requesting interruption of existing ValueRecalculationWorker"
                    )
                    self.value_recalc_thread.requestInterruption()
                    # Wait up to 500ms for thread to finish gracefully
                    if not self.value_recalc_thread.wait(500):
                        _logger.warning(
                            "ValueRecalculationWorker thread did not finish in time"
                        )
            except RuntimeError:
                # Thread was already deleted, that's fine
                pass
            finally:
                # Clean up old worker and thread
                self.value_recalc_worker = None
                self.value_recalc_thread = None

        # Create new worker and thread
        self.value_recalc_worker = ValueRecalculationWorker(
            self.recipe_table_model.table_data, weight
        )
        self.value_recalc_thread = QThread(self)

        # Move worker to thread
        self.value_recalc_worker.moveToThread(self.value_recalc_thread)

        # Connect signals - NO deleteLater on thread/worker, we manage lifecycle manually
        self.value_recalc_thread.started.connect(self.value_recalc_worker.run)
        self.value_recalc_worker.values_updated.connect(
            self.handle_values_updated, Qt.ConnectionType.QueuedConnection
        )
        self.value_recalc_worker.finished.connect(self.value_recalc_thread.quit)

        # Start the thread
        self.value_recalc_thread.start()

    @Slot(dict)
    def handle_values_updated(self, values_dict: dict) -> None:
        """Handle batch value updates from worker thread (runs on main thread).

        Args:
            values_dict: Dictionary of {row: value} pairs
        """
        self.recipe_table_model.update_values_batch(values_dict)

    # Called from recipe worker when a new recipe is added
    @Slot(Recipe)
    def handle_recipe_added(self, recipe: Recipe) -> None:
        building = GameDataManager.get_building(recipe.producedIn)

        # Calculate profit and consumables
        result = calculate_profit_and_consumables(
            recipe,
            self.settings.tech_level_filters[
                GameDataManager.get_building(recipe.producedIn).specialization
            ],
        )
        if result is None:
            profit_per_hour = float("-inf")
            consumable_preferred_combination = ()
            consumable_rejected_combination = ()
        else:
            (
                profit_per_hour,
                consumable_preferred_combination,
                consumable_rejected_combination,
            ) = result

        # Add building filter
        checkbox = self.toolbox.add_building_filter(building)
        if checkbox is not None:
            self.recipe_table_proxy_model.set_building_filter(
                building, True
            )  # need to set initial state before connect since emit is in add_building_filter()
            checkbox.checkbox_toggled.connect(
                self.recipe_table_proxy_model.set_building_filter
            )

        # Get quantity sold daily for the recipe output
        quantity_sold_daily = 0.0
        try:
            listing = Exchange.get_listing(recipe.output.id)
            if listing:
                quantity_sold_daily = listing.average_quantity_sold_daily
        except Exception as e:
            _logger.warning(f"Could not get quantity sold for recipe {recipe.id}: {e}")
        # Update recipe table
        self.recipe_table_model.add_row(
            recipe,
            profit_per_hour,
            consumable_preferred_combination,
            consumable_rejected_combination,
            quantity_sold_daily,
        )

        # Calculate and set the value based on current weight
        row = self.recipe_table_model.rowCount() - 1
        weight = self.toolbox.get_value_weight()
        quantity_sold_per_hour = (
            quantity_sold_daily * (recipe.timeMinutes / 60)
            if recipe.timeMinutes > 0
            else 0.0
        )
        # Apply logarithmic transformation to quantity
        # Scale by QUANTITY_SOLD_SCALING_FACTOR to bring it into comparable range with profit/hr
        quantity_sold_log = (
            math.log1p(quantity_sold_per_hour) * QUANTITY_SOLD_SCALING_FACTOR
        )
        value = (profit_per_hour * (1 - weight)) + (quantity_sold_log * weight)
        self.recipe_table_model.update_value(row, value)

    # Called from recipe worker when exchange listings are updated
    @Slot(dict)
    def handle_exchange_updated(self) -> None:
        """
        Update the profit per hour for all recipes in the table when listings are updated.

        Args:
            listings (Dict[int, Listing]): Updated listings data.
        """
        # Process recipes in batches to keep UI responsive
        batch_size = 20
        total_recipes = len(self.recipe_table_model.recipes)

        # Use a timer to process recipes in batches
        from PySide6.QtCore import QTimer

        def process_batch(start_idx):
            end_idx = min(start_idx + batch_size, total_recipes)

            # Process a batch of recipes
            for row in range(start_idx, end_idx):
                recipe = self.recipe_table_model.recipes[row]
                result = calculate_profit_and_consumables(
                    recipe,
                    self.settings.tech_level_filters[
                        GameDataManager.get_building(recipe.producedIn).specialization
                    ],
                )
                if result is None:
                    profit_per_hour = float("-inf")
                    consumable_preferred_combination = ()
                    consumable_rejected_combination = ()
                else:
                    (
                        profit_per_hour,
                        consumable_preferred_combination,
                        consumable_rejected_combination,
                    ) = result

                # Get updated quantity sold daily and convert to per-hour
                quantity_sold_daily = 0.0
                try:
                    listing = Exchange.get_listing(recipe.output.id)
                    if listing:
                        quantity_sold_daily = (
                            listing.average_quantity_sold_daily
                            * (recipe.timeMinutes / 60)
                            if recipe.timeMinutes > 0
                            else 0.0
                        )
                except Exception as e:
                    _logger.warning(
                        f"Could not get quantity sold for recipe {recipe.id}: {e}"
                    )

                self.recipe_table_model.setData(
                    self.recipe_table_model.index(row, 1),
                    profit_per_hour,
                    Qt.ItemDataRole.EditRole,
                )
                self.recipe_table_model.setData(
                    self.recipe_table_model.index(row, 2),
                    quantity_sold_daily,
                    Qt.ItemDataRole.EditRole,
                )

                # Update price delta
                listing = Exchange.get_listing(recipe.output.id)
                price_delta = (
                    (listing.current_price - listing.average_price) / 100 if listing else 0.0
                )
                self.recipe_table_model.setData(
                    self.recipe_table_model.index(row, 3),
                    price_delta,
                    Qt.ItemDataRole.EditRole,
                )

                # Update value based on current weight
                weight = self.toolbox.get_value_weight()
                # Apply logarithmic transformation to quantity
                quantity_sold_log = (
                    math.log1p(quantity_sold_daily) * QUANTITY_SOLD_SCALING_FACTOR
                )
                value = (profit_per_hour * (1 - weight)) + (quantity_sold_log * weight)
                self.recipe_table_model.update_value(row, value)

                self.recipe_table_model.update_consumables(
                    row,
                    consumable_preferred_combination,
                    consumable_rejected_combination,
                )

            # Process next batch or finish
            if end_idx < total_recipes:
                # Update status if parent window has a status bar
                parent_window = self.window()
                if hasattr(parent_window, "statusBar"):
                    parent_window.statusBar().showMessage(
                        f"Recalculating recipes: {end_idx}/{total_recipes}..."
                    )

                # Schedule next batch
                QTimer.singleShot(0, lambda: process_batch(end_idx))
            else:
                # Finished all batches
                parent_window = self.window()
                if hasattr(parent_window, "statusBar"):
                    parent_window.statusBar().showMessage(
                        "Recipe recalculation complete", 3000
                    )

        # Start processing the first batch
        process_batch(0)

    def closeEvent(self, event: QCloseEvent) -> None:
        _logger.debug("Saving settings.")
        # TODO: Put this in galaxyTycoonUi.py
        self.settings.tech_level_filters = (
            self.recipe_table_proxy_model.tech_level_filters
        )
        self.settings.tech_level_maximums = self.toolbox.get_tech_level_maximums()

        # Stop the debounce timer
        self.value_weight_debounce_timer.stop()

        # Clean up value recalculation thread
        if self.value_recalc_thread is not None:
            try:
                if self.value_recalc_thread.isRunning():
                    _logger.debug("Cleaning up ValueRecalculationWorker thread")
                    self.value_recalc_thread.requestInterruption()
                    if not self.value_recalc_thread.wait(1000):
                        _logger.warning(
                            "ValueRecalculationWorker thread did not finish, terminating"
                        )
                        self.value_recalc_thread.terminate()
                        self.value_recalc_thread.wait()
            except RuntimeError:
                # Thread was already deleted, that's fine
                pass
